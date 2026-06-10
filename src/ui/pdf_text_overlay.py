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
from PySide6.QtCore import Qt, QRectF, QPoint, QSize, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QIcon


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
        """设置高亮矩形列表"""
        self._highlights = rects
        self.update()

        # 显示/隐藏工具栏
        if rects:
            self._show_toolbar_at_selection(rects)
        else:
            self._toolbar.hide()

    def clear_highlights(self):
        """清除所有高亮和工具栏"""
        self._highlights.clear()
        self._toolbar.hide()
        self.update()

    def _show_toolbar_at_selection(self, rects: List[QRectF]):
        """在选区上方显示浮动工具栏"""
        if not rects:
            return

        # 计算选区的包围矩形
        united = rects[0]
        for r in rects[1:]:
            united = united.united(r)

        # 工具栏位置：选区上方居中
        toolbar_w = self._toolbar.sizeHint().width()
        toolbar_h = self._toolbar.sizeHint().height()
        x = int(united.center().x() - toolbar_w / 2)
        y = int(united.top() - toolbar_h - 8)  # 上方 8px 间距

        # 边界检查：不要超出 viewport
        vp_w = self.width()
        x = max(8, min(x, vp_w - toolbar_w - 8))
        y = max(8, y)  # 顶部留出边距

        self._toolbar.move(x, y)
        self._toolbar.show()
        self._toolbar.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 绘制已选高亮（唯一必须的可视反馈）
        if self._highlights:
            painter.setBrush(QColor(0, 120, 255, 50))  # 蓝色半透明
            painter.setPen(Qt.NoPen)
            for rect in self._highlights:
                # 稍微扩展一点，让高亮更明显
                expanded = rect.adjusted(-1, -1, 1, 1)
                painter.drawRoundedRect(expanded, 2, 2)

        painter.end()


class FloatingToolbar(QWidget):
    """浮动工具栏 — 选中后弹出"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.ToolTip)  # 不接收焦点，不抢夺事件
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        # 样式
        self.setStyleSheet("""
            FloatingToolbar {
                background-color: #2d2d2d;
                border-radius: 8px;
                padding: 4px;
            }
            QPushButton {
                background-color: transparent;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #404040;
            }
            QPushButton:pressed {
                background-color: #505050;
            }
        """)

        # 阴影效果
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        # 布局
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # 复制按钮
        self.copy_btn = QPushButton("\U0001F4CB 复制")
        self.copy_btn.setToolTip("复制选中文本 (Ctrl+C)")
        layout.addWidget(self.copy_btn)

        # 搜索按钮
        self.search_btn = QPushButton("\U0001F50D 搜索")
        self.search_btn.setToolTip("搜索选中文本")
        layout.addWidget(self.search_btn)

        # 高亮按钮
        self.highlight_btn = QPushButton("\U0001F58D\uFE0F 标记")
        self.highlight_btn.setToolTip("添加永久高亮")
        layout.addWidget(self.highlight_btn)

        # 关闭按钮
        self.close_btn = QPushButton("\u2715")
        self.close_btn.setToolTip("关闭")
        self.close_btn.setFixedWidth(28)
        layout.addWidget(self.close_btn)

        self.setFixedSize(self.sizeHint())

        # 入场动画
        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def showEvent(self, event):
        """重写 showEvent 实现淡入动画"""
        self.setWindowOpacity(0.0)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hide(self):
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(super().hide)
        self._anim.start()
