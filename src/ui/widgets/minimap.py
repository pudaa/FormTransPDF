"""
缩略图导航面板 — 类 VS Code minimap

半透明覆盖在 PDF 查看器右上方，渲染全部页面缩略图，
可拖拽的视口指示器，点击跳转，明暗双主题适配。

Design: "Quiet Navigator" — 克制的琥珀/青铜指示器，柔光半透明底
"""

from __future__ import annotations

try:
    from PySide6 import shiboken6  # pip 安装的 PySide6 通常在此
except ImportError:  # conda 安装的 PySide6 把 shiboken6 作为顶层包
    import shiboken6
from PySide6.QtCore import QObject, Qt, QRect, QEasingCurve, QPropertyAnimation, QSize, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPen,
    QBrush,
    QMouseEvent,
    QPaintEvent,
    QPixmap,
    QImage,
    QEnterEvent,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from src.ui.base.theme import theme_manager, ThemePalette, ThemeMode


# ═══════════════════════════════════════════════════════════
# MinimapPanel
# ═══════════════════════════════════════════════════════════

class MinimapPanel(QWidget):
    """半透明 PDF 缩略图导航面板 — 双主题自适应。

    Signals:
        page_clicked(int):      点击缩略图跳转页 (0-based)
        viewport_dragged(float): 拖拽视口指示器 → vertical ratio (0~1)
    """

    THUMB_SCALE = 0.16        # 生成缩略图的分辨率系数（0.10 → 0.16：窄版页面更宽，减少左右留白）
    PANEL_WIDTH = 100
    MIN_PAGE_HEIGHT = 6
    PAD = 5             # 内容上/下留白
    PAGE_GAP = 1        # 页间距
    DRAG_THRESHOLD = 4  # 按下后移动超过该像素数即视为拖拽
    EDGE_SCROLL_ZONE = 24      # 拖拽时光标贴近上下边缘则自动滚动
    EDGE_SCROLL_INTERVAL = 30  # 边缘自动滚动周期 (ms)
    EDGE_SCROLL_STEP = 16      # 每次推进的像素

    page_clicked = Signal(int)
    viewport_dragged = Signal(float)       # 垂直比例 (0~1)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumbnails: list[QPixmap] = []
        self._page_count: int = 0
        self._page_pixmaps: list[QPixmap] = []   # 按面板宽度缩放后的每页图
        self._page_offsets: list[int] = []       # 每页内容坐标 y（未减滚动偏移）
        self._content_height: int = 0            # 内容总高（含底部留白）
        self._scroll_offset: int = 0             # 内容垂直滚动偏移
        self._max_scroll: int = 0                # 最大滚动偏移
        self._visible_range: tuple[float, float] = (0.0, 0.0)
        self._hovered_page: int = -1
        self._pressed: bool = False
        self._press_y: int = 0
        self._drag_started: bool = False
        self._dragging: bool = False
        self._drag_y: int = 0
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(self.EDGE_SCROLL_INTERVAL)
        self._edge_timer.timeout.connect(self._on_edge_scroll_tick)
        self._opacity_base: int = 160       # 基础不透明度
        self._fade_anim: QPropertyAnimation | None = None

        # 透明度效果 — 用于淡入淡出动画
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self.setFixedWidth(self.PANEL_WIDTH)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 始终 "可见"，但默认 opacity=0（透明不可交互）
        # 隐藏时透传鼠标事件：避免其 100px 宽区域成为 PDF/工具栏的点击盲区
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.show()

    # ── 公开方法 ────────────────────────────────────────────

    def begin_load(self, page_count: int, sample_size: QSize | None = None) -> None:
        """占位加载：先按首页纵横比铺白底占位图，缩略图随后增量填充。

        大文档（数百页）同步渲染全部缩略图会长时间阻塞主线程，
        改为「占位先行 + ThumbnailGenerator 分批回填」。
        """
        self._page_count = page_count
        self._scroll_offset = 0
        self._visible_range = (0.0, 0.0)
        thumb_w = max(self.width() - 8, 10)
        if sample_size is not None and sample_size.width() > 0:
            h = max(int(thumb_w * sample_size.height() / sample_size.width()),
                    self.MIN_PAGE_HEIGHT)
        else:
            h = 90
        placeholder = QPixmap(thumb_w, h)
        placeholder.fill(Qt.GlobalColor.white)
        self._thumbnails = [placeholder.copy() for _ in range(page_count)]
        self.refresh_geometry()
        self.update()

    def set_thumbnail(self, index: int, pixmap: QPixmap) -> None:
        """增量回填单页缩略图（占位纵横比与真实渲染一致，偏移无需重算）。"""
        if not (0 <= index < len(self._thumbnails)):
            return
        self._thumbnails[index] = pixmap
        thumb_w = max(self.width() - 8, 10)
        slot_h = max(pixmap.height(), self.MIN_PAGE_HEIGHT)
        if index < len(self._page_pixmaps):
            self._page_pixmaps[index] = pixmap.scaled(
                thumb_w, slot_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.update()

    @property
    def page_count(self) -> int:
        return self._page_count

    def load_document(self, page_count: int, thumbnails: list[QPixmap]) -> None:
        self._page_count = page_count
        self._thumbnails = thumbnails
        self._scroll_offset = 0
        self._visible_range = (0.0, 0.0)
        self.refresh_geometry()
        self.update()

    def refresh_geometry(self) -> None:
        """按面板宽度重算每页绘制尺寸/偏移与面板高度（槽位=绘制高度，无浪费）。"""
        if not self._thumbnails:
            return
        thumb_w = self.width() - 8  # 左右内边距各 4px（原 7px，调小让缩略图更宽）
        pixmaps: list[QPixmap] = []
        offsets: list[int] = []
        cy = self.PAD
        for thumb in self._thumbnails:
            slot_h = max(thumb.height(), self.MIN_PAGE_HEIGHT)
            scaled = thumb.scaled(
                thumb_w, slot_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmaps.append(scaled)
            offsets.append(cy)
            cy += scaled.height() + self.PAGE_GAP
        self._page_pixmaps = pixmaps
        self._page_offsets = offsets
        self._content_height = cy
        max_h = (self.parent().height() if self.parent() else 600) - 16
        panel_h = min(self._content_height + self.PAD, max_h)
        self.setFixedHeight(panel_h)
        self._max_scroll = max(0, self._content_height + self.PAD - panel_h)
        self._scroll_offset = min(self._scroll_offset, self._max_scroll)

    def set_visible_range(self, start_ratio: float, end_ratio: float) -> None:
        self._visible_range = (start_ratio, end_ratio)
        self._ensure_indicator_visible()

    def _ensure_indicator_visible(self) -> None:
        """内容超高时自动滚动，使视口指示器保持完整可见（拖拽/滚轮滚动时跟随）。"""
        if self._max_scroll <= 0 or self._content_height <= 0:
            return
        y1 = int(self._visible_range[0] * self._content_height)
        y2 = int(self._visible_range[1] * self._content_height)
        vis_bot = self._scroll_offset + self.height()
        if y1 < self._scroll_offset:
            self._scroll_offset = max(0, min(self._max_scroll, y1))
        elif y2 > vis_bot:
            self._scroll_offset = max(0, min(self._max_scroll, y2 - self.height()))
        self.update()

    def toggle(self) -> None:
        """带动画的显示/隐藏切换"""
        # 防御：minimap 可能随父 viewer 重建而销毁（布局切换场景）
        if not self._effect_alive():
            return
        # 停止正在进行的动画
        if self._fade_anim is not None and self._fade_anim.state() == QPropertyAnimation.State.Running:
            self._fade_anim.stop()

        currently_visible = self._opacity_effect.opacity() > 0.01
        target_opacity = 0.0 if currently_visible else 1.0

        # 隐藏时透传鼠标事件（不拦截其区域点击）
        self.setAttribute(Qt.WA_TransparentForMouseEvents, target_opacity == 0.0)

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_anim.setDuration(220)
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(target_opacity)
        self._fade_anim.setEasingCurve(
            QEasingCurve.Type.OutCubic if target_opacity > 0.5
            else QEasingCurve.Type.InCubic
        )
        self._fade_anim.start()

    def isVisible(self) -> bool:
        """重写：以 opacity 为准判断可见性。

        注意：minimap 是 viewer 的子对象，布局切换（mono↔dual）重建 viewer 时
        会随父对象一起销毁；此时本方法可能被 Qt 事件路径（resize/事件过滤）调用，
        必须防御已删除的 C++ 对象，否则抛 RuntimeError 拖垮应用。
        """
        if not self._effect_alive():
            return False
        return self._opacity_effect.opacity() > 0.01

    # ── 内部 ────────────────────────────────────────────────

    def _effect_alive(self) -> bool:
        """opacity effect 是否仍持有有效的 C++ 对象。"""
        return (
            self._opacity_effect is not None
            and shiboken6.isValid(self._opacity_effect)
        )

    @property
    def _tp(self) -> ThemePalette:
        return theme_manager.palette

    def _panel_colors(self) -> dict:
        """根据当前主题返回 minimap 配色"""
        tp = self._tp
        is_dark = tp.mode == ThemeMode.DARK
        return {
            "bg": QColor(24, 24, 30, self._opacity_base) if is_dark
                  else QColor(220, 212, 195, self._opacity_base),
            "border": QColor(60, 60, 70, 140) if is_dark
                      else QColor(180, 170, 155, 160),
            "viewport": QColor(tp.accent.red(), tp.accent.green(),
                               tp.accent.blue(), 100),
            "viewport_border": tp.accent.lighter(140),
            "hover": QColor(255, 255, 255, 25) if is_dark
                     else QColor(80, 60, 20, 30),
            "page_shadow": QColor(0, 0, 0, 40) if is_dark
                           else QColor(120, 100, 70, 30),
        }

    def _page_at_y(self, y: int) -> int:
        """屏幕 y → 页面索引（考虑滚动偏移）"""
        if not self._page_offsets:
            return -1
        content_y = y + self._scroll_offset
        for i, (off, pix) in enumerate(zip(self._page_offsets, self._page_pixmaps)):
            if off <= content_y < off + pix.height() + self.PAGE_GAP:
                return i
        return -1

    def _viewport_rect(self, colors: dict) -> QRect | None:
        """计算视口指示器的矩形区域（考虑滚动，越界时在边缘露一线）"""
        if self._content_height <= 0 or self._visible_range[1] <= 0:
            return None
        w = self.width()
        h_panel = self.height()
        y1 = int(self._visible_range[0] * self._content_height) - self._scroll_offset
        y2 = int(self._visible_range[1] * self._content_height) - self._scroll_offset
        if y2 <= 0:            # 完全在当前可视区上方
            return QRect(2, 0, w - 4, 4)
        if y1 >= h_panel:      # 完全在下方
            return QRect(2, h_panel - 4, w - 4, 4)
        y1 = max(0, y1)
        y2 = min(h_panel, y2)
        h = max(y2 - y1, 4)
        if h >= h_panel:
            return QRect(2, 0, w - 4, h_panel)
        return QRect(2, y1, w - 4, h)

    def _scroll_ratio_from_y(self, y: int) -> float:
        """屏幕 y → 滚动比例（考虑滚动偏移；0~1，视口中心对齐光标）"""
        if self._content_height <= 0:
            return 0.0
        ratio = (y + self._scroll_offset) / self._content_height
        return max(0.0, min(1.0, ratio))

    # ── 事件 ────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent | None) -> None:
        if not self._page_pixmaps:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setClipRect(self.rect())   # 滚动时内容不溢出
        c = self._panel_colors()
        w = self.width()

        # 柔光背景 + 圆角
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(c["bg"]))
        p.drawRoundedRect(self.rect(), 6, 6)

        # 内边框
        pen = QPen(c["border"], 1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)

        # 缩略图（仅绘制可见区域）
        for i, pix in enumerate(self._page_pixmaps):
            off = self._page_offsets[i] - self._scroll_offset
            if off + pix.height() < 0 or off > self.height():
                continue
            px = (w - pix.width()) // 2

            # 页阴影（微妙的深度暗示）
            p.fillRect(px + 1, off + 1, pix.width(), pix.height(), c["page_shadow"])
            p.drawPixmap(px, off, pix)

            # 悬停发光
            if i == self._hovered_page:
                p.fillRect(px, off, pix.width(), pix.height(), c["hover"])

        # 视口指示器
        vp_rect = self._viewport_rect(c)
        if vp_rect:
            # 半透明填充
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(c["viewport"]))
            p.drawRoundedRect(vp_rect, 3, 3)

            # 边框
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(c["viewport_border"], 1))
            p.drawRoundedRect(vp_rect, 3, 3)

        p.end()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._pressed = True
        self._press_y = int(event.position().y())
        self._drag_started = False
        self._dragging = False

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        y = int(event.position().y())

        if self._dragging:
            # 拖拽中：连续滚动（视口中心跟随光标）
            self._drag_y = y
            self.viewport_dragged.emit(self._scroll_ratio_from_y(y))
            self._update_edge_timer(y)
            return

        if self._pressed and not self._drag_started and abs(y - self._press_y) >= self.DRAG_THRESHOLD:
            # 按下后移动超过阈值 → 进入拖拽模式（无需先点击，按下即拖）
            self._drag_started = True
            self._dragging = True
            self._drag_y = y
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.viewport_dragged.emit(self._scroll_ratio_from_y(y))
            self._update_edge_timer(y)
            return

        page = self._page_at_y(y)
        if page != self._hovered_page:
            self._hovered_page = page
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._pressed = False
        self._edge_timer.stop()
        if self._dragging:
            self._dragging = False
            self._drag_started = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.update()
            return
        # 未拖拽 → 视为点击：精确跳转到该页
        page = self._page_at_y(self._press_y)
        if 0 <= page < self._page_count:
            self.page_clicked.emit(page)

    def _update_edge_timer(self, y: int) -> None:
        """拖拽时光标贴近面板上下边缘 → 启动定时自动滚动（可到达所有页）。"""
        h = self.height()
        if self._max_scroll <= 0:
            self._edge_timer.stop()
            return
        if y >= h - self.EDGE_SCROLL_ZONE and self._scroll_offset < self._max_scroll:
            self._edge_timer.start()
        elif y <= self.EDGE_SCROLL_ZONE and self._scroll_offset > 0:
            self._edge_timer.start()
        else:
            self._edge_timer.stop()

    def _on_edge_scroll_tick(self) -> None:
        """边缘自动滚动：光标保持在边缘时持续推进内容与滚动比例。"""
        if not self._dragging:
            self._edge_timer.stop()
            return
        y = self._drag_y
        h = self.height()
        if y >= h - self.EDGE_SCROLL_ZONE:
            if self._scroll_offset >= self._max_scroll:
                self._edge_timer.stop()
                return
            self._scroll_offset = min(
                self._max_scroll, self._scroll_offset + self.EDGE_SCROLL_STEP
            )
        elif y <= self.EDGE_SCROLL_ZONE:
            if self._scroll_offset <= 0:
                self._edge_timer.stop()
                return
            self._scroll_offset = max(0, self._scroll_offset - self.EDGE_SCROLL_STEP)
        else:
            self._edge_timer.stop()
            return
        self.update()
        self.viewport_dragged.emit(self._scroll_ratio_from_y(y))

    def enterEvent(self, event: QEnterEvent | None) -> None:
        self._opacity_base = 210
        self.update()

    def leaveEvent(self, event) -> None:
        self._opacity_base = 140
        self._hovered_page = -1
        if not self._dragging:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()

    def wheelEvent(self, event) -> None:
        # 滚轮滚动缩略图内容（内容超高时可到达所有页面）
        if self._max_scroll <= 0:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta:
            step = max(24, abs(delta) // 4)
            self._scroll_offset = max(
                0, min(self._max_scroll, self._scroll_offset - (step if delta > 0 else -step))
            )
            self.update()
            event.accept()
        else:
            event.ignore()


# ═══════════════════════════════════════════════════════════
# 缩略图生成工具
# ═══════════════════════════════════════════════════════════

def _render_page_thumb(doc: QPdfDocument, index: int, thumb_scale: float) -> QPixmap | None:
    """渲染单页缩略图（白底合成）。返回 None 表示渲染失败。"""
    size = doc.pagePointSize(index)
    thumb_w = max(int(size.width() * thumb_scale), 20)
    thumb_h = max(int(size.height() * thumb_scale), 14)
    image = doc.render(index, QSize(thumb_w, thumb_h))
    if image.isNull():
        return None
    # 合成到与渲染图同尺寸的白色底板上（无边框，避免纵向空间浪费）
    canvas = QImage(image.size(), QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#ffffff"))
    p = QPainter(canvas)
    p.drawImage(0, 0, image)
    p.end()
    return QPixmap.fromImage(canvas)


def generate_thumbnails(doc: QPdfDocument, page_count: int, thumb_scale: float = 0.10) -> list[QPixmap]:
    """同步生成全部页面缩略图 — 仅适合小文档；大文档用 ThumbnailGenerator。"""
    from PySide6.QtGui import QPainter
    thumbnails: list[QPixmap] = []
    for i in range(page_count):
        pix = _render_page_thumb(doc, i, thumb_scale)
        if pix is not None:
            thumbnails.append(pix)
    return thumbnails


class ThumbnailGenerator(QObject):
    """大文档缩略图分批生成器 — 主线程 QTimer 分片，UI 不阻塞。

    旧实现对数百页文档一次性同步 doc.render()，主线程阻塞可达数十秒
    （应用"未响应"假死）。本生成器每 interval_ms 渲染 batch_size 页，
    通过 batch_ready(start_index, pixmaps) 增量回填面板。
    """

    batch_ready = Signal(int, list)   # start_index, [QPixmap, ...]
    finished = Signal()

    def __init__(
        self,
        doc: QPdfDocument,
        page_count: int,
        thumb_scale: float,
        batch_size: int = 6,
        interval_ms: int = 25,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._doc = doc
        self._count = page_count
        self._scale = thumb_scale
        self._batch = max(1, batch_size)
        self._next = 0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._next = 0
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def done(self) -> bool:
        return self._next >= self._count

    def _tick(self) -> None:
        if self._next >= self._count:
            self._timer.stop()
            self.finished.emit()
            return
        start = self._next
        batch: list[QPixmap] = []
        for _ in range(self._batch):
            if self._next >= self._count:
                break
            pix = _render_page_thumb(self._doc, self._next, self._scale)
            if pix is not None:
                batch.append(pix)
            self._next += 1
        if batch:
            self.batch_ready.emit(start, batch)
        if self._next >= self._count:
            self._timer.stop()
            self.finished.emit()
