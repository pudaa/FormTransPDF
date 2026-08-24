"""碰撞计算比例守卫 + 覆盖层缓存测试（需要 qapp fixture）。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget

from src.ui.pdf.cover import CoverSegment
from src.ui.pdf.text_overlay import _COVER_CACHE_LIMIT, TextOverlay


def make_seg(x, y, w, h, text="t", ox=0.0, oy=0.0) -> CoverSegment:
    s = CoverSegment(page=0, text=text, pdf_x=x, pdf_y=y,
                     pdf_width=w, pdf_height=h, font_size=10.0)
    s.offset_x = ox
    s.offset_y = oy
    s.content_rect = QRectF(x, y, w, h)
    return s


def _overlay() -> TextOverlay:
    return TextOverlay(QWidget())


# ── next_y / next_x 比例守卫 ────────────────────────────────

def test_next_y_sliver_not_constrain(qapp):
    overlay = _overlay()
    a = make_seg(100, 100, 200, 80)
    sliver = make_seg(297, 200, 200, 50)  # x 重叠仅 3px
    nxt = overlay._compute_next_y([a, sliver], 800.0, QPointF(0, 0))
    assert nxt[id(a)] == 800.0


def test_next_y_real_overlap_constrains(qapp):
    overlay = _overlay()
    a = make_seg(100, 100, 200, 80)
    c = make_seg(150, 200, 200, 50)  # 重叠 150 ≥ 25%×200
    nxt = overlay._compute_next_y([a, c], 800.0, QPointF(0, 0))
    assert abs(nxt[id(a)] - 196.0) < 1e-6


def test_next_x_diagonal_sliver_not_constrain(qapp):
    overlay = _overlay()
    d = make_seg(100, 100, 200, 80)
    diag = make_seg(298, 176, 100, 4)  # y 接触仅 2px 且极矮
    nxt_x = overlay._compute_next_x([d, diag], 612.0, QPointF(0, 0))
    assert nxt_x[id(d)] == 612.0 - 4.0


def test_next_x_real_overlap_constrains(qapp):
    overlay = _overlay()
    d = make_seg(100, 100, 200, 80)
    e = make_seg(320, 120, 200, 60)
    nxt_x = overlay._compute_next_x([d, e], 612.0, QPointF(0, 0))
    assert abs(nxt_x[id(d)] - 316.0) < 1e-6


# ── 页级缓存失效 + LRU ──────────────────────────────────────

def test_bump_cache_page_scoped(qapp):
    overlay = _overlay()
    k1 = (1, 100, overlay._global_version, 0)
    k2 = (2, 100, overlay._global_version, 0)
    overlay._cover_cache[k1] = QPixmap(4, 4)
    overlay._cover_cache[k2] = QPixmap(4, 4)

    overlay._bump_cache({1})
    assert k1 not in overlay._cover_cache
    assert k2 in overlay._cover_cache  # 邻页缓存保留


def test_lru_eviction(qapp):
    overlay = _overlay()
    for i in range(_COVER_CACHE_LIMIT + 5):
        key = (100 + i, 100, overlay._global_version, 0)
        overlay._cover_cache[key] = QPixmap(4, 4)
        while len(overlay._cover_cache) > _COVER_CACHE_LIMIT:
            overlay._cover_cache.popitem(last=False)
    assert len(overlay._cover_cache) == _COVER_CACHE_LIMIT
    assert (100, 100, overlay._global_version, 0) not in overlay._cover_cache


# ── 浮动段绘制顺序（浮动段最后画，浮在最上层）───────────────

def test_float_painted_last(qapp):
    """排序键验证：浮动段排序值更大 → 排在最后绘制。"""
    normal = make_seg(10, 10, 100, 30, text="normal")
    floating = make_seg(10, 50, 100, 30, text="float", ox=5, oy=5)
    ordered = sorted(
        [s for s in (normal, floating) if s.content_rect and not s.content_rect.isEmpty()],
        key=lambda s: (
            1 if (getattr(s, "offset_x", 0.0) != 0.0 or getattr(s, "offset_y", 0.0) != 0.0) else 0,
            s.content_rect.y(), s.content_rect.x(),
        ),
    )
    assert ordered[-1] is floating, "浮动段必须排在最后绘制"
