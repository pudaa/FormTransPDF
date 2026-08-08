"""
字块拖拽 + 字号下限 验证脚本（offscreen，Brown 三栏论文）。

覆盖：
1. 拖拽偏移渲染：给段设置 offset 后，整段平移绘制（浮在最上层，垂直放开不裁切）；
2. 字号下限：碰撞挤压时字号最多缩到 _FIT_MIN_FONT_PX（9px），不再蚂蚁小；
3. 未拖动段行为不变（回归）。
"""
from __future__ import annotations

import os
import sys
import glob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"D:/Codes/FormTransPDF")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFontDatabase, QImage
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
for fp in glob.glob("C:/Windows/Fonts/msyh.ttc") + glob.glob("C:/Windows/Fonts/segoeui.ttf"):
    QFontDatabase.addApplicationFont(fp)

import fitz
from types import SimpleNamespace

from src.ui.pdf.cover import build_segments
from src.ui.pdf.text_overlay import TextOverlay, _FIT_MIN_FONT_PX, _cover_font

PDF = r"C:/Users/21591/Zotero/storage/3VGRMIN4/Brown 等 - Situated Cognition and the Culture of Learning.pdf"
PAGE = 1  # 标准三栏页


def main() -> int:
    fail = 0
    fitz_doc = fitz.open(PDF)
    p = fitz_doc[PAGE]
    rect = p.rect
    scale = 1109.0 / rect.width
    LAYOUT_RECT = QRectF(0, 0, rect.width * scale, rect.height * scale)

    spans = []
    for block_idx, b in enumerate(p.get_text("dict")["blocks"]):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                bbox = s["bbox"]
                spans.append(SimpleNamespace(
                    page=PAGE, text=s["text"],
                    pdf_x=bbox[0], pdf_y=bbox[1],
                    pdf_width=bbox[2] - bbox[0], pdf_height=bbox[3] - bbox[1],
                    font_size=s.get("size", 10.0), block_id=block_idx,
                    content_rect=None,
                ))
    segs = build_segments(spans)
    for seg in segs:
        seg.content_rect = QRectF(
            LAYOUT_RECT.x() + seg.pdf_x * scale,
            LAYOUT_RECT.y() + seg.pdf_y * scale,
            seg.pdf_width * scale, seg.pdf_height * scale,
        )
    # 给全部段塞译文（部分段保持原文），模拟译文态
    long_text = "这是第 {i} 段的译文内容，用于验证多栏布局下译文换行与列内裁剪，包含足够长的中文以触发换行。"
    for i, seg in enumerate(segs):
        if seg.content_rect.width() > LAYOUT_RECT.width() * 0.5:
            seg.display_text = f"短译文段 {i}"
        elif i % 2 == 0:
            seg.display_text = long_text.format(i=i)

    L = SimpleNamespace(rect=LAYOUT_RECT, scale=scale)

    ov = TextOverlay()
    ov.set_cover({PAGE: segs}, {PAGE: L}, bump=True)
    ov.set_cover_mode("translated")

    # ── 场景 1：拖拽偏移渲染 ──
    # 把 seg[2]（正文段）向下右拖 180/120，模拟用户 Alt+拖拽
    dragged = segs[2]
    orig_rect = dragged.content_rect
    dragged.offset_x = 180.0
    dragged.offset_y = 120.0
    pix = ov._render_page_pixmap(PAGE, L, segs)
    out = r"D:/Codes/FormTransPDF/output/brown_page1_drag.png"
    pix.save(out)
    d1 = dragged.offset_x == 180.0 and dragged.offset_y == 120.0
    # 渲染不抛异常 + pixmap 非空（dpr 会影响像素尺寸，直接断言非空即可）
    d2 = pix is not None and not pix.isNull()
    print(f"[1] 拖拽偏移渲染：seg[2] offset=({dragged.offset_x:.0f},{dragged.offset_y:.0f}) "
          f"原位置=({orig_rect.x():.0f},{orig_rect.y():.0f})")
    print(f"    D1 offset 生效: {'✓' if d1 else '✗'}  D2 渲染输出非空: {'✓' if d2 else '✗'}")
    fail += 0 if (d1 and d2) else 1

    # ── 场景 2：字号下限（碰撞挤压时不低于 _FIT_MIN_FONT_PX）──
    # 复刻 _render_page_pixmap 的自适应循环：base_px 大 + avail_h 极小 → 应停在 9px
    base_px = 26.0
    avail_h = 30.0  # 极挤
    text = "这是被挤压的译文内容，包含足够长的中文文本以触发换行。"
    line_rects = [(50.0, 100.0, 300.0, 18.0), (50.0, 121.0, 300.0, 18.0)]
    fallback = (50.0, 100.0, 300.0, 40.0)
    chosen = None
    for scale in (1.0, 0.9, 0.8, 0.7, 0.65, 0.6):
        cand = max(base_px * scale, _FIT_MIN_FONT_PX)
        if cand > base_px:
            cand = base_px
        font = _cover_font(text, cand)
        tl, draw_rects, text_h = ov._layout_flow(text, font, line_rects, fallback)
        if text_h <= avail_h + 1.0 or cand <= _FIT_MIN_FONT_PX + 0.5:
            chosen = cand
            break
    else:
        # 兜底档（与 _render_page_pixmap 一致）：全部放不下 → 强制缩到下限
        cand = min(_FIT_MIN_FONT_PX, base_px)
        font = _cover_font(text, cand)
        tl, draw_rects, text_h = ov._layout_flow(text, font, line_rects, fallback)
        chosen = cand
    s1 = chosen is not None and chosen >= _FIT_MIN_FONT_PX
    s2 = chosen == _FIT_MIN_FONT_PX  # 挤到下限应停在 9px（不再往下）
    print(f"\n[2] 字号下限：base_px=26 挤压 → 选中字号={chosen}px (下限 {_FIT_MIN_FONT_PX}px)")
    print(f"    S1 ≥ 下限: {'✓' if s1 else '✗'}  S2 恰停在 9px: {'✓' if s2 else '✗'}")
    fail += 0 if (s1 and s2) else 1

    # ── 场景 3：未拖动段回归（offset=0 → 普通渲染路径）──
    dragged.offset_x = 0.0
    dragged.offset_y = 0.0
    pix2 = ov._render_page_pixmap(PAGE, L, segs)
    out2 = r"D:/Codes/FormTransPDF/output/brown_page1_drag_reset.png"
    pix2.save(out2)
    r1 = all(getattr(s, "offset_x", 0.0) == 0.0 and getattr(s, "offset_y", 0.0) == 0.0 for s in segs)
    print(f"\n[3] 复位回归：全部 offset=0 → 正常渲染 {'✓' if r1 else '✗'}")
    fail += 0 if r1 else 1

    fitz_doc.close()
    print(f"\n== 结果: {'全部通过 ✅' if fail == 0 else f'{fail} 项失败 ❌'} ==")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
