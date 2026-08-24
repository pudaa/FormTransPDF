"""浏览器式标签拖拽测试：实时跟随 / 挤压缓动 / 一次性重排信号 / 点击不受影响。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.ui.widgets.document_tab_bar import DocumentTabBar

TITLES = ["Alpha Paper", "Beta Paper", "Gamma Paper", "Delta Paper"]


def _pump(n: int = 6) -> None:
    """处理事件队列（布局/显示生效）。"""
    for _ in range(n):
        QApplication.processEvents()


@pytest.fixture
def bar(qapp):
    b = DocumentTabBar()
    b.resize(600, DocumentTabBar.BAR_HEIGHT)
    for t in TITLES:
        b.add_tab(t)
    # 固定自然宽度，槽位几何完全确定：x_i = i * (120 + GAP)
    for it in b._items:
        it.natural_width = lambda: 120  # type: ignore[method-assign]
    b.show()
    _pump()
    try:
        QTest.qWaitForWindowExposed(b, 500)
    except Exception:
        pass
    _pump()
    yield b
    b.deleteLater()


def _move(widget, x: float, y: float = 10.0) -> None:
    """向控件发送带左键按下的 MouseMove（QTest.mouseMove 不携带按键态）。"""
    ev = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, ev)


def _release(widget, x: float, y: float = 10.0) -> None:
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(int(x), int(y)))


def _tick(bar, n: int = 60) -> None:
    """手动驱动动画帧至收敛。"""
    for _ in range(n):
        bar._tick_drag()


# ── 实时跟随 + 挤压 ─────────────────────────────────────────

def test_drag_follows_mouse_and_squeezes(bar):
    """拖拽签 1:1 跟随光标；其余签向空位两侧缓动让位。"""
    x0 = [it.x() for it in bar._items]
    assert x0 == [0, 124, 248, 372]

    # 按下第 1 个标签（Beta），越过阈值进入拖拽
    item = bar._items[1]
    QTest.mousePress(item, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    _move(item, 60, 10)  # 触发阈值（grab_dx=60）
    assert bar._drag_item is item
    assert all(it.parentWidget() is not None for it in bar._items)

    # 光标右移：拖拽签必须逐帧贴住光标（内容坐标 = 栏坐标 - 视口偏移20）
    for bx in (150, 250, 340, 440):
        _move(bar, bx)
        expected_cx = bx - bar._scroll.viewport().mapTo(bar, QPoint(0, 0)).x()
        assert item.x() == int(expected_cx - 60), f"未跟随光标 @bar_x={bx}"

    # 拖到末尾（drop=3）：其余签被挤压左移让位
    _move(bar, 440)
    _tick(bar)
    assert bar._drop_index == 3
    assert bar._items[2].x() == 124  # Gamma: 248 → 124
    assert bar._items[3].x() == 248  # Delta: 372 → 248
    assert bar._items[0].x() == 0

    _release(bar, 440)
    # 一次性重排：Beta 移到末尾，信号只发一次
    assert [it.title() for it in bar._items] == [
        "Alpha Paper", "Gamma Paper", "Delta Paper", "Beta Paper"
    ]
    assert bar._drop_index == 3 and bar._drag_item is None


def test_reorder_signal_emitted_once_with_indices(qapp):
    bar = DocumentTabBar()
    bar.resize(600, DocumentTabBar.BAR_HEIGHT)
    for t in TITLES:
        bar.add_tab(t)
    for it in bar._items:
        it.natural_width = lambda: 120  # type: ignore[method-assign]
    bar.show()
    _pump()

    got: list[tuple[int, int]] = []
    bar.tabs_reordered.connect(lambda a, b_: got.append((a, b_)))

    item = bar._items[1]
    QTest.mousePress(item, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    _move(item, 60, 10)
    _move(bar, 440)
    _tick(bar)
    _release(bar, 440)

    assert got == [(1, 3)], f"应恰好一次 (1,3)，实际 {got}"


def test_drop_at_origin_no_signal(bar):
    """拖出又拖回原位释放：不重排、不发信号。"""
    got: list[tuple[int, int]] = []
    bar.tabs_reordered.connect(lambda a, b_: got.append((a, b_)))

    item = bar._items[2]
    QTest.mousePress(item, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    _move(item, 60, 10)
    _move(bar, 230)   # 略向左偏（仍在原槽位附近）
    _tick(bar)
    _move(bar, 268)   # 回到原位附近
    _tick(bar)
    _release(bar, 268)

    assert got == []
    assert [it.title() for it in bar._items] == TITLES


# ── 点击 / 关闭不受拖拽改造影响 ─────────────────────────────

def test_click_still_activates(bar):
    got: list[int] = []
    bar.tab_activated.connect(got.append)

    item = bar._items[2]
    QTest.mousePress(item, Qt.MouseButton.LeftButton, pos=QPoint(30, 10))
    _move(item, 33, 10)  # 低于阈值
    _release(item, 33, 10)

    assert got == [2]
    assert [it.title() for it in bar._items] == TITLES


def test_close_button_still_works(bar):
    got: list[int] = []
    bar.tab_close_requested.connect(got.append)
    bar._items[1]._close_btn.click()
    assert got == [1]


# ── 溢出 + 边缘自动滚动 ─────────────────────────────────────

def test_overflow_edge_autoscroll(qapp):
    bar = DocumentTabBar()
    bar.resize(260, DocumentTabBar.BAR_HEIGHT)  # 视口窄 → 溢出
    for t in TITLES:
        bar.add_tab(t)
    for it in bar._items:
        it.natural_width = lambda: 120  # type: ignore[method-assign]
    bar.show()
    _pump()
    sb = bar._scroll.horizontalScrollBar()
    assert sb.maximum() > 0, "应处于溢出状态"

    item = bar._items[0]
    QTest.mousePress(item, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    _move(item, 60, 10)
    # 光标送到视口右缘之外 → 触发边缘自动滚动
    vp_off = bar._scroll.viewport().mapTo(bar, QPoint(0, 0)).x()
    _move(bar, vp_off + bar._scroll.viewport().width() + 40)
    v0 = sb.value()
    _tick(bar, 30)
    assert sb.value() > v0 or sb.value() == sb.maximum()

    _release(bar, vp_off + 100)
    assert bar.count() == 4
    assert bar._drag_item is None
