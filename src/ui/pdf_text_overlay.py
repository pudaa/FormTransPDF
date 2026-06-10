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
    QWidget, QHBoxLayout, QPushButton, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor

from src.ui.theme import theme_manager


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
        self._toolbar = FloatingToolbar(self)
        self._toolbar.hide()

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
    """浮动工具栏 — 选中后弹出，显示在选区上方"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        # 确保工具栏有独立的不透明背景
        self.setAutoFillBackground(True)

        # 修复：使用高对比度配色，不依赖主题
        # 暖象牙色背景 + 深墨色文字，在任何主题下都清晰可读
        bg = "#f5e6c8"           # 暖象牙色
        bg_hover = "#e8d4a8"     # 悬浮色
        border_color = "#d4a853"  # 金色边框
        fg = "#2c2416"           # 深墨色文字
        shadow_color = QColor(0, 0, 0, 60)  # 降低阴影透明度

        self.setStyleSheet(f"""
            FloatingToolbar {{
                background-color: {bg};
                border: 1.5px solid {border_color};
                border-radius: 6px;
            }}
            QPushButton {{
                background-color: transparent;
                color: {fg};
                border: none;
                border-radius: 3px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {bg_hover};
            }}
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(8)
        shadow.setColor(shadow_color)
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        self.copy_btn = QPushButton("📋 复制")
        self.copy_btn.setToolTip("复制到剪贴板")
        layout.addWidget(self.copy_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setToolTip("取消选择")
        self.close_btn.setFixedWidth(28)
        layout.addWidget(self.close_btn)

        self.setFixedSize(self.sizeHint())