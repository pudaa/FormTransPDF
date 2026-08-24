"""cover.py 段构建测试：垃圾段过滤 + 列流切分（无控件依赖）。"""

from __future__ import annotations

from src.ui.pdf.cover import (
    build_segments,
    is_junk_text,
    is_reference_entry,
    is_references_heading,
)


class FakeSpan:
    def __init__(self, text, x, y, size, block_id, w=None, h=None):
        self.page = 0
        self.text = text
        self.pdf_x = x
        self.pdf_y = y
        self.pdf_width = w if w is not None else max(10.0, len(text) * size * 0.5)
        self.pdf_height = h if h is not None else size * 1.4
        self.font_size = size
        self.block_id = block_id


# ── 垃圾段判定 ──────────────────────────────────────────────

def test_is_junk_text():
    assert is_junk_text("(5)")
    assert is_junk_text("2023.")
    assert is_junk_text("x2 + y2 = r2")
    assert is_junk_text("(a)")
    assert not is_junk_text("Figure 3: Overall architecture.")
    assert not is_junk_text(
        "The accuracy improved by 15.2% (p < 0.01) across all benchmarks."
    )


def test_reference_helpers():
    assert is_reference_entry("[1] Smith, J. (2020). Deep learning survey.")
    assert not is_reference_entry("[show] something")
    assert is_references_heading("References")
    assert is_references_heading("BIBLIOGRAPHY")
    assert not is_references_heading("Referenced works are listed below.")


def test_build_segments_filters_page_furniture():
    pw, ph = 612.0, 792.0
    spans = [
        FakeSpan("IEEE TRANSACTIONS ON PATTERN ANALYSIS", 72, 24, 8, block_id=0),
        FakeSpan("Body paragraph about transformers and attention.", 72, 300, 10, block_id=1),
        FakeSpan("arxiv-stamp", 8, 100, 7, block_id=2, w=8, h=520),
        FakeSpan("References", 72, 600, 11, block_id=3),
        FakeSpan("[1] Vaswani, A. et al. Attention is all you need. 2017.", 72, 620, 9, block_id=4),
        FakeSpan("[2] Another cited work by someone. 2018.", 72, 640, 9, block_id=5),
    ]
    segs = build_segments(spans, page_size=(pw, ph))
    assert len(segs) == 1
    assert "transformers" in segs[0].text


# ── 列流不连续切分（跨栏合并 block 兜底）────────────────────

def test_column_split():
    spans = []
    y = 100.0
    for i in range(3):
        spans.append(FakeSpan(f"Left column sentence {i} continues here.",
                              40, y, 10, block_id=0, w=250))
        spans.append(FakeSpan(f"Right column line number {i} text.",
                              310, y + 2, 10, block_id=0, w=250))
        y += 14.0
    segs = build_segments(spans)
    assert len(segs) >= 2
    for s in segs:
        assert s.pdf_width < 280, f"段横跨两栏: {s.text[:24]}"


# ── 连字符拼接回归 ──────────────────────────────────────────

def test_hyphen_join():
    spans = [
        FakeSpan("This study examines two ex-", 40, 100, 10, block_id=0, w=250),
        FakeSpan("amples of mathematics instruction here.", 40, 114, 10, block_id=0, w=250),
        FakeSpan("We compare against the state-of-", 40, 140, 10, block_id=1, w=250),
        FakeSpan("the-art baselines across benchmarks.", 40, 154, 10, block_id=1, w=250),
    ]
    segs = build_segments(spans)
    texts = [s.text for s in segs]
    assert any("examples" in t for t in texts), f"断词未拼接: {texts}"
    assert any("state-of-the-art" in t for t in texts), f"复合词连字符丢失: {texts}"
