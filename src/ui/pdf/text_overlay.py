"""
PDF 文本覆盖层 — 覆盖在 QPdfView viewport 上的透明层。

只负责绘制：
1. 已选中文本的高亮矩形（蓝色半透明）
2. 浮动工具栏（选中后弹出，提供复制、搜索等操作）

所有坐标均为 viewport 相对坐标。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
)
from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QTimer, QPropertyAnimation
from PySide6.QtGui import (
    QPainter, QColor, QFont, QFontDatabase, QTextDocument, QTextLayout,
    QTextOption, QPixmap,
)

from src.ui.base.icon_factory import svg_icon
from src.ui.base.theme import theme_manager
from src.ui.pdf.cover import COVER_TRANSPARENT, COVER_MODES

# ── 覆盖层绘制常量 ──────────────────────────────────────────
_COVER_BG = QColor("#FFFFFF")      # 覆盖白底
_COVER_FG = QColor("#1A1A1A")      # 覆盖黑字
_COVER_PAD = 2.0                   # 白底每侧外扩像素（覆盖原文抗锯齿边缘）
# 渲染下限：base_px 低于此值的段不绘制（保持原文透出）。与「自适应下限」分离——
# 碰撞时允许缩小，但绝不低于 _FIT_MIN_FONT_PX（避免缩到蚂蚁大小不可读）。
_COVER_MIN_FONT_PX = 6.5
# 自适应字号下限：碰撞挤压时译文最多缩到该像素大小，低于此宁可轻微溢出/裁切。
# （9 → 10：实测 9px 在 100% 缩放下已接近不可读；配合碰撞误判修复，
#  正常段落极少触底）
_FIT_MIN_FONT_PX = 10.0
_COVER_CACHE_LIMIT = 24            # 页 pixmap 缓存上限（LRU 淘汰，不再全清）
# 单页 pixmap 像素上限（约 96MB RGBA）：极端缩放（8×+HiDPI）下整页可达数百 MB，
# 超限按比例降低内部分辨率渲染（视觉轻微变糊 vs 内存尖峰/OOM）
_PIXMAP_MAX_PIXELS = 24_000_000
# CJK 字形填满 em 方框，同 pt 下视觉比拉丁大 ~15%，故乘以 0.85 让译文与原 PDF 视觉等高
_CJK_VISUAL_SCALE = 0.85
# 段落之间的最小间距（像素），白底向下扩张到此即截断，避免压到下一段
_SEG_GAP = 4.0
# 单段宽下限：低于此值不渲染。降低（原 12 → 6）让表格窄 cell 也能绘制。
_SEG_MIN_WIDTH = 6.0
# 行距系数：在 QTextLayout 实际 line.height 基础上乘以此值，给译文一点呼吸空间
_LINE_LEADING = 1.18
# 行间最小垂直间距（像素）：译文/回退行矩形之间绝不重叠的最小间隔。
# 译文字体（微软雅黑）行高通常大于原文行 bbox 高度，若直接沿用原文行 top，
# 相邻译文行会顶到上一行底 → 文字互相遮盖。布局器用 cursor_y 游标保证
# 每行 top ≥ 上一行底 + _LINE_GAP。
_LINE_GAP = 3.0


def _has_cjk(text: str) -> bool:
    """检测文本是否含中日韩字符（用于字体回退）。"""
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF:
            return True
    return False


# ── 字体回退链（QFontDatabase 运行时探测，替代硬编码平台判断）──
_FONT_FAMILY_CACHE: dict[str, str] = {}


def _first_available_family(candidates: list[str]) -> str:
    """返回系统中第一个实际安装的字体族；全缺时回退末项（由 Qt 自行兜底）。"""
    key = "|".join(candidates)
    if key not in _FONT_FAMILY_CACHE:
        try:
            available = set(QFontDatabase.families())
        except Exception:
            available = set()
        _FONT_FAMILY_CACHE[key] = next(
            (f for f in candidates if f in available), candidates[-1]
        )
    return _FONT_FAMILY_CACHE[key]


def _cjk_font_family() -> str:
    return _first_available_family([
        "Microsoft YaHei UI",   # Windows
        "PingFang SC",          # macOS
        "Noto Sans CJK SC",     # Linux 主流发行版
        "WenQuanYi Zen Hei",    # Linux 老发行版兜底
    ])


def _latin_font_family() -> str:
    return _first_available_family([
        "Segoe UI",             # Windows
        "Helvetica Neue",       # macOS
        "DejaVu Sans",          # Linux
    ])


def _cover_font(text: str, pixel_size: float) -> QFont:
    """按文本语言选择字体并调整像素尺寸。

    CJK 字形在微软雅黑里填满 em 方框，同 pt 下视觉比拉丁字大 ~15%。
    因此对 CJK 文本乘以 _CJK_VISUAL_SCALE，使译文在视觉上贴合原 PDF 文字大小。
    像素尺寸公式：font_pt × layout_scale（与 PDF 渲染同坐标系，逐 pt 换算）。
    """
    family = _cjk_font_family() if _has_cjk(text) else _latin_font_family()
    size = pixel_size * (_CJK_VISUAL_SCALE if _has_cjk(text) else 1.0)
    font = QFont(family)
    font.setPixelSize(max(1, int(round(size))))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


class TextOverlay(QWidget):
    """
    覆盖在 QPdfView viewport 上的透明层。

    只负责绘制：
    1. 已选中文本的高亮矩形（蓝色半透明）—— 必须实现
    2. 浮动工具栏（选中后弹出）
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")

        self._highlights: List[QRectF] = []

        # ── 覆盖层（粗糙翻译）状态 ──
        self._cover_mode: str = COVER_TRANSPARENT
        self._cover_pages: dict = {}       # page -> [CoverSegment]（content 坐标已就绪）
        self._cover_layouts: dict = {}     # page -> PageLayout
        # 双层版本号：全局版本（模式切换）+ 页级版本（流式增量只失效脏页）
        self._global_version: int = 0
        self._page_versions: dict[int, int] = {}
        # LRU 页缓存：(page, scale_key, global_v, page_v) -> QPixmap
        self._cover_cache: OrderedDict = OrderedDict()

        # ── 浮动工具栏 ──
        # 父级必须是 viewport（而非本透明层）：WA_TransparentForMouseEvents 会使其
        # 整个子树（含工具栏按钮）被真实命中测试跳过——按钮收不到真实点击。
        # 坐标与覆盖层一致（覆盖层覆盖整个 viewport，位于 (0,0)）。
        self._toolbar = FloatingToolbar(self.parentWidget() or self)
        self._toolbar.hide()

        # ── 轻量提示气泡（如「已复制」）──
        self._toast = Toast(self)
        self._toast.hide()

    @property
    def toolbar(self):
        return self._toolbar

    def set_highlights(self, rects: List[QRectF]):
        """设置高亮矩形列表（仅更新绘制，不控制工具栏）"""
        self._highlights = rects
        self.update()

    def show_toolbar_at(self, rects: List[QRectF]):
        """在选区上方显示浮动工具栏（仅在鼠标松开时调用）"""
        if not rects:
            self._toolbar.hide()
            return

        # 计算选区的包围矩形
        united = rects[0]
        for r in rects[1:]:
            united = united.united(r)

        toolbar_w = self._toolbar.sizeHint().width()
        toolbar_h = self._toolbar.sizeHint().height()
        x = int(united.center().x() - toolbar_w / 2)
        y = int(united.top() - toolbar_h - 8)

        # 边界检查
        vp_w = self.width()
        x = max(8, min(x, vp_w - toolbar_w - 8))
        y = max(8, y)

        self._toolbar.move(x, y)
        self._toolbar.show()
        self._toolbar.raise_()

    def show_toast(self, text: str, anchor_rect: QRectF | None = None) -> None:
        """在选区附近显示轻量提示气泡（如「已复制」），边界自动夹取。"""
        self._toast.adjustSize()
        w = self._toast.width()
        h = self._toast.height()
        if anchor_rect is not None:
            x = int(anchor_rect.center().x() - w / 2)
            y = int(anchor_rect.bottom() + 8)
        else:
            x = (self.width() - w) // 2
            y = (self.height() - h) // 2
        x = max(8, min(x, self.width() - w - 8))
        y = max(8, min(y, self.height() - h - 8))
        self._toast.show_toast(text, QPoint(x, y))

    def clear_highlights(self):
        """清除所有高亮和工具栏"""
        self._highlights.clear()
        self._toolbar.hide()
        self.update()

    # ── 覆盖层（粗糙翻译）API ──────────────────────────────

    @property
    def cover_mode(self) -> str:
        return self._cover_mode

    def set_cover_mode(self, mode: str) -> None:
        """切换覆盖层渲染模式（透明 / 白底原文 / 白底译文）。"""
        if mode not in COVER_MODES:
            return
        if mode == self._cover_mode:
            return
        self._cover_mode = mode
        self._bump_cache(None)
        self.update()

    def _bump_cache(self, pages: set[int] | None) -> None:
        """失效页缓存：pages=None 全部；否则仅指定页（页级版本+1 并剔除旧项）。

        流式翻译期间每 150ms 防抖刷新只 bump 收到译文的页 —— 其余页缓存
        原样保留，不再整页重渲染（旧实现全清导致的卡顿根因之一）。
        """
        if pages is None:
            self._global_version += 1
            self._page_versions.clear()
            self._cover_cache.clear()
            return
        for pg in pages:
            self._page_versions[pg] = self._page_versions.get(pg, 0) + 1
        for key in [k for k in self._cover_cache if k[0] in pages]:
            self._cover_cache.pop(key, None)

    def set_cover(
        self,
        pages: dict,
        layouts: dict,
        bump: bool = False,
        bump_pages: set[int] | None = None,
    ) -> None:
        """设置覆盖层数据。

        :param pages:      page -> [CoverSegment]（content_rect 已由 viewer 计算）
        :param layouts:    page -> PageLayout（页矩形与缩放）
        :param bump:       True = 全量失效（模式切换/完成）；与 bump_pages 二选一
        :param bump_pages: 仅这些页数据变化（流式增量）；空集合 = 仅同步引用
        """
        self._cover_pages = pages or {}
        self._cover_layouts = layouts or {}
        if bump_pages is not None:
            if bump_pages:
                self._bump_cache(bump_pages)
        elif bump:
            self._bump_cache(None)
        self.update()

    def clear_cover(self) -> None:
        self._cover_pages = {}
        self._cover_layouts = {}
        self._cover_mode = COVER_TRANSPARENT
        self._bump_cache(None)
        self.update()

    # ── 覆盖层绘制 ─────────────────────────────────────────

    def _scroll_offset(self) -> tuple[int, int]:
        """当前视口滚动偏移（内容坐标 → viewport 坐标）。

        覆盖层是 QPdfView viewport 的子控件，滚动条在其祖父 QPdfView 上。
        """
        try:
            vp = self.parentWidget()
            if vp is None:
                return 0, 0
            view = vp.parentWidget()
            hs = view.horizontalScrollBar().value() if hasattr(view, "horizontalScrollBar") else 0
            vs = view.verticalScrollBar().value() if hasattr(view, "verticalScrollBar") else 0
            return int(hs), int(vs)
        except Exception:
            return 0, 0

    def _draw_cover(self, painter: QPainter) -> None:
        """绘制覆盖层：按页绘制白底黑字（带按页 QPixmap 缓存）。"""
        if self._cover_mode == COVER_TRANSPARENT or not self._cover_pages:
            return
        if not self._cover_layouts:
            return

        hs, vs = self._scroll_offset()
        vp_rect = QRectF(0, 0, self.width(), self.height())

        for page, segs in self._cover_pages.items():
            layout = self._cover_layouts.get(page)
            if layout is None or not segs:
                continue
            page_vp = layout.rect.translated(-hs, -vs)
            if not page_vp.intersects(vp_rect):
                continue

            scale_key = int(layout.scale * 100)
            key = (
                page, scale_key, self._global_version,
                self._page_versions.get(page, 0),
            )
            pix = self._cover_cache.get(key)
            if pix is None:
                pix = self._render_page_pixmap(page, layout, segs)
                if pix is None or pix.isNull():
                    continue
                self._cover_cache[key] = pix
                # LRU 淘汰最旧（旧实现超限全清 → 连续缩放时反复全量重渲染）
                while len(self._cover_cache) > _COVER_CACHE_LIMIT:
                    self._cover_cache.popitem(last=False)
            else:
                self._cover_cache.move_to_end(key)

            painter.drawPixmap(page_vp.topLeft(), pix)

    def _compute_next_y(
        self, segs: list, page_h: float, origin
    ) -> dict[int, float]:
        """按 x 范围重叠规则计算每段向下扩展的下界 next_y。

        替代旧版列感知（_cluster_columns + 25/75 分位裁剪 + 跨列 overflow 单独列）三件套：
        1. 不再把段归到「列」再做聚类 —— 直接以「下方哪个段的 x 与本段重叠」作边界。
        2. 全宽段（width > page_w × 0.5）天然与所有下方段 x 重叠 → 退回为页底或最近段顶。
        3. 段之间不存在水平重叠 → 自然不互相覆盖（PDF 文字流本质保证）。
        4. 段与段的下方重叠判定使用 2px 缓冲，规避抗锯齿/坐标舍入造成的假接触。

        :returns: id(seg) -> 下一页内 y 上限（已扣 _SEG_GAP；无下方重叠段 → 页底）。
        """
        items = [s for s in segs if s.content_rect and not s.content_rect.isEmpty()]
        page_bottom = origin.y() + float(page_h)
        next_y_by_seg: dict[int, float] = {}
        for seg in items:
            r = seg.content_rect
            below_y = []
            for b in items:
                if b is seg:
                    continue
                br = b.content_rect
                if br.y() <= r.y() + 1:
                    continue
                # x 重叠判定：要求有效重叠宽度 ≥ **本段自身宽度**的 25%（仅数像素
                # 的擦边接触不算 —— 上下标/公式编号/栏沟毛边曾把本段可用高度压到
                # 极限，触发过度缩字）。基准取本段而非较窄者：避免"极窄碎段搭在
                # 本段边缘"时按窄段比例凑数通过。
                ovl = min(br.right(), r.right()) - max(br.left(), r.left())
                if ovl > 2 and ovl >= 0.25 * r.width():
                    below_y.append(br.y())
            next_y_by_seg[id(seg)] = (
                min(below_y) - _SEG_GAP if below_y else page_bottom
            )
        return next_y_by_seg

    def _compute_next_x(
        self, segs: list, page_w: float, origin
    ) -> dict[int, float]:
        """按「右侧 y 重叠段」计算每段横向可用右边界 next_x。

        与 _compute_next_y（纵向碰撞）对称的**横向碰撞**：
        - 对每段，右侧最近的、y 范围与本段重叠的段，其左边缘即横向边界；
        - 右侧没有碰撞段（如页眉/单行短段）→ 边界 = 页宽（译文可占满整页，
          不再被原文行宽自限换行 —— 数字截断 bug 的根因修复）；
        - 返回值为页面局部 x 坐标（已扣 _SEG_GAP），行宽 = next_x − 段左。

        :returns: id(seg) -> 页面局部横向右边界（content 坐标，已扣 GAP）。
        """
        items = [s for s in segs if s.content_rect and not s.content_rect.isEmpty()]
        page_right = origin.x() + float(page_w)
        next_x_by_seg: dict[int, float] = {}
        for seg in items:
            r = seg.content_rect
            right_bound = page_right
            for b in items:
                if b is seg:
                    continue
                br = b.content_rect
                # 在右侧（左边缘 ≥ 本段右边缘 − 2px 容差）且 y 范围有**有效重叠**
                # （≥ 本段自身高度的 15% 且 > 2px）：斜向擦边的单像素接触不再收窄
                # 本段行宽，避免"横向剩余空间没用完就缩字"。
                if br.left() >= r.right() - 2:
                    y_ovl = min(br.bottom(), r.bottom()) - max(br.top(), r.top())
                    if y_ovl > 2 and y_ovl >= 0.15 * r.height():
                        if br.left() < right_bound:
                            right_bound = br.left()
            next_x_by_seg[id(seg)] = right_bound - _SEG_GAP
        return next_x_by_seg

    def _layout_flow(self, text: str, font, line_rects: list,
                     fallback_rect: tuple, line_width: float | None = None,
                     base_x: float | None = None) -> tuple:
        """逐行流动排版：译文在「可用行宽」里换行，行间做碰撞消除。

        智能断行（WordWrap，Qt 默认）：中文任意处断行，**数字/英文按词不拆**
        （之前用 WrapAnywhere 会把 "1993" 硬拆成 "(1"+"993"）。
        行宽来源：
        - `line_width` 提供（推荐）：横向碰撞可用宽度 —— 右侧没有碰撞段/没到
          页边时，译文可占满可用空间，**不再被原文行宽自限换行**（根因修复）；
        - 否则退化为原文行宽（旧行为，兼容调用方）。
        行 X 起点：
        - `base_x` 提供（推荐）：**所有译文行统一从该 X 起排（左对齐整齐）**——
          原文段落存在首行缩进/悬挂缩进/参差短行时，各行 lx 不同，若译文行跟随
          各自原文行 x，会出现「第二行左侧大量空白、第三行继承缩进」的奇怪缩进；
        - 否则退化为跟随原文行 x（旧行为，兼容调用方）。
        行间碰撞消除：译文字体行高通常大于原文行 bbox，直接用原文行 top 会顶到
        上一行底 → cursor_y 单调递增游标保证任意两行矩形互不重叠。

        返回 (tl, draw_rects, text_h)：draw_rects 为每行页面局部矩形 (x,y,w,h)。
        """
        tl = QTextLayout(text)
        tl.setFont(font)
        t_opt = QTextOption()
        # 智能断行：中文任意断，数字/英文按词不拆（数字截断 bug 根因修复）
        t_opt.setWrapMode(QTextOption.WrapMode.WordWrap)
        tl.setTextOption(t_opt)
        tl.beginLayout()
        draw_rects: list[tuple[float, float, float, float]] = []
        if line_rects:
            last_lx = line_rects[-1][0]
            last_lw = line_rects[-1][2]
            cursor_y = line_rects[0][1]  # 首行对齐原文首行 top
            for (_lx, _ly, _lw, _lh) in line_rects:
                line = tl.createLine()
                if not line.isValid():
                    break
                lw = line_width if line_width is not None else _lw
                line.setLineWidth(max(1.0, lw))
                lh = max(_lh, line.height() * _LINE_LEADING)
                # 碰撞消除：绝不顶到上一行；行距不足时顺延游标
                ly = max(_ly, cursor_y)
                x = base_x if base_x is not None else _lx
                draw_rects.append((x, ly, lw, lh))
                cursor_y = ly + lh + _LINE_GAP
            # 剩余文本继续排（宽 = 最后一行宽，y 沿游标向下累加）
            while True:
                line = tl.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(max(1.0, last_lw if line_width is None else line_width))
                hh = line.height() * _LINE_LEADING
                x = base_x if base_x is not None else last_lx
                draw_rects.append((x, cursor_y,
                                   last_lw if line_width is None else line_width, hh))
                cursor_y += hh + _LINE_GAP
        else:  # 无行矩形（旧数据兜底）：整段单矩形内换行
            fx, fy, fw, _fh = fallback_rect
            y = fy
            while True:
                line = tl.createLine()
                if not line.isValid():
                    break
                lw = line_width if line_width is not None else fw
                line.setLineWidth(max(1.0, lw))
                hh = line.height() * _LINE_LEADING
                x = base_x if base_x is not None else fx
                draw_rects.append((x, y, lw, hh))
                y += hh + _LINE_GAP
        tl.endLayout()
        text_h = sum(h for (_x, _y, _w, h) in draw_rects)
        return tl, draw_rects, text_h

    def _render_page_pixmap(self, page: int, layout, segs) -> QPixmap | None:
        """把一页的覆盖层（白底 + 文本）预渲染为透明底 pixmap。

        布局器逻辑（按段自身范围 + x 重叠碰撞消除）：
        - **next_y**：每段向下扩展上限 = 与本段 x 重叠的最近下方段顶部 − GAP（无则页底）。
          替代旧版「按 x 中心聚列 + 25/75 分位裁剪 + 跨列 overflow 单独全宽列」三件套：
            · 列聚类会把横跨坏段既归到原列又归到 overflow 列 → 重复绘制全宽白底盖邻列；
            · 分位裁剪在横跨坏段存在时拉偏列边界 → 本列段被裁窄 → 译文显示不全；
            · 新方案不再做「列」抽象，直接以「x 范围是否重叠」判定，根除双重问题。
        - **clip rect**：每段用自己的 content_rect（外扩 _COVER_PAD）水平裁剪，
          垂直裁到 max_bottom = min(段底+白底扩张, next_y)。
          保证：① 本段白底/文字完全覆盖本段原文；② 水平方向不会越界到不重叠的他段。
        - **字号**：先按 base_px 排版；若 text_h > avail_h 则迭代缩小
          （步长 0.9/0.8/0.7/0.65/0.6，floor = _COVER_MIN_FONT_PX），
          真正放不下时才允许轻微裁切。标题/正文字号差异化恢复，跨列不互相挤小字号。
        - **关键**：每行译文 drawText 前必须 p.setFont(font)（否则译文全用 painter
          默认字体渲染，导致「均码变小」的视觉问题——这是覆盖层一直存在的核心 bug）。
        """
        w = max(1, int(round(layout.rect.width())))
        h = max(1, int(round(layout.rect.height())))
        dpr = self.devicePixelRatioF() or 1.0

        # 像素帽：极端缩放（8×+HiDPI）下整页 RGBA 可达数百 MB —— 超限按比例
        # 降低内部分辨率（视觉轻微变糊，换取内存安全）
        total_px = float(w) * float(h) * dpr * dpr
        if total_px > _PIXMAP_MAX_PIXELS:
            dpr *= (_PIXMAP_MAX_PIXELS / total_px) ** 0.5

        pix = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)

        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        origin = layout.rect.topLeft()

        # ── 每段的下方扩展上界（纵向碰撞：x 重叠规则）──
        next_y_by_seg = self._compute_next_y(segs, float(h), origin)
        # ── 每段的横向可用右边界（横向碰撞：右侧 y 重叠段 / 页边）──
        next_x_by_seg = self._compute_next_x(segs, float(w), origin)

        # 绘制顺序：普通段按 (y, x) 升序（上方先画）；**被拖拽偏移的浮动段排序值
        # 更大 → 排在最后绘制，浮在其他段白底之上** —— 拖出来的字块不被盖住。
        # （旧代码键值写反：floating=0 排最前 → 最先画 → 反被普通段覆盖）
        def _float_key(s) -> tuple:
            floating = (
                getattr(s, "offset_x", 0.0) != 0.0
                or getattr(s, "offset_y", 0.0) != 0.0
            )
            return (1 if floating else 0, s.content_rect.y(), s.content_rect.x())

        ordered_segs = sorted(
            [s for s in segs if s.content_rect and not s.content_rect.isEmpty()],
            key=_float_key,
        )

        for seg in ordered_segs:
            text = (seg.display_text or seg.text).strip()
            if not text:
                continue

            # 用户拖拽偏移：整段平移（仅绘制位置，不参与碰撞计算）
            off_x = getattr(seg, "offset_x", 0.0)
            off_y = getattr(seg, "offset_y", 0.0)
            is_floating = off_x != 0.0 or off_y != 0.0
            r = seg.content_rect.translated(
                -origin.x() + off_x, -origin.y() + off_y
            )
            base_px = seg.font_size * layout.scale
            if base_px < _COVER_MIN_FONT_PX or r.width() < _SEG_MIN_WIDTH:
                continue

            # ── 行级矩形（PDF 坐标 → 页面局部 content 坐标）──
            line_rects = []
            if seg.line_rects:
                for (x0_p, y0_p, x1_p, y1_p) in seg.line_rects:
                    line_rects.append((
                        r.x() + (x0_p - seg.pdf_x) * layout.scale,
                        r.y() + (y0_p - seg.pdf_y) * layout.scale,
                        (x1_p - x0_p) * layout.scale,
                        (y1_p - y0_p) * layout.scale,
                    ))
            fallback_rect = (r.x(), r.y(), r.width(), r.height())
            if not line_rects:
                line_rects = [fallback_rect]

            # ── 可用高度：普通段受「下方 x 重叠段」限制；浮动段放开到页底 ──
            if is_floating:
                avail_h = max(0.0, float(h) - r.y() - _SEG_GAP)
            else:
                avail_h = max(
                    0.0,
                    next_y_by_seg[id(seg)] - origin.y() - r.y() - _SEG_GAP,
                )

            # ── 可用宽度（横向碰撞）：行宽 = 右侧边界 − 段左 − GAP ──
            # 右侧没有碰撞段/没到页边时，译文可占满可用空间，不再被原文行宽
            # 自限换行（数字截断 bug 根因修复）。浮动段沿用原位置碰撞宽度。
            avail_w = max(
                0.0,
                next_x_by_seg[id(seg)] - origin.x() - r.x() - _SEG_GAP,
            )
            if avail_w < _SEG_MIN_WIDTH:
                continue

            # ── 字号 × 行宽 二维适配（字号优先：不第一时间压缩）──
            # 设计：base 字号 + 横向碰撞可用宽度排版 → text_h：
            #   - text_h ≤ avail_h → 直接用（放得下，不缩字号）；
            #   - text_h > avail_h → 译文过长 → 才缩字号（档位 + 下限 _FIT_MIN_FONT_PX=9）；
            #   - 兜底：全部档放不下 → 强制缩到 9，宁轻微溢出/裁切。
            _FIT_SCALES = (1.0, 0.9, 0.8, 0.7, 0.65, 0.6)

            font = _cover_font(text, base_px)
            tl, draw_rects, text_h = self._layout_flow(
                text, font, line_rects, fallback_rect, line_width=avail_w, base_x=r.x()
            )
            font_px = base_px
            if text_h > avail_h + 1.0:
                # 译文过长：缩字号（行宽保持碰撞可用宽度，每档尝试）
                for scale in _FIT_SCALES[1:]:
                    cand = max(base_px * scale, _FIT_MIN_FONT_PX)
                    font = _cover_font(text, cand)
                    tl, draw_rects, text_h = self._layout_flow(
                        text, font, line_rects, fallback_rect, line_width=avail_w, base_x=r.x()
                    )
                    if text_h <= avail_h + 1.0 or cand <= _FIT_MIN_FONT_PX + 0.5:
                        font_px = cand
                        break
                else:
                    # 兜底：全部字号档放不下 → 强制缩到下限，宁轻微溢出/裁切
                    cand = min(_FIT_MIN_FONT_PX, base_px)
                    font = _cover_font(text, cand)
                    tl, draw_rects, text_h = self._layout_flow(
                        text, font, line_rects, fallback_rect, line_width=avail_w, base_x=r.x()
                    )
                    font_px = cand

            # ── 段级白底高度：覆盖整段（含未译文行）──
            orig_h = sum(h for (_lx, _ly, _w, h) in line_rects)
            box_h = max(orig_h, text_h)
            # 防御性封顶：不可超过本段到下方 x 重叠段的距离
            box_h = min(box_h, max(r.height(), avail_h))
            if is_floating:
                # 浮动段（用户拖出查看）：垂直放开到自身白底，不被碰撞边界裁切
                max_bottom = r.y() + box_h + 2 * _COVER_PAD
            else:
                max_bottom = min(
                    r.y() + box_h + 2 * _COVER_PAD,
                    next_y_by_seg[id(seg)] - origin.y(),
                )
            max_bottom = max(0.0, max_bottom)

            # ── 横向 clip：本段左侧 → 横向碰撞右边界（next_x + pad）──
            # 行宽已是碰撞可用宽度，clip 必须同步放开，否则译文/白底被裁回段宽。
            x0 = max(0.0, r.x() - _COVER_PAD)
            x1 = min(float(w), next_x_by_seg[id(seg)] - origin.x() + _COVER_PAD)
            clip_w = max(0.0, x1 - x0)
            if clip_w <= 0.0 or max_bottom <= 0.0:
                continue
            p.save()
            p.setClipRect(QRectF(x0, 0.0, clip_w, max_bottom))

            # ── 段级不透明白底：覆盖到横向碰撞右边界（含未译原文行 + 可用空白）──
            # 宽度 = 碰撞可用宽度 + GAP + pad，让译文段视觉上「撑满」可用列宽。
            bg_w = min(float(w), next_x_by_seg[id(seg)] - origin.x()) - r.x()
            p.fillRect(
                QRectF(r.x() - _COVER_PAD, r.y() - _COVER_PAD,
                       bg_w + 2 * _COVER_PAD, box_h + 2 * _COVER_PAD),
                _COVER_BG,
            )

            # ── 译文行：每行独立白底 + 译文分片 ──
            # ★ 关键修复：drawText 前必须 setFont，否则 painter 默认字体导致
            #   所有译文「均码变小」。
            #
            # 对齐用 AlignVCenter（而非 AlignTop）：CJK 经 0.85 视觉补偿后比原
            # 文字号略小，AlignTop 会让译文贴上行顶 → 行底留白。VCenter 让译文
            # 在原始行 bbox 中垂直居中，标题/正文都更像原版排版。
            _ALIGN = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            p.setFont(font)
            for i, (lx, ly, lw, lh) in enumerate(draw_rects):
                if ly + lh <= 0 or ly >= max_bottom:
                    continue
                line = tl.lineAt(i)
                if not line.isValid():
                    continue
                p.fillRect(
                    QRectF(lx - _COVER_PAD, ly - _COVER_PAD,
                           lw + 2 * _COVER_PAD, lh + 2 * _COVER_PAD),
                    _COVER_BG,
                )
                start = line.textStart()
                line_text = text[start:start + line.textLength()]
                if line_text:
                    p.drawText(
                        QRectF(lx, ly, lw, lh),
                        _ALIGN,
                        line_text,
                    )

            # ── 已译段译文较短：剩余原文行用「空白不透明白底」遮盖 ──
            # 用户需求：不要用原文英文补绘（会造成"我到底在看哪一段"的误会），
            # 白底精确覆盖原文即可；未翻译段（display_text 为 None）走主循环
            # 整段原文显示，不受本分支影响。
            if (
                getattr(seg, "line_texts", None)
                and len(draw_rects) < len(line_rects)
            ):
                # 从译文最后一行底 + _LINE_GAP 起作游标（行间碰撞消除同主循环）
                fb_cursor = (
                    draw_rects[-1][1] + draw_rects[-1][3] + _LINE_GAP
                    if draw_rects else r.y()
                )
                for i in range(len(draw_rects), len(line_rects)):
                    if i >= len(seg.line_texts):
                        break
                    lx, _ly0, lw, _lh0 = line_rects[i]
                    lh = max(_lh0, 1.0)
                    ly = max(_ly0, fb_cursor)
                    if ly + lh <= 0 or ly >= max_bottom:
                        continue
                    # 只画不透明白底遮盖原文行，不绘制任何文字
                    p.fillRect(
                        QRectF(lx - _COVER_PAD, ly - _COVER_PAD,
                               lw + 2 * _COVER_PAD, lh + 2 * _COVER_PAD),
                        _COVER_BG,
                    )
                    fb_cursor = ly + lh + _LINE_GAP
            p.restore()

        p.end()
        return pix

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 覆盖层（粗糙翻译白底黑字）先画，选区高亮叠在上面
        self._draw_cover(painter)

        if self._highlights:
            painter.setBrush(QColor(0, 120, 255, 50))
            painter.setPen(Qt.NoPen)
            for rect in self._highlights:
                expanded = rect.adjusted(-1, -1, 1, 1)
                painter.drawRoundedRect(expanded, 2, 2)

        painter.end()


