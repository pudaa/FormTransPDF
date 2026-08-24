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

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
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


# 触发拖拽的位移阈值（像素）：低于此值视为普通点击
DRAG_THRESHOLD = 6
# 其余标签向目标槽位缓动的每帧插值系数（浏览器式“挤压”手感）
_DRAG_EASE = 0.38
# 拖拽至视口边缘的自动滚动触发带宽度（像素）与每帧滚动速度
_EDGE_MARGIN = 28
_EDGE_SCROLL_SPEED = 12


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
            and (event.pos() - self._press_pos).manhattanLength() > DRAG_THRESHOLD
        ):
            # 超过拖拽阈值：交由标签栏进入拖拽重排模式（标签栏 grabMouse）。
            # 传入按下点在标签内的 x 偏移，供拖拽签 1:1 跟随光标时保持抓取点。
            self._drag_active = True
            self._on_drag_start(self._index, float(event.position().x()))
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
        # ── 浏览器式拖拽状态 ──
        # 拖拽期间所有标签脱离布局管理、由手动 setGeometry 控制：
        # 拖拽签 1:1 跟随光标；其余签向「落点空位」两侧的目标槽位缓动（挤压）。
        self._drag_item: _TabItem | None = None
        self._drag_orig_index = -1
        self._drop_index = 0
        self._grab_dx = 0.0          # 按下点在拖拽签内的 x 偏移（内容坐标）
        self._last_bar_x = 0.0       # 最近一次光标的标签栏坐标（边缘滚动后回贴用）
        self._targets: dict[int, float] = {}   # id(item) -> 目标 x（非拖拽项）
        self._edge_dir = 0           # 边缘自动滚动方向（-1/0/1）
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._tick_drag)

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

    # ── 浏览器式拖拽重排 ──────────────────────────────────
    #
    # 复刻 Chrome/Firefox 的三层机制：
    #   1) 拖拽签脱离布局流，1:1 跟随光标（保持抓取点不跳）；
    #   2) 其余标签按「落点空位」排列——落点由拖拽签中心与其余签槽位中点
    #      实时比较得出；
    #   3) 落点变化时其余签向新目标位缓动滑动（挤压/让位观感）。
    #   释放时吸附落位、逻辑重排一次并交还布局管理。

    def _on_item_drag_start(self, index: int, grab_x: float) -> None:
        """进入拖拽：全部标签脱离布局、锁定内容尺寸、启动动画帧。"""
        if not (0 <= index < len(self._items)) or self._drag_item is not None:
            return
        drag = self._items[index]
        self._drag_item = drag
        self._drag_orig_index = index
        self._drop_index = index
        self._grab_dx = float(grab_x)
        self._last_bar_x = self._item_center_bar_x(drag)

        # 记录几何后全部移出布局（保留末尾 stretch），改由手动 setGeometry 控制
        for it in self._items:
            it._drag_y = it.y()
            it._drag_h = it.height()
            self._tabs_layout.removeWidget(it)
        # 锁定内容尺寸：布局清空后 sizeHint 收缩会导致 QScrollArea 压缩内容宽
        self._content.setMinimumSize(self._content.width(), self._content.height())

        drag.raise_()
        self._compute_targets()  # 初始目标 == 当前位置，无跳动
        self.grabMouse()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._anim_timer.start()

    def _item_center_bar_x(self, item: _TabItem) -> float:
        """标签中心在标签栏坐标系中的 x。"""
        return item.mapTo(self, item.rect().center()).x()

    def _content_x_from_bar(self, bar_x: float) -> float:
        """标签栏坐标 → 内容坐标（计入视口偏移与横向滚动量）。"""
        vp = self._scroll.viewport()
        off = vp.mapTo(self, QPoint(0, 0)).x()
        return bar_x - off + self._scroll.horizontalScrollBar().value()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_item is not None:
            bar_x = float(event.position().x())
            self._last_bar_x = bar_x
            cx = self._content_x_from_bar(bar_x)
            drag = self._drag_item
            max_x = max(0, self._content.width() - drag.width())
            nx = min(max(cx - self._grab_dx, 0.0), float(max_x))
            drag.move(int(round(nx)), drag.y())
            self._update_edge_scroll(bar_x)
            self._update_drop()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_item is not None:
            self._end_drag()
            return
        super().mouseReleaseEvent(event)

    def _update_edge_scroll(self, bar_x: float) -> None:
        """光标进入视口左右边缘触发带 → 标记自动滚动方向（由动画帧执行）。"""
        if self._scroll.horizontalScrollBar().maximum() <= 0:
            self._edge_dir = 0
            return
        vp = self._scroll.viewport()
        local = bar_x - vp.mapTo(self, QPoint(0, 0)).x()
        if local < _EDGE_MARGIN:
            self._edge_dir = -1
        elif local > vp.width() - _EDGE_MARGIN:
            self._edge_dir = 1
        else:
            self._edge_dir = 0

    def _slot_xs(self, others: list[_TabItem], drop: int, drag_w: int) -> list[float]:
        """其余标签在「drop 处插入 drag_w 空位」时的槽位 x（内容坐标）。"""
        xs: list[float] = []
        x = 0.0
        for i, it in enumerate(others):
            if i == drop:
                x += drag_w + self.TAB_GAP
            xs.append(x)
            x += it.width() + self.TAB_GAP
        return xs

    def _update_drop(self) -> None:
        """按拖拽签中心实时计算落点槽位；变化时刷新其余标签的目标位置。"""
        drag = self._drag_item
        if drag is None:
            return
        others = [it for it in self._items if it is not drag]
        if not others:
            return
        drag_cx = drag.x() + drag.width() / 2
        d = self._drop_index
        for _ in range(3):  # 迭代收敛（单帧内至多跨越一两个槽位）
            xs = self._slot_xs(others, d, drag.width())
            nd = 0
            for i, it in enumerate(others):
                if drag_cx > xs[i] + it.width() / 2:
                    nd = i + 1
            if nd == d:
                break
            d = nd
        if d != self._drop_index:
            self._drop_index = d
            self._compute_targets()

    def _compute_targets(self) -> None:
        """按「其余标签 + drop 处空位」的排列计算各非拖拽标签的目标 x。"""
        drag = self._drag_item
        if drag is None:
            return
        others = [it for it in self._items if it is not drag]
        arranged = others[: self._drop_index] + [drag] + others[self._drop_index :]
        x = 0.0
        self._targets.clear()
        for it in arranged:
            if it is not drag:
                self._targets[id(it)] = x
            x += it.width() + self.TAB_GAP

    def _tick_drag(self) -> None:
        """16ms 动画帧：边缘自动滚动 + 其余标签向目标位缓动（挤压效果）。"""
        if self._drag_item is None:
            self._anim_timer.stop()
            return
        sb = self._scroll.horizontalScrollBar()
        if self._edge_dir != 0 and sb.maximum() > 0:
            nv = min(max(sb.value() + _EDGE_SCROLL_SPEED * self._edge_dir, 0), sb.maximum())
            if nv != sb.value():
                sb.setValue(nv)
                # 滚动后重贴拖拽签位置，保持其仍抓在光标下
                cx = self._content_x_from_bar(self._last_bar_x)
                drag = self._drag_item
                max_x = max(0, self._content.width() - drag.width())
                drag.move(
                    int(round(min(max(cx - self._grab_dx, 0.0), max_x))), drag.y()
                )
                self._update_drop()

        for it in self._items:
            if it is self._drag_item:
                continue
            tgt = self._targets.get(id(it))
            if tgt is None:
                continue
            cur = it.x()
            delta = tgt - cur
            # 距目标 ≤1px 直接吸附：否则小数缓动会在 ±0.6px 附近形成
            # 「round 后原地踏步」的定点循环（实测卡在 tgt+1 不收敛）
            if abs(delta) <= 1.0:
                nx = tgt
            else:
                nx = cur + delta * _DRAG_EASE
            if int(round(nx)) != it.x():
                it.move(int(round(nx)), it.y())

    def _end_drag(self) -> None:
        """结束拖拽：吸附落位 → 一次性逻辑重排 → 交还布局管理 → 发信号。"""
        self._anim_timer.stop()
        drag = self._drag_item
        orig = self._drag_orig_index
        drop = self._drop_index
        self._drag_item = None
        self._edge_dir = 0
        self.unsetCursor()
        try:
            self.releaseMouse()
        except RuntimeError:
            pass

        # 吸附到最终槽位（避免交还布局时跳变）
        self._compute_targets()
        for it in self._items:
            if it is drag:
                continue
            tx = self._targets.get(id(it))
            if tx is not None:
                it.move(int(round(tx)), it.y())

        moved = drag is not None and drop != orig
        if moved:
            self._items.pop(orig)
            self._items.insert(drop, drag)
            for i, it in enumerate(self._items):
                it._index = i
            self._active = _adjust_index(self._active, orig, drop)

        # 恢复布局管理并复位拖拽态
        self._content.setMinimumSize(0, 0)
        self._rebuild_tabs_layout()
        self._relayout()
        self._apply_styles()
        if drag is not None:
            drag._drag_active = False
            drag._press_pos = QPoint()

        if moved:
            self.tabs_reordered.emit(orig, drop)

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
        if self._drag_item is not None:
            return  # 拖拽中几何由动画帧手动控制，避免布局重算打架
        self._relayout()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._scroll.viewport() and event.type() == QEvent.Type.Wheel:
            dy = event.angleDelta().y()
            dx = event.angleDelta().x()
            delta = dy if dy else dx
            self._scroll_by(delta)
            return True
        return super().eventFilter(watched, event)
