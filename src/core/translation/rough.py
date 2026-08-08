"""
粗糙翻译 — 复用即时翻译通道（translate_text）对全文行级 segment 做并发批量翻译。

特性：
- 按页顺序流式处理：先到先显示（页内并发受限，页面间串行 → 页码顺序呈现）
- asyncio.Semaphore 限流，避免瞬时打爆服务端（默认 4 并发）
- 结果不落盘：由 viewer 存内存 dict[(page, index)] = 译文
- doc_id 版本控制：文档切换 / 取消时，在途信号自动作废

信号：
    segment_done(doc_id, page, index, text)   单块译文就绪（viewer 更新覆盖层）
    page_done(doc_id, page)                   整页完成
    finished(doc_id)                          全部完成
    failed(doc_id, message)                   致命错误（单个块失败不回退，保留原文）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Mapping

from PySide6.QtCore import QObject, Signal

from src.core.translation.text import translate_text

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 4


class RoughTranslator(QObject):
    """并发批量翻译器 — 粗糙翻译的执行单元。"""

    segment_done = Signal(int, int, int, str)   # doc_id, page, index, text
    page_done = Signal(int, int)                # doc_id, page
    finished = Signal(int)                      # doc_id
    failed = Signal(int, str)                   # doc_id, message

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._doc_id = -1
        self._task: asyncio.Task | None = None

    @property
    def active_doc_id(self) -> int:
        return self._doc_id

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(
        self,
        doc_id: int,
        segments: list[tuple[int, int, str]],
        profile: Mapping[str, str],
        lang_in: str,
        lang_out: str,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        """启动粗糙翻译。

        :param segments: [(page, index, text), ...]，page 升序（页内任意序）
        :param profile:  与即时翻译一致的翻译配置（dict 或 TextTranslationProfile）
        """
        self.cancel()
        self._doc_id = doc_id
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("粗糙翻译：无运行中的事件循环，任务未启动")
            return
        self._task = loop.create_task(
            self._run(segments, profile, lang_in, lang_out, concurrency)
        )

    def cancel(self) -> None:
        """取消当前任务，并使所有在途信号作废。"""
        self._doc_id = -1
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    # ── 内部实现 ──────────────────────────────────────────

    async def _run(
        self,
        segments: list[tuple[int, int, str]],
        profile: Mapping[str, str],
        lang_in: str,
        lang_out: str,
        concurrency: int,
    ) -> None:
        doc_id = self._doc_id
        sem = asyncio.Semaphore(concurrency)

        pages: dict[int, list[tuple[int, str]]] = {}
        order: list[int] = []
        for page, index, text in segments:
            if page not in pages:
                pages[page] = []
                order.append(page)
            pages[page].append((index, text))

        for page in order:
            if doc_id != self._doc_id:
                return
            await asyncio.gather(
                *[
                    self._translate_one(doc_id, page, idx, text, profile, lang_in, lang_out, sem)
                    for idx, text in pages[page]
                ]
            )
            if doc_id == self._doc_id:
                self.page_done.emit(doc_id, page)

        if doc_id == self._doc_id:
            logger.info("粗糙翻译完成: %d 页", len(order))
            self.finished.emit(doc_id)

    async def _translate_one(
        self,
        doc_id: int,
        page: int,
        index: int,
        text: str,
        profile: Mapping[str, str],
        lang_in: str,
        lang_out: str,
        sem: asyncio.Semaphore,
    ) -> None:
        async with sem:
            if doc_id != self._doc_id:
                return
            try:
                translated = await translate_text(text, profile, lang_in, lang_out)
            except Exception as exc:  # noqa: BLE001 - 单块失败不中断整篇
                logger.debug("粗糙翻译块失败 (page=%s idx=%s): %s", page, index, exc)
                return  # 保留原文（viewer 回退）
            if doc_id == self._doc_id and translated:
                self.segment_done.emit(doc_id, page, index, translated)
