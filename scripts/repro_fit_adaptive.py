"""
字号 × 行宽 二维自适应 验证脚本（offscreen）。

覆盖新算法三个分支：
A. 纵向富余 → 收窄行宽（重新分段）填充，字号保持 base_px（不压缩字体）；
B. 译文过长 → 才缩字号（下限 _FIT_MIN_FONT_PX=9px）；
C. 极挤 → 兜底缩到 9px，宁溢出也不蚂蚁小。

复刻 _render_page_pixmap 的寻优逻辑（真实渲染路径），用 _layout_flow 排。
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

from src.ui.pdf.text_overlay import TextOverlay, _FIT_MIN_FONT_PX, _cover_font

_FIT_SCALES = (1.0, 0.9, 0.8, 0.7, 0.65, 0.6)
_FILL_WIDTH = 0.8
_FILL_THRESHOLD = 0.75


def fit(ov: TextOverlay, text: str, base_px: float, avail_h: float,
        line_rects: list):
    """复刻 _render_page_pixmap 的自适应分支，返回 (font_px, wscale, text_h)。"""
    fallback = (line_rects[0][0], line_rects[0][1], line_rects[0][2], 40.0)
    font = _cover_font(text, base_px)
    tl, draw_rects, text_h = ov._layout_flow(text, font, line_rects, fallback)
    font_px = base_px
    wscale = 1.0
    if text_h > avail_h + 1.0:
        for scale in _FIT_SCALES[1:]:
            cand = max(base_px * scale, _FIT_MIN_FONT_PX)
            font = _cover_font(text, cand)
            tl, draw_rects, text_h = ov._layout_flow(text, font, line_rects, fallback)
            if text_h <= avail_h + 1.0 or cand <= _FIT_MIN_FONT_PX + 0.5:
                font_px = cand
                break
        else:
            cand = min(_FIT_MIN_FONT_PX, base_px)
            font = _cover_font(text, cand)
            tl, draw_rects, text_h = ov._layout_flow(text, font, line_rects, fallback)
            font_px = cand
    elif text_h < avail_h * _FILL_THRESHOLD:
        # 注：填充分支当前已去掉（数字截断 bug），仅保留 hook 注释
        fill_rects = [(x, y, max(1.0, w * _FILL_WIDTH), h) for (x, y, w, h) in line_rects]
        tl2, dr2, th2 = ov._layout_flow(text, font, fill_rects, fallback)
        if th2 <= avail_h + 1.0 and th2 > text_h + 0.5:
            tl, draw_rects, text_h = tl2, dr2, th2
            wscale = _FILL_WIDTH
    return font_px, wscale, text_h, len(draw_rects)


def fit_no_fill(ov: TextOverlay, text: str, base_px: float, avail_h: float, line_rects: list):
    """复刻当前算法（已去掉收窄填充分支）：base 字号 + 1.0 行宽 → 不缩放下。"""
    fallback = (line_rects[0][0], line_rects[0][1], line_rects[0][2], 40.0)
    font = _cover_font(text, base_px)
    tl, draw_rects, text_h = ov._layout_flow(text, font, line_rects, fallback)
    font_px = base_px
    if text_h > avail_h + 1.0:
        for scale in _FIT_SCALES[1:]:
            cand = max(base_px * scale, _FIT_MIN_FONT_PX)
            font = _cover_font(text, cand)
            tl, draw_rects, text_h = ov._layout_flow(text, font, line_rects, fallback)
            if text_h <= avail_h + 1.0 or cand <= _FIT_MIN_FONT_PX + 0.5:
                font_px = cand
                break
        else:
            cand = min(_FIT_MIN_FONT_PX, base_px)
            font = _cover_font(text, cand)
            tl, draw_rects, text_h = ov._layout_flow(text, font, line_rects, fallback)
            font_px = cand
    return font_px, text_h, len(draw_rects)


def main() -> int:
    ov = TextOverlay()
    fail = 0

    # ── A：译文简洁 + 纵向富余 → 字号保持 base（不缩，行宽不收窄）──
    # 文本长度选在「1.0 行宽 2 行 / 0.8 行宽 3 行」区间（~41 字），便于观察
    text_a = "这是被测试的简洁译文内容，用于验证纵向富余时通过重新分段填充空间，让行数增加铺开更多。"
    lr_a = [(50.0, 100.0, 400.0, 15.0), (50.0, 118.0, 400.0, 15.0)]
    base_a = 20.0
    avail_a = 260.0  # 富余大
    fpa, tha, lines_a = fit_no_fill(ov, text_a, base_a, avail_a, lr_a)
    a1 = fpa == base_a  # 字号保持（不压缩）
    a2 = tha <= avail_a + 1.0  # 放得下
    a3 = lines_a >= 2  # 至少 2 行
    print(f"[A] 简洁+富余：字号={fpa}px(base={base_a}) 行数={lines_a} text_h={tha:.0f}/avail={avail_a}")
    print(f"    A1 字号保持base（不缩）: {'✓' if a1 else '✗'}  A2 放得下: {'✓' if a2 else '✗'}  "
          f"A3 正常行数: {'✓' if a3 else '✗'}")
    fail += 0 if (a1 and a2 and a3) else 1

    # ── B：译文过长 → 缩字号（不到万不得已不缩，但确实放不下）──
    text_b = "这段译文非常长，包含大量中文文本用于模拟译文比原文更长导致纵向空间不足需要缩小字号的场景。"
    lr_b = [(50.0, 200.0, 200.0, 15.0), (50.0, 218.0, 200.0, 15.0)]
    base_b = 20.0
    avail_b = 55.0  # 很挤
    fpb, thb, lines_b = fit_no_fill(ov, text_b, base_b, avail_b, lr_b)
    b1 = fpb < base_b  # 确实缩了字号
    b2 = fpb >= _FIT_MIN_FONT_PX  # 不低于下限
    print(f"\n[B] 译文过长：字号={fpb}px(base={base_b}) text_h={thb:.0f}/avail={avail_b}")
    print(f"    B1 缩字号: {'✓' if b1 else '✗'}  B2 不低于9px: {'✓' if b2 else '✗'}")
    fail += 0 if (b1 and b2) else 1

    # ── C：极挤 → 兜底缩到 9px 下限 ──
    text_c = "极挤场景下的长译文内容，纵向空间严重不足，必须缩到下限但不能再小。"
    lr_c = [(50.0, 300.0, 300.0, 15.0)]
    base_c = 30.0
    avail_c = 24.0  # 一行都放不下
    fpc, thc, lines_c = fit_no_fill(ov, text_c, base_c, avail_c, lr_c)
    c1 = fpc == _FIT_MIN_FONT_PX  # 恰缩到下限
    print(f"\n[C] 极挤：字号={fpc}px(下限 {_FIT_MIN_FONT_PX}px) text_h={thc:.0f}/avail={avail_c}")
    print(f"    C1 恰停在下限: {'✓' if c1 else '✗'}")
    fail += 0 if c1 else 1

    print(f"\n== 结果: {'全部通过 ✅' if fail == 0 else f'{fail} 项失败 ❌'} ==")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
