"""
复现脚本 — 用真实 fitz + 真实渲染路径复现"翻译文本重叠"问题。

流程（与真实 app 完全一致）：
1. fitz 创建测试 PDF（英文正文 ~10pt，一个标题 + 若干段落）
2. 按 PdfTextExtractor 的逻辑提取 span（含 block_id）
3. build_segments → 段落级 CoverSegment（PDF 坐标）
4. 按真实布局参数（vp=1109, scale≈0.906，页面居中）计算 content_rect
5. 硬编码中文译文，走 TextOverlay._render_page_pixmap 渲染
6. 输出每段的 content_rect / 字号 / 译文，并保存页面渲染 PNG
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

sys.path.insert(0, r"D:/Codes/FormTransPDF")

import fitz

from src.ui.pdf.cover import build_segments, COVER_TRANSLATED, CoverSegment
from src.ui.pdf.text_overlay import TextOverlay

# 与用户日志一致的布局参数
VP_W = 1109
SCALE = 0.906

translations = {
    0: "注意力即所需：Transformer 架构模型综述",
    1: "本研究考察了数学教学中展现出特定特征的两个教学实例，并据此探讨有效教学实践的一般规律。本文基于课堂观察数据，对教师行为与学生学习效果之间的关联进行了系统分析。",
    2: "自注意力机制对所有输入位置计算加权求和，使模型能够在单层内捕捉长距离依赖关系，从而显著提升了并行训练效率与翻译质量。",
    3: "我们与三种基准数据集上的最新基线方法进行了对比实验，结果表明所提出的架构在收敛速度与最终效果上均优于既有方案。",
}


def make_pdf(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 60
    # 标题（单行）
    page.insert_text((60, y), "Attention Is All You Need: A Survey of Transformer Models",
                    fontsize=14, fontname="times-roman")
    y += 14 * 1.4 + 16
    # 三个多行段落（用 insert_textbox 自动折行 → 产生多 span/多 line）
    paragraphs = [
        "This study examines two examples of mathematics instruction that "
        "exhibit certain characteristics of effective teaching practice. "
        "Drawing on classroom observation data, we analyze the relationship "
        "between teacher behavior and student learning outcomes.",
        "The self-attention mechanism computes a weighted sum over all input "
        "positions, allowing the model to capture long-range dependencies in a "
        "single layer.",
        "We compare against the state-of-the-art baselines across three "
        "benchmarks, showing faster convergence and better final performance.",
    ]
    for p in paragraphs:
        rect = fitz.Rect(60, y, 60 + 490, y + 80)  # 490pt 宽
        page.insert_textbox(rect, p, fontsize=10, fontname="times-roman")
        y += 60
    doc.save(path)
    doc.close()


def _insert(page, y: float, text: str, size: float) -> float:
    return size * 1.4


def extract_spans(path: str):
    """复刻 PdfTextExtractor._ExtractWorker 的 span 提取逻辑。"""
    doc = fitz.open(path)
    page = doc[0]
    spans = []
    for block_idx, block in enumerate(page.get_text("dict")["blocks"]):
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                bbox = span["bbox"]
                from types import SimpleNamespace
                spans.append(SimpleNamespace(
                    page=0, text=span["text"],
                    pdf_x=bbox[0], pdf_y=bbox[1],
                    pdf_width=bbox[2] - bbox[0],
                    pdf_height=bbox[3] - bbox[1],
                    font_size=span["size"], block_id=block_idx,
                    content_rect=None,
                ))
    doc.close()
    return spans


def main() -> int:
    app = QApplication(sys.argv)

    pdf = r"D:/Codes/FormTransPDF/output/repro_source.pdf"
    make_pdf(pdf)
    spans = extract_spans(pdf)
    print(f"== 提取到 {len(spans)} 个 span ==")

    segs = build_segments(spans)
    print(f"== build_segments → {len(segs)} 个段落 ==")

    # 真实布局：vp=1109, scale=0.906，页面居中
    page_w = round(612 * SCALE)
    page_h = round(792 * SCALE)
    x0 = (max(page_w, VP_W) - page_w) / 2
    layout_rect = QRectF(x0, 0, page_w, page_h)

    for idx, seg in enumerate(segs):
        seg.content_rect = QRectF(
            layout_rect.x() + seg.pdf_x * SCALE,
            layout_rect.y() + seg.pdf_y * SCALE,
            seg.pdf_width * SCALE,
            seg.pdf_height * SCALE,
        )
        seg.display_text = translations.get(idx)
        print(
            f"  [{idx}] rect=({seg.content_rect.x():.0f},{seg.content_rect.y():.0f}) "
            f"{seg.content_rect.width():.0f}x{seg.content_rect.height():.0f} "
            f"font_pt={seg.font_size:.1f} font_px={seg.font_size * SCALE:.1f}"
        )
        print(f"        text: {seg.text[:60]}")

    class FakeLayout:
        page_num = 0
        rect = layout_rect
        scale = SCALE

    overlay = TextOverlay()
    overlay.setGeometry(0, 0, VP_W, 800)
    overlay.set_cover({0: segs}, {0: FakeLayout()}, bump=True)
    overlay.set_cover_mode(COVER_TRANSLATED)

    # 直接取每页 pixmap（真实渲染路径）
    pix = overlay._render_page_pixmap(0, FakeLayout(), segs)

    # 叠加浅色页面背景便于查看
    page_img = QPixmap(VP_W, 800)
    page_img.fill(QColor("#F3EFE4"))
    p = QPainter(page_img)
    p.drawPixmap(int(layout_rect.x()), int(layout_rect.y()), pix)
    p.end()
    out = r"D:/Codes/FormTransPDF/output/repro_rough_render.png"
    page_img.save(out)
    print("saved:", out)
    return 0


if __name__ == "__main__":
    from PySide6.QtGui import QPainter
    raise SystemExit(main())
