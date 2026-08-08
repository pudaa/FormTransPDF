"""
智能断行 + 横向碰撞可用宽度 验证脚本（offscreen）。

覆盖：
1. 智能断行（WordWrap）：含数字/英文/标点混合文本不被硬拆；
2. 行宽 = 横向碰撞可用宽度：页眉段（右侧无碰撞段）行宽到页边，译文不被原文行宽自限换行。
"""
from __future__ import annotations

import os
import sys
import glob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"D:/Codes/FormTransPDF")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFontDatabase, QTextLayout, QTextOption, QFont
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
for fp in glob.glob("C:/Windows/Fonts/msyh.ttc") + glob.glob("C:/Windows/Fonts/segoeui.ttf"):
    QFontDatabase.addApplicationFont(fp)

from src.ui.pdf.text_overlay import TextOverlay, _cover_font


def main() -> int:
    fail = 0
    ov = TextOverlay()
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(20)

    # ── A：智能断行 — 数字/英文按词不拆，中文任意断 ──
    text_a = "认知科学 17, 495-499（1993）"
    tl = QTextLayout(text_a)
    tl.setFont(font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WordWrap)
    tl.setTextOption(opt)
    tl.beginLayout()
    chunks = []
    while True:
        line = tl.createLine()
        if not line.isValid():
            break
        line.setLineWidth(200)
        chunks.append(text_a[line.textStart():line.textStart() + line.textLength()])
    tl.endLayout()
    a1 = "495-499" not in "".join(chunks)[:0]  # 占位
    # 数字 495-499 完整出现在某一行（智能断行核心：不被拆成 "495-49" + "9..."）
    intact = any("495-499" in c for c in chunks)
    # 行数不应暴增（短文本不应被拆成多行）—— 行宽 200px 文本约 17 字 → 2 行合理
    a3 = len(chunks) <= 3
    print(f"[A] 智能断行：行宽 200px 排版 →")
    for i, c in enumerate(chunks):
        print(f"    行{i}: {c!r}")
    print(f"    A1 数字 495-499 整体完整: {'✓' if intact else '✗'}  "
          f"A2 行数合理(≤3): {'✓' if a3 else '✗'}")
    fail += 0 if (intact and a3) else 1

    # ── B：横向碰撞 — 单行段（页眉）行宽到页边，译文不被自限 ──
    # 构造一个简单场景：page 内只有 seg[0]（页眉）
    from src.ui.pdf.cover import CoverSegment
    seg = CoverSegment(page=0, text="COGNITIVE SCIENCE 17, 4959 (1993)",
                       pdf_x=72, pdf_y=72, pdf_width=400, pdf_height=15,
                       font_size=10.0, line_rects=[(72, 72, 400, 15)],
                       line_texts=["COGNITIVE SCIENCE 17, 4959 (1993)"])
    seg.content_rect = QRectF(72, 72, 400, 15)
    seg.display_text = "认知科学 17, 495-499（1993）"
    origin = QRectF(0, 0, 595, 842).topLeft()
    page_w, page_h = 595.0, 842.0
    next_x_by_seg = ov._compute_next_x([seg], page_w, origin)
    next_x = next_x_by_seg[id(seg)]
    b1 = next_x > 400  # 横向可用宽度 > 原文行宽（页眉右侧没碰撞段，next_x 应 = page_w - GAP）
    b2 = abs(next_x - (page_w - 4.0)) < 1.0  # 约等于页宽 - GAP
    avail_w = next_x - origin.x() - seg.content_rect.x() - 4.0  # _SEG_GAP
    b3 = avail_w > 400  # 行宽扩展
    print(f"\n[B] 横向碰撞：单行段(右侧无碰撞) next_x={next_x:.0f} (≈page_w-GAP?={abs(next_x-(page_w-4.0))<1})")
    print(f"    B1 next_x > 原文行宽 400: {'✓' if b1 else '✗'}  "
          f"B2 约等于页宽-GAP: {'✓' if b2 else '✗'}  "
          f"B3 avail_w > 400: {'✓' if b3 else '✗'}")
    fail += 0 if (b1 and b2 and b3) else 1

    # ── C：多栏场景 — 三栏 y 重叠，右侧 next_x = 邻栏左（边界 = 列宽）──
    # 三段 y 设相同（200）使其垂直重叠，触发横向 y-重叠判定；
    # 段坐标全部控制在 page_w(595) 内
    seg_left = CoverSegment(page=0, text="left col", pdf_x=72, pdf_y=200,
                            pdf_width=180, pdf_height=15, font_size=10.0,
                            line_rects=[(72, 200, 180, 15)],
                            line_texts=["left"])
    seg_left.content_rect = QRectF(72, 200, 180, 15)
    seg_left.display_text = "左栏译文"
    seg_mid = CoverSegment(page=0, text="mid col", pdf_x=270, pdf_y=200,
                           pdf_width=180, pdf_height=15, font_size=10.0,
                           line_rects=[(270, 200, 180, 15)],
                           line_texts=["mid"])
    seg_mid.content_rect = QRectF(270, 200, 180, 15)
    seg_mid.display_text = "中栏译文"
    seg_right = CoverSegment(page=0, text="right col", pdf_x=468, pdf_y=200,
                             pdf_width=127, pdf_height=15, font_size=10.0,
                             line_rects=[(468, 200, 127, 15)],
                             line_texts=["right"])
    seg_right.content_rect = QRectF(468, 200, 127, 15)
    seg_right.display_text = "右栏译文"
    nx = ov._compute_next_x([seg_left, seg_mid, seg_right], page_w, origin)
    # left next_x ≈ seg_mid.left - GAP = 270 - 4 = 266
    # mid  next_x ≈ seg_right.left - GAP = 468 - 4 = 464
    # right next_x ≈ page_w - GAP = 595 - 4 = 591（右侧无碰撞段）
    c1 = abs(nx[id(seg_left)] - 266) < 2
    c2 = abs(nx[id(seg_mid)] - 464) < 2
    c3 = abs(nx[id(seg_right)] - 591) < 2
    print(f"\n[C] 多栏：左/中/右 next_x = {nx[id(seg_left)]:.0f}/{nx[id(seg_mid)]:.0f}/{nx[id(seg_right)]:.0f}")
    print(f"    C1 左栏→中栏左(266): {'✓' if c1 else '✗'}  "
          f"C2 中栏→右栏左(464): {'✓' if c2 else '✗'}  "
          f"C3 右栏→页边(591): {'✓' if c3 else '✗'}")
    fail += 0 if (c1 and c2 and c3) else 1

    print(f"\n== 结果: {'全部通过 ✅' if fail == 0 else f'{fail} 项失败 ❌'} ==")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())