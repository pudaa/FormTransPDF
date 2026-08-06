"""
文档标签页栏 — 模仿浏览器标签页的多文档切换组件。

特性：
- 每个标签：标题（超出自动省略）+ 关闭按钮
- 宽度计算：natural = clamp(标题宽 + 关闭钮 + 内边距, MIN_TAB_W, MAX_TAB_W)
- 标签过多溢出时：所有标签等宽收缩到 MIN_TAB_W；仍溢出则显示 ‹/› 滚动按钮
- 滚轮横向滚动、激活标签自动滚到可见、主题自适应（refresh_theme）

数据模型：DocumentTab 描述一个打开的文档（源文件 + 译文结果 + 视图状态）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

from src.ui.base.icon_factory import svg_icon
from src.ui.base.theme import theme_manager


@dataclass
class DocumentTab:
    """一个打开的文档（标签页）状态。"""

    title: str                     # 标签显示名
    source_pdf: Path | None        # 源文件（历史结果标签页中为结果文件，用于去重/加载）
    mono_pdf: Path | None = None   # 仅译文
    dual_pdf: Path | None = None   # 双语对照
    view: str = "source"           # "source" | "result"
    has_source: bool = True        # 是否有真实源文件（历史结果标签页为 False）
    source_hash: str = ""          # 源文件内容指纹（复用检测）
    source_bytes_hash: str = ""    # 源文件字节指纹（复用检测兜底）


def _adjust_index(index: int, from_i: int, to_i: int) -> int:
    """标签从 from_i 移动到 to_i（最终位置）后，原活动索引的新位置。"""
    if index == from_i:
        return to_i
    if from_i < index <= to_i:
        return index - 1
    if to_i <= index < from_i:
        return index + 1
    return index


class _TabItem(QFrame):
    """单个文档标签：标题（自动省略）+ 关闭按钮。"""

    def __init__(
        self,
        title: str,
        index: int,
        on_activate,
        on_close,
        on_drag_start,
    ) -> None:
        super().__init__()
        self._full_title = title
        self._index = index
        self._on_activate = on_activate
        self._on_drag_start = on_drag_start
        self._press_pos = QPoint()
        self._drag_active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)
        # 垂直/水平扩展：填满标签行高度与分配宽度，保证可点击区域
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(DocumentTabBar.BAR_HEIGHT - 4)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 4, 0)
        lay.setSpacing(2)

        self._label = QLabel(title)
        self._label.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(self._label, stretch=1)

        self._close_btn = QPushButton()
        self._close_btn.setFixedSize(16, 16)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setToolTip("关闭标签页")
        self._close_btn.setStyleSheet("background: transparent; border: none;")
        self._close_btn.clicked.connect(on_close)
        lay.addWidget(self._close_btn)

        self._elide()

    # ── 内部 ──────────────────────────────────────────────

    def title(self) -> str:
        return self._full_title

    def set_title(self, title: str) -> None:
        self._full_title = title
        self._elide()

    def natural_width(self) -> int:
        """标题自然宽度（受全局 MIN/MAX 约束）。"""
        fm = QFontMetrics(self._label.font())
        text_w = fm.horizontalAdvance(self._full_title)
        w = text_w + 8 + 4 + 16 + 2  # 左右边距 + 关闭钮 + spacing
        return max(DocumentTabBar.MIN_TAB_W, min(w, DocumentTabBar.MAX_TAB_W))

    def refresh_close_icon(self) -> None:
        tp = theme_manager.palette
        self._close_btn.setIcon(svg_icon("close", tp.text_secondary, 12))

    def _elide(self) -> None:
        """按当前宽度省略标题。"""
        fm = QFontMetrics(self._label.font())
        avail = self.width() - (8 + 4 + 16 + 2)
        self._label.setText(
            fm.elidedText(self._full_title, Qt.TextElideMode.ElideRight, max(avail, 10))
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
            self._drag_active = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and not self._drag_active
            and (event.pos() - self._press_pos).manhattanLength() > 6
        ):
            # 超过拖拽阈值：交由标签栏进入拖拽重排模式（标签栏 grabMouse）
            self._drag_active = True
            self._on_drag_start(self._index)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._drag_active:
            # 纯点击（未拖拽）→ 激活
            self._on_activate(self._index)
        super().mouseReleaseEvent(event)


class DocumentTabBar(QWidget):
    """浏览器式文档标签栏。"""

    tab_activated = Signal(int)          # index
    tab_close_requested = Signal(int)    # index
    tabs_reordered = Signal(int, int)    # from_index, to_index（最终位置）

    MIN_TAB_W = 100
    MAX_TAB_W = 240
    BAR_HEIGHT = 30
    TAB_GAP = 4
    SCROLL_STEP = 140

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.BAR_HEIGHT)
        self._items: list[_TabItem] = []
        self._active = -1
        # 拖拽重排状态
        self._drag_started = False
        self._drag_item: _TabItem | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 溢出滚动按钮（仅在标签过多时显示）
        self._left_btn = QPushButton("‹")
        self._right_btn = QPushButton("›")
        for btn in (self._left_btn, self._right_btn):
            btn.setFixedSize(20, self.BAR_HEIGHT)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setVisible(False)
        self._left_btn.clicked.connect(lambda: self._scroll_by(-self.SCROLL_STEP))
        self._right_btn.clicked.connect(lambda: self._scroll_by(self.SCROLL_STEP))

        # 横向滚动区域（无边框透明；widgetResizable 使内容填满视口高度）
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("background: transparent; border: none;")

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._tabs_layout = QHBoxLayout(self._content)
        self._tabs_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs_layout.setSpacing(self.TAB_GAP)
        self._tabs_layout.addStretch(1)
        self._scroll.setWidget(self._content)

        root.addWidget(self._left_btn)
        root.addWidget(self._scroll, stretch=1)
        root.addWidget(self._right_btn)

        # 滚轮 → 横向滚动
        self._scroll.viewport().installEventFilter(self)

        self._apply_styles()

    # ── 公共 API ──────────────────────────────────────────

    def add_tab(self, title: str) -> int:
        """添加标签，返回其 index。"""
        index = len(self._items)
        item = _TabItem(
            title, index, self._on_item_activated, self._on_item_close, self._on_item_drag_start
        )
        self._tabs_layout.insertWidget(index, item)
        self._items.append(item)
        item.refresh_close_icon()
        # 新标签立即应用主题样式（背景/边框/文字色），否则显示为未渲染
        self._apply_styles()
        self._relayout()
        return index

    def remove_tab(self, index: int) -> None:
        """移除标签。"""
        if not (0 <= index < len(self._items)):
            return
        item = self._items.pop(index)
        self._tabs_layout.removeWidget(item)
        item.deleteLater()
        for i, it in enumerate(self._items):
            it._index = i
        self._relayout()

    def set_active(self, index: int) -> None:
        """高亮激活标签，并滚动到可见。index=-1 表示无激活。"""
        if index == self._active:
            return
        self._active = index
        self._apply_styles()
        if 0 <= index < len(self._items):
            self._scroll_to_item(index)

    def set_title(self, index: int, title: str) -> None:
        if 0 <= index < len(self._items):
            self._items[index].set_title(title)
            self._relayout()

    def refresh_theme(self) -> None:
        """主题切换后重建关闭图标与标签样式。"""
        for item in self._items:
            item.refresh_close_icon()
        self._apply_styles()

    def count(self) -> int:
        return len(self._items)

    # ── 内部 ──────────────────────────────────────────────

    def _on_item_activated(self, index: int) -> None:
        self.tab_activated.emit(index)

    def _on_item_close(self) -> None:
        """关闭按钮点击：通过 sender 定位所属标签。"""
        btn = self.sender()
        if btn is None:
            return
        item = btn.parentWidget()
        if isinstance(item, _TabItem) and 0 <= item._index < len(self._items):
            self.tab_close_requested.emit(item._index)

    # ── 拖拽重排 ──────────────────────────────────────────

    def _on_item_drag_start(self, index: int) -> None:
        """标签拖拽开始：抓取鼠标并进入重排模式。"""
        if not (0 <= index < len(self._items)):
            return
        self._drag_started = True
        self._drag_item = self._items[index]
        self.grabMouse()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_started and self._drag_item is not None:
            pos = self._items.index(self._drag_item)
            target = self._hovered_index(event.position().x())
            if target is not None and target != pos:
                self._move_tab_final(pos, target)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_started:
            self._drag_started = False
            self._drag_item = None
            self.unsetCursor()
            self.releaseMouse()
            return
        super().mouseReleaseEvent(event)

    def _hovered_index(self, x: float) -> int | None:
        """按光标 x（标签栏坐标系）返回最近的标签 index。"""
        if not self._items:
            return None
        best, best_d = 0, 10**9
        for i, it in enumerate(self._items):
            cx = it.mapTo(self, it.rect().center()).x()
            d = abs(x - cx)
            if d < best_d:
                best_d, best = d, i
        return best

    def _move_tab_final(self, from_index: int, to_index: int) -> None:
        """将标签移动到 to_index（最终位置，QTabBar.moveTab 语义）。"""
        if from_index == to_index:
            return
        if not (0 <= from_index < len(self._items)) or not (0 <= to_index < len(self._items)):
            return
        item = self._items.pop(from_index)
        self._items.insert(to_index, item)
        for i, it in enumerate(self._items):
            it._index = i
        self._rebuild_tabs_layout()
        self._active = _adjust_index(self._active, from_index, to_index)
        self._apply_styles()
        self._relayout()
        self.tabs_reordered.emit(from_index, to_index)

    def _rebuild_tabs_layout(self) -> None:
        """按当前 _items 顺序重建标签布局（保持末尾 stretch）。"""
        stretch_item = self._tabs_layout.takeAt(self._tabs_layout.count() - 1)
        for it in self._items:
            self._tabs_layout.removeWidget(it)
        for it in self._items:
            self._tabs_layout.addWidget(it)
        if stretch_item is not None:
            self._tabs_layout.addItem(stretch_item)

    def _relayout(self) -> None:
        """按可用宽度重算各标签宽度（浏览器式收缩）。"""
        n = len(self._items)
        if n == 0:
            self._update_overflow_buttons()
            return
        viewport_w = max(self._scroll.viewport().width(), 1)
        naturals = [it.natural_width() for it in self._items]
        total_natural = sum(naturals) + (n - 1) * self.TAB_GAP

        if total_natural <= viewport_w:
            widths = naturals
        else:
            # 溢出：所有标签等宽收缩（下限 MIN_TAB_W）
            per = (viewport_w - (n - 1) * self.TAB_GAP) // n
            per = max(per, self.MIN_TAB_W)
            widths = [per] * n

        for it, w in zip(self._items, widths):
            it.setFixedWidth(w)
            it._elide()
        self._update_overflow_buttons()

    def _update_overflow_buttons(self) -> None:
        sb = self._scroll.horizontalScrollBar()
        overflow = sb.maximum() > 0
        self._left_btn.setVisible(overflow)
        self._right_btn.setVisible(overflow)
        self._sync_scroll_buttons()

    def _scroll_by(self, dx: int) -> None:
        sb = self._scroll.horizontalScrollBar()
        sb.setValue(sb.value() + dx)
        self._sync_scroll_buttons()

    def _sync_scroll_buttons(self) -> None:
        sb = self._scroll.horizontalScrollBar()
        self._left_btn.setEnabled(sb.value() > 0)
        self._right_btn.setEnabled(sb.value() < sb.maximum())

    def _scroll_to_item(self, index: int) -> None:
        item = self._items[index]
        sb = self._scroll.horizontalScrollBar()
        vp_w = self._scroll.viewport().width()
        x0 = item.x()
        x1 = x0 + item.width()
        if x0 < sb.value():
            sb.setValue(x0)
        elif x1 > sb.value() + vp_w:
            sb.setValue(x1 - vp_w)
        self._sync_scroll_buttons()

    def _apply_styles(self) -> None:
        """按主题与激活状态刷新标签样式。"""
        tp = theme_manager.palette
        for i, it in enumerate(self._items):
            active = i == self._active
            bg = tp.canvas.name() if active else tp.surface.name()
            fg = tp.accent.name() if active else tp.text_secondary.name()
            it.setStyleSheet(
                f"QFrame {{ background-color: {bg};"
                f"border: 1px solid {tp.divider.name()};"
                f"border-radius: 4px 4px 0 0; }}"
                f"QFrame:hover {{ background-color: "
                f"{tp.surface_hover.name() if not active else tp.canvas.name()}; }}"
            )
            it._label.setStyleSheet(
                f"color: {fg}; font-size: 9pt; background: transparent; border: none;"
            )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._scroll.viewport() and event.type() == QEvent.Type.Wheel:
            dy = event.angleDelta().y()
            dx = event.angleDelta().x()
            delta = dy if dy else dx
            self._scroll_by(delta)
            return True
        return super().eventFilter(watched, event)