class FloatingToolbar(QWidget):
    """浮动工具栏 — 选中文本后弹出，显示在选区上方。

    提供：复制 / 翻译 / 搜索 / 关闭。
    固定暖象牙配色（不随主题），在任何 PDF 背景下都清晰可读。
    """

    # ── 固定配色（不依赖主题，保证明暗背景下都可读）──
    BG = "#f7ecd2"        # 暖象牙底色
    BG_HOVER = "#eddcae"  # 按钮悬浮
    BG_PRESS = "#dfc98e"  # 按钮按下
    BORDER = "#d4a853"    # 金色描边
    FG = "#2c2416"        # 深墨色文字
    CLOSE_FG = "#8a5a3a"  # 关闭按钮文字
    CLOSE_HOVER = "#f0d2c8"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        # 关键：QSS 对 QWidget 本体的 background/border/border-radius
        # 必须设置 WA_StyledBackground 才会生效（否则背景透明、圆角丢失）
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.setStyleSheet(f"""
            FloatingToolbar {{
                background-color: {self.BG};
                border: 1.5px solid {self.BORDER};
                border-radius: 8px;
            }}
            FloatingToolbar QPushButton {{
                background-color: transparent;
                color: {self.FG};
                border: none;
                border-radius: 5px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
            }}
            FloatingToolbar QPushButton:hover {{
                background-color: {self.BG_HOVER};
            }}
            FloatingToolbar QPushButton:pressed {{
                background-color: {self.BG_PRESS};
            }}
            FloatingToolbar QPushButton#closeBtn {{
                padding: 5px 9px;
                color: {self.CLOSE_FG};
                font-size: 13px;
            }}
            FloatingToolbar QPushButton#closeBtn:hover {{
                background-color: {self.CLOSE_HOVER};
                color: #8a2a2a;
            }}
        """)

        # 柔和浮起感
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14)
        shadow.setColor(QColor(0, 0, 0, 70))
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self.copy_btn = QPushButton(" 复制")
        self.copy_btn.setIcon(svg_icon("copy", self.FG, 14))
        self.copy_btn.setToolTip("复制选中文本到剪贴板")
        layout.addWidget(self.copy_btn)

        self.translate_btn = QPushButton(" 翻译")
        self.translate_btn.setIcon(svg_icon("translate", self.FG, 14))
        self.translate_btn.setToolTip("把选中文本发到即时翻译")
        layout.addWidget(self.translate_btn)

        self.search_btn = QPushButton(" 搜索")
        self.search_btn.setIcon(svg_icon("search", self.FG, 14))
        self.search_btn.setToolTip("在 Google Scholar 中搜索")
        layout.addWidget(self.search_btn)

        # 分隔线
        sep = QWidget()
        sep.setAttribute(Qt.WA_StyledBackground, True)
        sep.setFixedSize(1, 16)
        sep.setStyleSheet("background-color: #cbb27a;")
        layout.addWidget(sep)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setToolTip("取消选择")
        self.close_btn.setFixedWidth(32)
        layout.addWidget(self.close_btn)

        # 复制反馈恢复定时器（成员持有，避免 singleShot 绑方法被 GC）
        self._copy_reset_timer = QTimer(self)
        self._copy_reset_timer.setSingleShot(True)
        self._copy_reset_timer.setInterval(1200)
        self._copy_reset_timer.timeout.connect(self.reset_copy)

        self.setFixedSize(self.sizeHint())

    def show_copied(self) -> None:
        """复制成功反馈：切换为「已复制」，1.2s 后自动恢复"""
        self.copy_btn.setIcon(svg_icon("check", self.FG, 14))
        self.copy_btn.setText(" 已复制")
        self._copy_reset_timer.start()

    def reset_copy(self) -> None:
        self.copy_btn.setIcon(svg_icon("copy", self.FG, 14))
        self.copy_btn.setText(" 复制")


