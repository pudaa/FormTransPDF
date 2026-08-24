"""
粗糙翻译调度核心 — 纯 asyncio 实现，不依赖 Qt。

从 RoughTranslator 抽出的可测试逻辑层，职责链：

    段条目归一化 → 批合并（不跨页）→ 并发调度（请求级信号量）
    → 重试（指数退避）→ 成败/进度/页完成统计 → ScheduleResult

设计约束：
- 不 import PySide6 —— 可在无显示环境的 CI 中直接 pytest；
- 翻译函数通过构造器注入（single_fn / batch_fn / can_batch_fn），
  测试用假体替换，生产由 rough.RoughTranslator 适配层传入默认实现；
- 取消语义：调用方 cancel 掉承载 run() 的 asyncio.Task 即可，
  CancelledError 原样向上传播，统计回调不会在取消后误发。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping, Sequence

from src.core.translation.text import (
    BatchFormatError,
    supports_batch,
    translate_batch,
    translate_text,
)

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 4
# 单段/单批最大尝试次数（含首次）；重试间隔指数退避
MAX_ATTEMPTS = 3
RETRY_DELAYS = (1.0, 2.0, 4.0)
# 批量合并上限：不跨页合批（保持页完成信号粒度与上下文局部性）
BATCH_MAX_SEGMENTS = 8
BATCH_MAX_CHARS = 2000


@dataclass(frozen=True)
class SegmentItem:
    """一个翻译单元：页码 + 页内序号 + 原文 + 是否标题。"""

    page: int
    index: int
    text: str
    is_heading: bool = False


@dataclass
class ScheduleResult:
    """一次调度的最终统计。"""

    total: int = 0
    ok: int = 0
    failed: int = 0
    failed_items: list[SegmentItem] = field(default_factory=list)


SingleFn = Callable[[str, Mapping, str, str], Awaitable[str]]
BatchFn = Callable[[Sequence[str], Mapping, str, str], Awaitable[list[str]]]
CanBatchFn = Callable[[Mapping], bool]


class TranslationScheduler:
    """并发批量翻译调度器（框架无关）。"""

    def __init__(
        self,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        max_attempts: int = MAX_ATTEMPTS,
        retry_delays: Sequence[float] = RETRY_DELAYS,
        batch_max_segments: int = BATCH_MAX_SEGMENTS,
        batch_max_chars: int = BATCH_MAX_CHARS,
        single_fn: SingleFn | None = None,
        batch_fn: BatchFn | None = None,
        can_batch_fn: CanBatchFn | None = None,
    ) -> None:
        self._concurrency = max(1, concurrency)
        self._max_attempts = max(1, max_attempts)
        self._retry_delays = tuple(retry_delays) or (0.0,)
        self._batch_max_segments = batch_max_segments
        self._batch_max_chars = batch_max_chars
        # 注入点：测试传假体；生产用 text.py 默认实现
        self._single_fn: SingleFn = single_fn or self._default_single
        self._batch_fn: BatchFn = batch_fn or self._default_batch
        self._can_batch_fn: CanBatchFn = can_batch_fn or supports_batch

    # ── 默认翻译实现（转发到 text.py）─────────────────────

    @staticmethod
    async def _default_single(text, profile, lang_in, lang_out, *, is_heading=False, glossary=""):
        return await translate_text(
            text, profile, lang_in, lang_out,
            is_heading=is_heading, glossary=glossary,
        )

    @staticmethod
    async def _default_batch(texts, profile, lang_in, lang_out, *, glossary=""):
        return await translate_batch(texts, profile, lang_in, lang_out, glossary=glossary)

    # ── 批合并 ────────────────────────────────────────────

    def make_batches(self, items: list[SegmentItem]) -> list[list[SegmentItem]]:
        """按 (page, index) 升序条目切批：不跨页、受段数与字符数上限约束。"""
        batches: list[list[SegmentItem]] = []
        cur: list[SegmentItem] = []
        chars = 0
        for it in items:
            t_len = len(it.text)
            if cur and (
                len(cur) >= self._batch_max_segments
                or chars + t_len > self._batch_max_chars
                or it.page != cur[-1].page
            ):
                batches.append(cur)
                cur = []
                chars = 0
            cur.append(it)
            chars += t_len
        if cur:
            batches.append(cur)
        return batches

    # ── 主入口 ────────────────────────────────────────────

    async def run(
        self,
        items: Sequence[SegmentItem],
        profile: Mapping,
        lang_in: str,
        lang_out: str,
        *,
        on_segment_done: Callable[[SegmentItem, str], None] | None = None,
        on_page_done: Callable[[int], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ScheduleResult:
        """调度全部段；返回最终统计。

        回调均为同步函数（适配层在其中 emit Qt 信号），在事件循环线程内被调用。
        """
        result = ScheduleResult(total=len(items))
        sem = asyncio.Semaphore(self._concurrency)

        page_remaining: dict[int, int] = {}
        for it in items:
            page_remaining[it.page] = page_remaining.get(it.page, 0) + 1

        if on_progress:
            on_progress(0, result.total)
        if not items:
            return result

        def settle(item: SegmentItem, success: bool) -> None:
            if success:
                result.ok += 1
            else:
                result.failed += 1
                result.failed_items.append(item)
            rem = page_remaining.get(item.page)
            if rem is not None:
                rem -= 1
                page_remaining[item.page] = rem
                if rem == 0 and on_page_done:
                    on_page_done(item.page)
            if on_progress:
                on_progress(result.ok + result.failed, result.total)

        batches = self.make_batches(list(items))
        await asyncio.gather(
            *[
                asyncio.create_task(
                    self._process_batch(b, profile, lang_in, lang_out, sem, settle,
                                        on_segment_done)
                )
                for b in batches
            ]
        )
        return result

    # ── 批处理 ────────────────────────────────────────────

    async def _process_batch(
        self,
        batch: list[SegmentItem],
        profile: Mapping,
        lang_in: str,
        lang_out: str,
        sem: asyncio.Semaphore,
        settle: Callable[[SegmentItem, bool], None],
        on_segment_done: Callable[[SegmentItem, str], None] | None,
    ) -> None:
        """处理一个批次：批量接口优先，格式不符回退逐段。

        并发语义为「请求级」：一个批次占 1 个信号量槽位（在途请求数 ≤ 并发）。
        不按段数占槽 —— 多次 acquire 会与其它大批次互相等待造成死锁
        （首批占满全部槽位后永远凑不齐剩余槽位）。
        """
        n = len(batch)
        async with sem:
            glossary = self._glossary(profile)
            if n == 1 or not self._can_batch_fn(profile):
                for it in batch:
                    await self._translate_single(it, profile, lang_in, lang_out,
                                                 glossary, settle, on_segment_done)
                return

            texts = [it.text for it in batch]
            outs: list[str] | None = None
            fmt_error: BatchFormatError | None = None
            for attempt in range(self._max_attempts):
                try:
                    outs = await self._batch_fn(texts, profile, lang_in, lang_out,
                                                glossary=glossary)
                    break
                except asyncio.CancelledError:
                    raise
                except BatchFormatError as exc:
                    fmt_error = exc  # 网络正常但模型不守格式 → 回退逐段（不重试）
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt < self._max_attempts - 1:
                        delay = self._retry_delays[
                            min(attempt, len(self._retry_delays) - 1)
                        ]
                        logger.debug(
                            "批量翻译失败(%d 段 第%d次): %s → %.0fs 后重试",
                            n, attempt + 1, exc, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.warning("批量翻译最终失败(%d 段): %s", n, exc)
                    for it in batch:
                        settle(it, False)
                    return

            if fmt_error is not None:
                logger.debug("批量返回格式不符，回退逐段: %s", fmt_error)
                for it in batch:
                    await self._translate_single(it, profile, lang_in, lang_out,
                                                 glossary, settle, on_segment_done)
                return
            if outs is None:
                return  # 取消导致的中途退出

            for it, out in zip(batch, outs):
                if out:
                    if on_segment_done:
                        on_segment_done(it, out)
                    settle(it, True)
                else:
                    logger.warning("批量返回空译文 (page=%s idx=%s)", it.page, it.index)
                    settle(it, False)

    async def _translate_single(
        self,
        item: SegmentItem,
        profile: Mapping,
        lang_in: str,
        lang_out: str,
        glossary: str,
        settle: Callable[[SegmentItem, bool], None],
        on_segment_done: Callable[[SegmentItem, str], None] | None,
    ) -> None:
        """逐段翻译（原生通道 / 批量回退）：失败指数退避重试。"""
        for attempt in range(self._max_attempts):
            try:
                translated = await self._single_fn(
                    item.text, profile, lang_in, lang_out,
                    is_heading=item.is_heading, glossary=glossary,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单块失败不中断整篇
                translated = None
                reason = str(exc)
            else:
                reason = ""
            if translated:
                if on_segment_done:
                    on_segment_done(item, translated)
                settle(item, True)
                return
            if attempt < self._max_attempts - 1:
                delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
                logger.debug(
                    "粗糙翻译块失败 (page=%s idx=%s 第%d次): %s → %.0fs 后重试",
                    item.page, item.index, attempt + 1, reason or "空结果", delay,
                )
                await asyncio.sleep(delay)
                continue
            logger.warning(
                "粗糙翻译块最终失败 (page=%s idx=%s): %s",
                item.page, item.index, reason or "翻译服务返回空结果",
            )
            settle(item, False)

    @staticmethod
    def _glossary(profile: Mapping) -> str:
        try:
            return str(profile.get("glossary", "") or "")
        except AttributeError:
            return str(getattr(profile, "glossary", "") or "")
