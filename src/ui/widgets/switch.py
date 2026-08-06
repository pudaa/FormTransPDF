"""
Switch — 自定义开关组件（已适配 FormTransPDF 主题）。

开状态：主题强调色（暗色金 / 亮色青铜）；关状态：主题禁用灰；
滑块为白色。颜色实时取自 theme_manager，主题切换后调用 refresh_theme() 重绘。
"""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from src.ui.theme import theme_manager


class Switch(QWidget):
    """自定义开关组件。"""

    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None,
                 width: int = 50, height: int = 26) -> None:
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._checked = False
        self._hovered = False
        self._slider_position = self._off_position()
        self.setMouseTracking(True)

        self._anim = QPropertyAnimation(self, b"slider_position")
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    # ── slider_position 属性（由动画驱动）───────────────────

    def _slider_size(self) -> int:
        return self.height() - 8

    def _off_position(self) -> int:
        margin = (self.height() - self._slider_size()) / 2.0
        return int(margin)

    def _on_position(self) -> int:
        return self.width() - self._slider_size() - self._off_position()

    def _get_slider_position(self) -> float:
        return self._slider_position

    def _set_slider_position(self, pos: float) -> None:
        self._slider_position = pos
        self.update()

    slider_position = Property(float, _get_slider_position, _set_slider_position)

    # ── 状态 ──────────────────────────────────────────────

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if self._checked == checked:
            return
        self._checked = checked
        self._animate_to(checked)
        self.toggled.emit(checked)

    def toggle(self) -> None:
        self.setChecked(not self._checked)

    def _animate_to(self, checked: bool) -> None:
        end = self._on_position() if checked else self._off_position()
        self._anim.stop()
        self._anim.setStartValue(self._slider_position)
        self._anim.setEndValue(end)
        self._anim.start()

    # ── 主题 / 事件 ───────────────────────────────────────

    def refresh_theme(self) -> None:
        """主题切换后重绘。"""
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tp = theme_manager.palette

        # 轨道颜色：开=强调色，关=禁用灰，hover 轻微提亮，禁用=弱化
        if not self.isEnabled():
            track = tp.divider
        elif self._checked:
            track = tp.accent_hover if self._hovered else tp.accent
        else:
            track = tp.text_secondary if self._hovered else tp.text_disabled

        radius = self.height() / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), radius, radius)

        # 滑块（白色）
        size = self._slider_size()
        y = (self.height() - size) / 2.0
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(int(self._slider_position), int(y), size, size)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()
        else:
            super().mousePressEvent(event)