class Toast(QLabel):
    """轻量提示气泡：深底浅字、圆角、淡入淡出后自动消失（如「已复制」）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background-color: rgba(46, 36, 20, 235);"
            "color: #f5e6c8;"
            "border: 1px solid #d4a853;"
            "border-radius: 6px;"
            "padding: 5px 14px;"
            "font-size: 12px;"
            "font-weight: 500;"
        )
        self.hide()
        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(0.0)
        self.setGraphicsEffect(self._fade)
        self._anim = QPropertyAnimation(self._fade, b"opacity", self)
        self._anim.finished.connect(self._on_anim_finished)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(1500)
        self._hide_timer.timeout.connect(self._fade_out)
        self._fading_out = False

    def show_toast(self, text: str, pos: QPoint) -> None:
        """显示气泡：淡入 → 停留 1.5s → 淡出隐藏。"""
        self.setText(text)
        self.adjustSize()
        self.move(pos)
        self._anim.stop()
        self._hide_timer.stop()
        self._fading_out = False
        self._fade.setOpacity(0.0)
        self.show()
        self.raise_()
        self._anim.setDuration(150)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._hide_timer.start()

    def _fade_out(self) -> None:
        self._anim.stop()
        self._fading_out = True
        self._anim.setDuration(250)
        self._anim.setStartValue(self._fade.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_finished(self) -> None:
        if self._fading_out:
            self.hide()
            self._fading_out = False