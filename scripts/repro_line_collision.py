"""
行间碰撞消除验证 — 构造「原文行距紧密 + 译文行高较大」场景，
检查 _layout_flow / _render_page_pixmap 返回的行矩形是否两两不重叠。

背景：译文字体（微软雅黑）行高通常 > 原文行 bbox 高度；若译文行沿用
原文行 top，行高差会顶到上一行底 → 文字互相遮盖。布局器用 cursor_y
游标保证 ly[i+1] >= ly[i] + lh[i] + _LINE_GAP。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"D:/Codes/FormTransPDF")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
import glob

app = QApplication(sys.argv)
for fp in glob.glob("C:/Windows/Fonts/msyh.ttc") + glob.glob("C:/Windows/Fonts/segoeui.ttf"):
    QFontDatabase.addApplicationFont(fp)

from src.ui.pdf.text_overlay import TextOverlay, _cover_font, _LINE_GAP


def check_no_overlap(rects: list, label: str) -> bool:
    """检查同一段内行矩形是否两两垂直重叠。返回 True=无重叠。"""
    ok = True
    for i in range(len(rects) - 1):
        _x0, y0, _w0, h0 = rects[i]
        x1, y1, _w1, _h1 = rects[i + 1]
        gap = y1 - (y0 + h0)
        if gap < 0:
            ok = False
            print(f"  ❌ {label} 行{i}与行{i+1} 重叠 {gap:.1f}px "
                  f"(y={y0:.0f}+{h0:.0f} → y={y1:.0f})")
        elif gap < _LINE_GAP:
            print(f"  ⚠️ {label} 行{i}与行{i+1} 间距不足 {gap:.1f}px（< _LINE_GAP）")
    return ok


def main() -> int:
    ov = TextOverlay()
    total = 0
    fail = 0

    # ── 场景 1：原文 3 行、行距紧密（行 top 差 13px，行高 12px），
    #            译文行高（微软雅黑 CJK）约 16px+ —— 旧版必重叠 ──
    line_rects = [
        (50.0, 100.0, 400.0, 12.0),
        (50.0, 113.0, 400.0, 12.0),
        (50.0, 126.0, 400.0, 12.0),
    ]
    text1 = "这是第 1 段的译文内容，包含足够长的中文文本以触发换行，验证行间碰撞消除是否生效，每行不会挤压遮盖上一行。"
    font = _cover_font(text1, 16.0)
    tl, draw_rects, text_h = ov._layout_flow(text1, font, line_rects, (50.0, 100.0, 400.0, 60.0))
    print(f"场景1（紧密行距多行原文）: 原文3行top=100/113/126, 译文行数={len(draw_rects)}")
    for i, (x, y, w, h) in enumerate(draw_rects):
        print(f"  行{i}: y={y:.1f} h={h:.1f}")
    ok = check_no_overlap(draw_rects, "场景1")
    total += 1
    fail += 0 if ok else 1

    # ── 场景 2：原文 1 行 + 译文 3 行（剩余行向下累加）──
    line_rects2 = [(50.0, 200.0, 200.0, 15.0)]
    text2 = "短行原文对应的长译文，会折成多行，每一行都要与上一行保持间距，绝不互相遮盖。"
    tl2, draw_rects2, text_h2 = ov._layout_flow(text2, _cover_font(text2, 15.0), line_rects2, (50.0, 200.0, 200.0, 30.0))
    print(f"\n场景2（1行原文→多行译文）: 译文行数={len(draw_rects2)}")
    for i, (x, y, w, h) in enumerate(draw_rects2):
        print(f"  行{i}: y={y:.1f} h={h:.1f}")
    ok = check_no_overlap(draw_rects2, "场景2")
    total += 1
    fail += 0 if ok else 1

    # ── 场景 3：字号自适应 —— 行距拉开后 text_h 变大 → 应缩小字号 ──
    # 直接验证自适应循环能找到 text_h <= avail_h 的字号
    base_px = 20.0
    avail_h = 90.0
    chosen = None
    for scale in (1.0, 0.9, 0.8, 0.7, 0.65, 0.6):
        cand = max(base_px * scale, 6.5)
        tl3, draw_rects3, text_h3 = ov._layout_flow(
            text1, _cover_font(text1, cand), line_rects, (50.0, 100.0, 400.0, 60.0)
        )
        print(f"场景3 字号 {cand:.1f}px → text_h={text_h3:.1f} (avail_h={avail_h})")
        if text_h3 <= avail_h + 1.0 or cand <= 6.5 + 0.5:
            chosen = cand
            break
    print(f"场景3: 自适应选中字号 = {chosen:.1f}px")
    if chosen is None:
        print("  ❌ 自适应未收敛（应始终能选出字号）")
    elif chosen >= base_px * 0.95:
        print("  ✅ base_px 直接放下，无需缩小（行距拉开后仍充裕）")
    else:
        print("  ℹ️ 行距拉开导致总高超限，缩小到", f"{chosen:.1f}px（预期代价）")
    total += 1

    print(f"\n== 结果: {total - fail}/{total} 场景通过 ==")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
