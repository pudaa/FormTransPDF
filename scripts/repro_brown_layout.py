"""Brown 论文多栏布局验证：x 重叠 next_y + 段自身范围 clip + 自适应字号。"""
import os
import sys
import glob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"D:/Codes/FormTransPDF")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
for fp in glob.glob("C:/Windows/Fonts/msyh.ttc") + glob.glob("C:/Windows/Fonts/segoeui.ttf"):
    QFontDatabase.addApplicationFont(fp)

import fitz
from types import SimpleNamespace
from src.ui.pdf.cover import build_segments
from src.ui.pdf.text_overlay import TextOverlay

PDF = r"C:/Users/21591/Zotero/storage/3VGRMIN4/Brown 等 - Situated Cognition and the Culture of Learning.pdf"
fitz_doc = fitz.open(PDF)
ov = TextOverlay()

for PAGE in (1, 0):
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
    # 模拟翻译：全宽段（标题/作者）用短译文（符合实际），正文段用长译文
    long_text = (
        "这是第 {i} 段的译文内容，用于验证多栏布局下译文换行与"
        "列内裁剪是否正确，包含足够长的中文文本以触发换行。"
    )
    short_text = lambda i: f"短译文段 {i}"  # 标题/作者用短文本
    for i, seg in enumerate(segs):
        if seg.content_rect.width() > LAYOUT_RECT.width() * 0.5:
            seg.display_text = short_text(i)
        elif i % 2 == 0:
            seg.display_text = long_text.format(i=i)
    class L:
        rect = LAYOUT_RECT
        scale = scale
    ov.set_cover({PAGE: segs}, {PAGE: L()}, bump=True)
    ov.set_cover_mode("translated")
    pix = ov._render_page_pixmap(PAGE, L(), segs)
    out = rf"D:/Codes/FormTransPDF/output/brown_page{PAGE}.png"
    pix.save(out)

    # 新版：x 重叠 next_y（替代旧版列聚类输出）
    next_y = ov._compute_next_y(segs, float(LAYOUT_RECT.height()), LAYOUT_RECT.topLeft())
    print(f"\n== page {PAGE}: {len(segs)} segs ==")
    for i, seg in enumerate(segs[:6]):
        r = seg.content_rect
        print(f"  [{i}] y={r.y()-LAYOUT_RECT.y():.0f} w={r.width():.0f}px base={seg.font_size*scale:.0f}px "
              f"next_y={next_y[id(seg)]-LAYOUT_RECT.y():.0f} {seg.text[:22]!r}")
    print("  saved:", out)

fitz_doc.close()
