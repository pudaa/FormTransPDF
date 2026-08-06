"""
PDF 文本覆盖层 — 覆盖在 QPdfView viewport 上的透明层。

只负责绘制：
1. 已选中文本的高亮矩形（蓝色半透明）
2. 浮动工具栏（选中后弹出，提供复制、搜索等操作）

所有坐标均为 viewport 相对坐标。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
)
from PySide6.QtCore import Qt, QPoint, QRectF, QTimer, QPropertyAnimation
from PySide6.QtGui import QPainter, QColor

from src.ui.base.icon_factory import svg_icon
from src.ui.base.theme import theme_manager


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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

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