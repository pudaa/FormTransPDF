"""
真实论文渲染测试 — 用用户提供的 Zotero PDF 跑完整管线。

流程：
1. fitz 提取 span（与 PdfTextExtractor 相同逻辑，含 block_id）
2. build_segments → 段落级 segment
3. 用真实 PdfLayoutEngine（QPdfView + QPdfDocument，FitToWidth）计算布局与缩放
4. 前几段填入真实中文译文，其余段保持原文回退（模拟流式呈现中的"待翻译"状态）
5. TextOverlay 渲染第 0 页 → 输出 PNG + 段统计
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, QMargins
from PySide6.QtGui import QColor, QFontDatabase, QPixmap, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QApplication

sys.path.insert(0, r"D:/Codes/FormTransPDF")

import fitz

from src.ui.pdf.cover import build_segments, COVER_TRANSLATED
from src.ui.pdf.layout_engine import PdfLayoutEngine
from src.ui.pdf.text_overlay import TextOverlay

PDF = r"C:/Users/21591/Zotero/storage/NVSNY528/Greeno和Moore - 1993 - Situativity and Symbols Response to Vera and Simon.pdf"
VP_W = 1109
VP_H = 776

# 前几个段落的真实中文译文（模拟已翻译状态），其余段保持 display_text=None → 回退原文
translations = {
    0: "认知科学 17，495-499（1993）",
    1: "情境性与符号：",
    2: "对维拉与西蒙的回应",
    3: "詹姆斯·G·格里诺",
    4: "斯坦福大学与",
    5: "维拉与西蒙（1993）为我们审视认知科学中的情境性观点提供了有益且重要的基础。他们对认知科学理论的讨论，尤其是关于符号表征在认知过程中的作用，促使我们重新思考情境认知与符号计算之间的张力。本文将在梳理其核心论点的基础上，回应其对情境性观点的质疑，并阐明我们对符号与情境关系的理解。",
    6: "符号问题",
}


def extract_spans(page_idx: int, doc):
    page = doc[page_idx]
    spans = []
    for block_idx, block in enumerate(page.get_text("dict")["blocks"]):
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                bbox = span["bbox"]
                spans.append(SimpleNamespace(
                    page=page_idx, text=span["text"],
                    pdf_x=bbox[0], pdf_y=bbox[1],
                    pdf_width=bbox[2] - bbox[0],
                    pdf_height=bbox[3] - bbox[1],
                    font_size=span["size"], block_id=block_idx,
                    content_rect=None,
                ))
    return spans


def main() -> int:
    app = QApplication(sys.argv)

    # offscreen 平台不加载 Windows 系统字体，注册后再渲染（真实桌面 app 不需要此步骤）
    import glob
    for _fp in (
        glob.glob("C:/Windows/Fonts/msyh.ttc") + glob.glob("C:/Windows/Fonts/msyh*.ttc") +
        glob.glob("C:/Windows/Fonts/segoeui.ttf") + glob.glob("C:/Windows/Fonts/arial.ttf")
    ):
        QFontDatabase.addApplicationFont(_fp)

    fitz_doc = fitz.open(PDF)

    # ── 真实布局：QPdfView + PdfLayoutEngine（FitToWidth）──
    view = QPdfView()
    view.resize(VP_W, VP_H)
    qdoc = QPdfDocument()
    qdoc.load(PDF)
    view.setDocument(qdoc)
    view.setPageMode(QPdfView.PageMode.MultiPage)
    view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
    view.setPageSpacing(0)
    view.setDocumentMargins(QMargins(0, 0, 0, 0))
    engine = PdfLayoutEngine(view)
    engine.set_document(qdoc)
    layouts = engine.compute_layout(VP_W, VP_H)
    print(f"== 布局：{len(layouts)} 页，scale={layouts[0].scale:.3f} ==")
    print(f"   page0 rect=({layouts[0].rect.x():.0f},{layouts[0].rect.y():.0f}) "
          f"{layouts[0].rect.width():.0f}x{layouts[0].rect.height():.0f}")

    # ── 第 0 页：提取 + 分段 + 渲染 ──
    spans = extract_spans(0, fitz_doc)
    segs = build_segments(spans)
    print(f"== page0: {len(spans)} spans → {len(segs)} 段落段 ==")
    layout0 = layouts[0]
    for idx, seg in enumerate(segs):
        seg.content_rect = QRectF(
            layout0.rect.x() + seg.pdf_x * layout0.scale,
            layout0.rect.y() + seg.pdf_y * layout0.scale,
            seg.pdf_width * layout0.scale,
            seg.pdf_height * layout0.scale,
        )
        seg.display_text = translations.get(idx)
        print(f"  [{idx}] y={seg.content_rect.y():.0f} {seg.content_rect.width():.0f}x{seg.content_rect.height():.0f} "
              f"font_pt={seg.font_size:.1f} font_px={seg.font_size * layout0.scale:.0f}"
              f"{' 译文' if seg.display_text else ' 原文'}")
        print(f"        {seg.text[:56]}")

    class FakeLayout:
        page_num = 0
        rect = layout0.rect
        scale = layout0.scale

    overlay = TextOverlay()
    overlay.setGeometry(0, 0, VP_W, VP_H)
    overlay.set_cover({0: segs}, {0: FakeLayout()}, bump=True)
    overlay.set_cover_mode(COVER_TRANSLATED)
    pix = overlay._render_page_pixmap(0, FakeLayout(), segs)

    # 叠加页面背景
    page_img = QPixmap(VP_W, VP_H)
    page_img.fill(QColor("#F3EFE4"))
    p = QPainter(page_img)
    p.drawPixmap(int(layout0.rect.x()), int(layout0.rect.y()), pix)
    p.end()
    out = r"D:/Codes/FormTransPDF/output/real_paper_page0.png"
    page_img.save(out)
    print("saved:", out)

    # ── 全文档统计：每页段落数 / span 数 ──
    total_segs = 0
    for pi in range(len(fitz_doc)):
        sp = extract_spans(pi, fitz_doc)
        s = build_segments(sp)
        total_segs += len(s)
        if pi <= 1:
            print(f"  page{pi}: {len(sp)} spans → {len(s)} segments")
    print(f"== 全文档 segments 总数: {total_segs} ==")

    fitz_doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
