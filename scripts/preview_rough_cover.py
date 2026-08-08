"""
覆盖层渲染 + 段落聚合预览 — 无需 PDF / 无网络。

验证点：
1. build_segments：span → 行 → 段（block 聚合）→ 行末连字符断词拼接
   （"two ex-" + "amples of ..." → "two examples of ..."）
2. 覆盖层渲染：白底黑字、长译文换行、译文缺失段回退原文、CJK 字体回退
3. 流式呈现逻辑（display_text=None → 画原文）
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

sys.path.insert(0, r"D:/Codes/FormTransPDF")

from src.ui.pdf.cover import CoverSegment, COVER_TRANSLATED, build_segments
from src.ui.pdf.text_overlay import TextOverlay

SCALE = 1.6
PAGE_W = 820
PAGE_H = 1160


class FakeLayout:
    def __init__(self, page_num, rect, scale):
        self.page_num = page_num
        self.rect = rect
        self.scale = scale


class FakeSpan:
    """与 TextSpan 同构的假 span（含 block_id）。"""

    def __init__(self, text, x, y, size, block_id, w=None):
        self.page = 0
        self.text = text
        self.pdf_x = x
        self.pdf_y = y
        self.pdf_width = w if w is not None else max(10.0, len(text) * size * 0.5)
        self.pdf_height = size * 1.4
        self.font_size = size
        self.block_id = block_id
        self.content_rect = None


def make_spans() -> list[FakeSpan]:
    spans = []
    x0 = 40
    w = 500

    def line(block, y, text, size):
        spans.append(FakeSpan(text, x0, y, size, block, w=w))
        return y + size * 1.5

    y = 30
    y = line(0, y, "Attention Is All You Need: A Survey of Transformer Models", 15)
    y = line(1, y, "John Doe, Jane Smith", 11)
    y = line(2, y, "Department of Computer Science, Example University", 9)
    y = y + 8

    # block 3：摘要 —— 包含用户提到的行末连字符断词场景
    # 原文视觉上两行： "two ex-" / "amples of mathematics instruction that exhibit certain"
    y = line(3, y, "This study examines two ex-", 10)
    y = line(3, y, "amples of mathematics instruction that exhibit certain", 10)
    y = line(3, y, "characteristics of effective teaching practice.", 10)
    y = y + 8

    # block 4：正文（两行）
    y = line(4, y, "The self-attention mechanism computes a weighted sum", 10)
    y = line(4, y, "over all input positions in a single layer.", 10)
    y = y + 8

    # block 5：复合词连字符延续 state-of-the-art
    y = line(5, y, "We compare against the state-of-", 10)
    y = line(5, y, "the-art baselines across three benchmarks.", 10)
    y = y + 8

    # block 6：未翻译的英文句（译文缺失回退原文）
    y = line(6, y, "This sentence has not been translated yet.", 10)
    return spans


def main() -> int:
    app = QApplication(sys.argv)

    spans = make_spans()
    segs = build_segments(spans)

    print("== build_segments 结果（段落级）==")
    for i, seg in enumerate(segs):
        print(f"  [{i}] ({seg.pdf_width:.0f}x{seg.pdf_height:.0f}pt, font {seg.font_size:.0f})")
        print(f"      text: {seg.text[:70]}")

    # 模拟译文（大部分有译文；block 6 无 → 回退原文）
    translations = {
        0: "注意力即所需：Transformer 架构模型综述",
        1: "张三，李四",
        2: "示例大学计算机科学系",
        3: "本研究考察了数学教学中展现出特定特征的两个教学实例，",
        4: "自注意力机制对所有输入位置计算加权求和，并在单层内完成。",
        5: "我们与三种基准上的最新（state-of-the-art）基线进行了比较。",
        # 6 无译文 → 显示原文
    }
    for idx, seg in enumerate(segs):
        if idx in translations:
            seg.display_text = translations[idx]
        # content_rect 由 viewer 计算，这里模拟：pdf 坐标 * SCALE
        seg.content_rect = QRectF(
            seg.pdf_x * SCALE, seg.pdf_y * SCALE,
            seg.pdf_width * SCALE, seg.pdf_height * SCALE,
        )

    # ── 模拟 PDF 背景页：浅色底 + 浅灰"原文带" ──
    page_img = QPixmap(PAGE_W, PAGE_H)
    page_img.fill(QColor("#F3EFE4"))
    p = QPainter(page_img)
    for seg in segs:
        r = seg.content_rect
        line_h = max(2.0, seg.font_size * SCALE * 0.55)
        y = r.y() + 2
        x = r.x() + 2
        end = r.right() - 4
        for i in range(max(1, int(r.height() / line_h))):
            width = max(20.0, (end - x) * (0.92 - 0.18 * (i % 3)))
            p.fillRect(QRectF(x, y, width, line_h * 0.6), QColor("#C8BFA9"))
            y += line_h
    p.end()

    # ── 覆盖层（译文模式）──
    overlay = TextOverlay()
    overlay.setGeometry(0, 0, PAGE_W, PAGE_H)
    overlay.set_cover(
        {0: segs},
        {0: FakeLayout(0, QRectF(0, 0, PAGE_W, PAGE_H), SCALE)},
        bump=True,
    )
    overlay.set_cover_mode(COVER_TRANSLATED)

    overlay_img = overlay.grab()
    p2 = QPainter(page_img)
    p2.drawPixmap(0, 0, overlay_img)
    p2.end()

    out = r"D:/Codes/FormTransPDF/output/rough_cover_preview.png"
    page_img.save(out)
    print("saved:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
