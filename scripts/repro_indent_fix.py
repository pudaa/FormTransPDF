"""
译文行统一左对齐（奇怪缩进修复）验证脚本（offscreen）。

场景：原文段落存在悬挂缩进（第 1 行顶格、第 2/3 行缩进）—— 旧逻辑译文行
x 跟随各自原文行 x，第 2 行会出现「左侧大量空白」的奇怪缩进；
修复后所有译文行统一从段左边界（r.x()）起排，左对齐整齐。
"""
from __future__ import annotations

import os
import sys
import glob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"D:/Codes/FormTransPDF")

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
for fp in glob.glob("C:/Windows/Fonts/msyh.ttc") + glob.glob("C:/Windows/Fonts/segoeui.ttf"):
    QFontDatabase.addApplicationFont(fp)

from src.ui.pdf.text_overlay import TextOverlay, _cover_font


def main() -> int:
    ov = TextOverlay()
    fail = 0

    # ── 场景：悬挂缩进原文（行1 x=50 顶格，行2/3 x=100 缩进）──
    # 段左边界 = min lx = 50
    line_rects = [
        (50.0, 100.0, 500.0, 15.0),
        (100.0, 118.0, 450.0, 15.0),   # 缩进
        (100.0, 136.0, 450.0, 15.0),   # 缩进
    ]
    text = "这是一段译文内容，用于验证所有译文行统一从左边界起排，不会因为原文悬挂缩进而产生奇怪的第二行缩进。"
    base = 16.0
    font = _cover_font(text, base)
    fallback = (50.0, 100.0, 500.0, 60.0)
    seg_left = 50.0

    # ── 旧行为（base_x=None）：译文行跟随原文行 x → 第 2 行缩进 ──
    tl_old, dr_old, th_old = ov._layout_flow(text, font, line_rects, fallback, line_width=500.0)
    xs_old = [r[0] for r in dr_old]
    # ── 新行为（base_x=seg_left）：所有行统一 50 ──
    tl_new, dr_new, th_new = ov._layout_flow(
        text, font, line_rects, fallback, line_width=500.0, base_x=seg_left
    )
    xs_new = [r[0] for r in dr_new]

    print("[悬挂缩进场景] 原文行 x = [50, 100, 100]")
    print(f"  旧行为（跟随原文行 x）：各行 x = {[f'{x:.0f}' for x in xs_old]}")
    print(f"  新行为（统一段左 50）：各行 x = {[f'{x:.0f}' for x in xs_new]}")

    # 断言：新行为所有行 x 完全一致（=50）
    n1 = len(set(xs_new)) == 1 and abs(xs_new[0] - seg_left) < 0.5
    # 旧行为确实存在缩进（证明场景构造有效）
    n0 = len(set(xs_old)) > 1
    # 行数/内容不因统一 x 变化（文本被完整排版）
    n2 = th_new >= th_old - 1.0  # 统一 x 后行宽不变（line_width 固定）→ text_h 应一致
    n3 = all(abs(x - seg_left) < 0.5 for x in xs_new)
    print(f"    N0 旧行为存在参差缩进（场景有效）: {'✓' if n0 else '✗'}")
    print(f"    N1 新行为所有行 x=50 统一: {'✓' if n1 else '✗'}")
    print(f"    N2 text_h 不退化: {'✓' if n2 else '✗'}")
    print(f"    N3 无一行偏离段左: {'✓' if n3 else '✗'}")
    fail += 0 if (n0 and n1 and n2 and n3) else 1

    print(f"\n== 结果: {'全部通过 ✅' if fail == 0 else f'{fail} 项失败 ❌'} ==")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
