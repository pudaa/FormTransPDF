"""TranslationScheduler 单元测试 — 纯 asyncio，无 Qt。"""

from __future__ import annotations

import asyncio

import pytest

from src.core.translation.scheduler import (
    BATCH_MAX_SEGMENTS,
    SegmentItem,
    TranslationScheduler,
)
from src.core.translation.text import BatchFormatError

PROFILE = {"translator": "openai"}


def make_items(n: int, page: int = 0) -> list[SegmentItem]:
    return [SegmentItem(page=page, index=i, text=f"src{i}") for i in range(n)]


def run(coro):
    return asyncio.run(coro)


# ── 批合并 ──────────────────────────────────────────────────

def test_make_batches_page_split():
    sched = TranslationScheduler()
    items = [SegmentItem(page=0, index=i, text="x") for i in range(3)] + \
            [SegmentItem(page=1, index=i, text="x") for i in range(3)]
    batches = sched.make_batches(items)
    assert len(batches) == 2
    assert {b[0].page for b in batches} == {0, 1}


def test_make_batches_segment_limit():
    sched = TranslationScheduler()
    batches = sched.make_batches(make_items(BATCH_MAX_SEGMENTS * 2 + 1))
    assert [len(b) for b in batches] == [BATCH_MAX_SEGMENTS, BATCH_MAX_SEGMENTS, 1]


def test_make_batches_char_limit():
    sched = TranslationScheduler(batch_max_segments=100, batch_max_chars=50)
    items = [SegmentItem(page=0, index=i, text="a" * 30) for i in range(4)]
    batches = sched.make_batches(items)
    # 30+31 > 50 → 每批只能装 1 段
    assert len(batches) == 4
    assert all(len(b) == 1 for b in batches)


# ── 批量成功路径 ────────────────────────────────────────────

def test_batch_success_flow():
    batch_calls: list[list[str]] = []

    async def batch_fn(texts, profile, li, lo, *, glossary=""):
        batch_calls.append(list(texts))
        return ["译" + t for t in texts]

    sched = TranslationScheduler(single_fn=None, batch_fn=batch_fn)
    done: list[tuple[int, int, str]] = []
    pages: list[int] = []
    progress: list[tuple[int, int]] = []

    result = run(sched.run(
        make_items(10), PROFILE, "en", "zh",
        on_segment_done=lambda it, t: done.append((it.page, it.index, t)),
        on_page_done=lambda p: pages.append(p),
        on_progress=lambda d, t: progress.append((d, t)),
    ))

    assert len(batch_calls) == 2  # 8+2
    assert sorted(done) == [(0, i, f"译src{i}") for i in range(10)]
    assert pages == [0]
    assert (result.ok, result.failed, result.total) == (10, 0, 10)
    assert progress[0] == (0, 10) and progress[-1] == (10, 10)


# ── 格式回退 ────────────────────────────────────────────────

def test_batch_format_fallback():
    single_calls: list[str] = []

    async def bad_batch(texts, profile, li, lo, *, glossary=""):
        raise BatchFormatError("bad")

    async def single_fn(text, profile, li, lo, *, is_heading=False, glossary=""):
        single_calls.append(text)
        return "单" + text

    sched = TranslationScheduler(single_fn=single_fn, batch_fn=bad_batch)
    done: list[str] = []
    result = run(sched.run(
        make_items(3), PROFILE, "en", "zh",
        on_segment_done=lambda it, t: done.append(t),
    ))

    assert done == ["单src0", "单src1", "单src2"]
    assert single_calls == ["src0", "src1", "src2"]
    assert (result.ok, result.failed) == (3, 0)


# ── 重试 ────────────────────────────────────────────────────

def test_retry_then_success():
    attempts = {"n": 0}

    async def flaky(text, profile, li, lo, *, is_heading=False, glossary=""):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("HTTP 429")
        return "你好"

    sched = TranslationScheduler(
        single_fn=flaky, can_batch_fn=lambda p: False,
        retry_delays=(0.001, 0.001),
    )
    done: list[str] = []
    result = run(sched.run(
        make_items(1), PROFILE, "en", "zh",
        on_segment_done=lambda it, t: done.append(t),
    ))
    assert done == ["你好"] and attempts["n"] == 3
    assert (result.ok, result.failed) == (1, 0)


def test_permanent_failure_counts():
    async def always_fail(text, profile, li, lo, *, is_heading=False, glossary=""):
        raise RuntimeError("boom")

    sched = TranslationScheduler(
        single_fn=always_fail, can_batch_fn=lambda p: False,
        retry_delays=(0.001,),
    )
    result = run(sched.run(make_items(2), PROFILE, "en", "zh"))
    assert (result.ok, result.failed) == (0, 2)
    assert len(result.failed_items) == 2


# ── 原生通道（不支持批量）────────────────────────────────────

def test_native_single_path():
    batch_called = {"n": 0}

    async def spy_batch(texts, profile, li, lo, *, glossary=""):
        batch_called["n"] += 1
        return list(texts)

    async def single_fn(text, profile, li, lo, *, is_heading=False, glossary=""):
        return "单" + text

    sched = TranslationScheduler(
        single_fn=single_fn, batch_fn=spy_batch,
        can_batch_fn=lambda p: False,
    )
    result = run(sched.run(make_items(2), {"translator": "bing"}, "en", "zh"))
    assert batch_called["n"] == 0
    assert result.ok == 2


# ── 多页 page_done / 空输入 ─────────────────────────────────

def test_multi_page_done_once_each():
    async def batch_fn(texts, profile, li, lo, *, glossary=""):
        return ["t" for _ in texts]

    sched = TranslationScheduler(batch_fn=batch_fn)
    pages: list[int] = []
    items = [SegmentItem(page=p, index=i, text=f"s{p}-{i}")
             for p in range(3) for i in range(5)]
    run(sched.run(items, PROFILE, "en", "zh", on_page_done=pages.append))
    assert sorted(pages) == [0, 1, 2]


def test_empty_items():
    sched = TranslationScheduler()
    result = run(sched.run([], PROFILE, "en", "zh"))
    assert (result.total, result.ok, result.failed) == (0, 0, 0)


# ── 取消语义 ────────────────────────────────────────────────

def test_cancel_propagates():
    started = asyncio.Event()

    async def slow_single(text, profile, li, lo, *, is_heading=False, glossary=""):
        started.set()
        await asyncio.sleep(10)
        return "never"

    sched = TranslationScheduler(
        single_fn=slow_single, can_batch_fn=lambda p: False,
        retry_delays=(0.001,),
    )

    async def scenario():
        task = asyncio.create_task(sched.run(make_items(1), PROFILE, "en", "zh"))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())


# ── 术语表透传 ──────────────────────────────────────────────

def test_glossary_passthrough():
    seen: dict[str, str] = {}

    async def single_fn(text, profile, li, lo, *, is_heading=False, glossary=""):
        seen["glossary"] = glossary
        seen["is_heading"] = is_heading
        return "ok"

    sched = TranslationScheduler(single_fn=single_fn, can_batch_fn=lambda p: False)
    item = SegmentItem(page=0, index=0, text="Introduction", is_heading=True)
    run(sched.run([item], {"translator": "openai", "glossary": "introduction=引言"},
                  "en", "zh"))
    assert seen == {"glossary": "introduction=引言", "is_heading": True}
