"""
粗糙翻译 — Qt 信号适配层。

调度/重试/批合并/统计等纯逻辑全部在 src/core/translation/scheduler.py
（无 Qt 依赖，可直接 pytest）；本模块只做三件事：

1. 把 viewer 传入的段条目归一化为 SegmentItem；
2. 在 qasync 事件循环上承载 scheduler.run() 任务（doc_id 版本控制 + 取消）；
3. 把调度回调映射为 Qt 信号（segment_done / page_done / progress / stats）。

信号：
    segment_done(doc_id, page, index, text)   单块译文就绪（viewer 更新覆盖层）
    page_done(doc_id, page)                   整页完成
    progress(doc_id, done, total)             进度（done 含成功+最终失败）
    stats(doc_id, ok, failed)                 结束统计（在 finished 之前发射）
    finished(doc_id)                          全部完成
    failed(doc_id, message)                   致命错误（保留：预留致命故障通道）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Mapping

from PySide6.QtCore import QObject, Signal

from src.core.translation.scheduler import (
    DEFAULT_CONCURRENCY,
    SegmentItem,
    TranslationScheduler,
)

logger = logging.getLogger(__name__)


class RoughTranslator(QObject):
    """并发批量翻译器 — 粗糙翻译的执行单元（scheduler 的 Qt 适配层）。"""

    segment_done = Signal(int, int, int, str)   # doc_id, page, index, text
    page_done = Signal(int, int)                # doc_id, page
    progress = Signal(int, int, int)            # doc_id, done, total
    stats = Signal(int, int, int)               # doc_id, ok, failed
    finished = Signal(int)                      # doc_id
    failed = Signal(int, str)                   # doc_id, message

    def __init__(self, parent: QObject | None = None,
                 scheduler: TranslationScheduler | None = None) -> None:
        super().__init__(parent)
        self._doc_id = -1
        self._task: asyncio.Task | None = None
        self._sched = scheduler or TranslationScheduler()

    @property
    def active_doc_id(self) -> int:
        return self._doc_id

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(
        self,
        doc_id: int,
        segments: list,
        profile: Mapping[str, str],
        lang_in: str,
        lang_out: str,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        """启动粗糙翻译。

        :param segments: [(page, index, text), ...] 或 [(page, index, text, is_heading), ...]
        :param profile:  与即时翻译一致的翻译配置（dict 或 TextTranslationProfile）
        """
        self.cancel()
        self._doc_id = doc_id
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("粗糙翻译：无运行中的事件循环，任务未启动")
            return
        items = [
            SegmentItem(
                page=it[0], index=it[1], text=it[2],
                is_heading=bool(it[3]) if len(it) > 3 else False,
            )
            for it in segments
        ]
        # concurrency 每次启动时生效（测试可注入自定义 scheduler）
        self._sched._concurrency = max(1, concurrency)
        self._task = loop.create_task(
            self._run(doc_id, items, profile, lang_in, lang_out)
        )

    def cancel(self) -> None:
        """取消当前任务，并使所有在途信号作废。"""
        self._doc_id = -1
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    # ── 内部：scheduler 回调 → Qt 信号 ────────────────────

    async def _run(
        self,
        doc_id: int,
        items: list[SegmentItem],
        profile: Mapping[str, str],
        lang_in: str,
        lang_out: str,
    ) -> None:
        def on_segment(item: SegmentItem, text: str) -> None:
            self.segment_done.emit(doc_id, item.page, item.index, text)

        def on_page(page: int) -> None:
            self.page_done.emit(doc_id, page)

        def on_progress(done: int, total: int) -> None:
            self.progress.emit(doc_id, done, total)

        result = await self._sched.run(
            items, profile, lang_in, lang_out,
            on_segment_done=on_segment,
            on_page_done=on_page,
            on_progress=on_progress,
        )

        if doc_id == self._doc_id:
            logger.info(
                "粗糙翻译完成: %d 段，成功 %d，失败 %d",
                result.total, result.ok, result.failed,
            )
            # stats 先于 finished：UI 先收到统计刷新状态文案，再收到完成同步按钮态
            self.stats.emit(doc_id, result.ok, result.failed)
            self.finished.emit(doc_id)
