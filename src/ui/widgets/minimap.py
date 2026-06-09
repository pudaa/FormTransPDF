"""
缩略图导航面板 — 类 VS Code minimap

半透明覆盖在 PDF 查看器右上方，渲染全部页面缩略图，
可拖拽的视口指示器，点击跳转，明暗双主题适配。

Design: "Quiet Navigator" — 克制的琥珀/青铜指示器，柔光半透明底
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRect, QEasingCurve, QPropertyAnimation, pyqtSignal
from PyQt6.QtGui import (
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
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

from src.ui.theme import theme_manager, ThemePalette, ThemeMode


# ═══════════════════════════════════════════════════════════
# MinimapPanel
# ═══════════════════════════════════════════════════════════

class MinimapPanel(QWidget):
    """半透明 PDF 缩略图导航面板 — 双主题自适应。

    Signals:
        page_clicked(int):      点击缩略图跳转页 (0-based)
        viewport_dragged(float): 拖拽视口指示器 → vertical ratio (0~1)
    """

    THUMB_SCALE = 0.10
    PANEL_WIDTH = 100
    MIN_PAGE_HEIGHT = 6

    page_clicked = pyqtSignal(int)
    viewport_dragged = pyqtSignal(float)       # 垂直比例 (0~1)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumbnails: list[QPixmap] = []
        self._page_count: int = 0
        self._visible_range: tuple[float, float] = (0.0, 0.0)
        self._hovered_page: int = -1
        self._dragging: bool = False
        self._drag_start_y: int = 0
        self._drag_start_ratio: float = 0.0
        self._total_content_h: int = 0
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
        self.show()

    # ── 公开方法 ────────────────────────────────────────────

    def load_document(self, page_count: int, thumbnails: list[QPixmap]) -> None:
        self._page_count = page_count
        self._thumbnails = thumbnails
        self._update_height()
        self.update()

    def set_visible_range(self, start_ratio: float, end_ratio: float) -> None:
        if not self._dragging:
            self._visible_range = (start_ratio, end_ratio)
            self.update()

    def toggle(self) -> None:
        """带动画的显示/隐藏切换"""
        # 停止正在进行的动画
        if self._fade_anim is not None and self._fade_anim.state() == QPropertyAnimation.State.Running:
            self._fade_anim.stop()

        currently_visible = self._opacity_effect.opacity() > 0.01
        target_opacity = 0.0 if currently_visible else 1.0

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
        """重写：以 opacity 为准判断可见性"""
        return self._opacity_effect.opacity() > 0.01

    # ── 内部 ────────────────────────────────────────────────

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

    def _update_height(self) -> None:
        if not self._thumbnails:
            return
        total_h = sum(max(p.height(), self.MIN_PAGE_HEIGHT) + 2
                      for p in self._thumbnails)
        max_h = (self.parent().height() if self.parent() else 600) - 16
        self.setFixedHeight(min(total_h + 10, max_h))

    def _page_at_y(self, y: int) -> int:
        if not self._thumbnails:
            return -1
        cy = 5
        for i, thumb in enumerate(self._thumbnails):
            h = max(thumb.height(), self.MIN_PAGE_HEIGHT)
            if cy <= y < cy + h + 2:
                return i
            cy += h + 2
        return -1

    def _viewport_rect(self, colors: dict) -> QRect | None:
        """计算视口指示器的矩形区域"""
        if self._total_content_h <= 0 or self._visible_range[1] <= 0:
            return None
        w = self.width()
        y1 = int(self._visible_range[0] * self._total_content_h) + 5
        y2 = int(self._visible_range[1] * self._total_content_h) + 5
        return QRect(2, y1, w - 4, max(y2 - y1, 4))

    def _scroll_ratio_from_y(self, y: int) -> float:
        """根据鼠标 Y 坐标计算对应的滚动比例"""
        if self._total_content_h <= 0:
            return 0.0
        indicator_h = max(
            (self._visible_range[1] - self._visible_range[0]) * self._total_content_h,
            4,
        )
        ratio = (y - 5 - indicator_h / 2) / self._total_content_h
        return max(0.0, min(1.0, ratio))

    # ── 事件 ────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent | None) -> None:
        if not self._thumbnails:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self._panel_colors()
        w = self.width()
        thumb_w = w - 14

        # 柔光背景 + 圆角
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(c["bg"]))
        p.drawRoundedRect(self.rect(), 6, 6)

        # 内边框
        pen = QPen(c["border"], 1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)

        # 缩略图
        cy = 5
        for i, thumb in enumerate(self._thumbnails):
            h = max(thumb.height(), self.MIN_PAGE_HEIGHT)
            scaled = thumb.scaled(
                thumb_w, h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            px = (w - scaled.width()) // 2

            # 页阴影（微妙的深度暗示）
            p.fillRect(px + 1, cy + 1, scaled.width(), h, c["page_shadow"])
            p.drawPixmap(px, cy, scaled)

            # 悬停发光
            if i == self._hovered_page:
                p.fillRect(px, cy, scaled.width(), h, c["hover"])

            cy += h + 2

        self._total_content_h = cy - 2

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
        if event is None:
            return
        y = int(event.position().y())
        vp_rect = self._viewport_rect(self._panel_colors())

        if event.button() == Qt.MouseButton.LeftButton:
            if vp_rect and vp_rect.contains(event.pos()):
                # 拖拽视口指示器
                self._dragging = True
                self._drag_start_y = y
                self._drag_start_ratio = self._scroll_ratio_from_y(y)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            else:
                # 点击跳转页面
                page = self._page_at_y(y)
                if 0 <= page < self._page_count:
                    self.page_clicked.emit(page)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        y = int(event.position().y())

        if self._dragging:
            ratio = self._scroll_ratio_from_y(y)
            self.viewport_dragged.emit(ratio)
        else:
            page = self._page_at_y(y)
            if page != self._hovered_page:
                self._hovered_page = page
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)

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
        # 滚轮事件透传给父组件（PDF 查看器）
        event.ignore()


# ═══════════════════════════════════════════════════════════
# 缩略图生成工具
# ═══════════════════════════════════════════════════════════

def generate_thumbnails(doc, page_count: int, thumb_scale: float = 0.10) -> list[QPixmap]:
    """从 PyMuPDF Document 生成全部页面的缩略图 QPixmap 列表"""
    import fitz
    thumbnails: list[QPixmap] = []
    mat = fitz.Matrix(thumb_scale, thumb_scale)
    for i in range(page_count):
        pix = doc[i].get_pixmap(matrix=mat)
        img = QImage(
            pix.samples, pix.width, pix.height, pix.stride,
            QImage.Format.Format_RGB888,
        )
        thumbnails.append(QPixmap.fromImage(img.copy()))
    return thumbnails
