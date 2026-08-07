"""
窗口装饰（第二期）— 无边框窗口 + 工具栏作标题栏。

组件：
- ``TitleBar``：可拖拽移动、双击最大化/还原的标题栏容器
- ``WindowControls``：最小化 / 最大化·还原 / 关闭 按钮（SVG 图标，主题自适应）
- ``_WindowChromeMixin``：注入 MainWindow —— 应用无边框、边缘缩放、DWM 圆角、
  最大化状态同步；Windows 上使用原生 ShowWindow / HTCAPTION 拖拽，支持 Aero Snap

跨平台：
- Windows：DWM 原生圆角 + 原生最大化（ShowWindow）+ 原生拖拽（snap 自动布局）
- 其他平台：直角；边缘缩放 / 标题栏拖拽 / 窗口控制按钮全平台可用
"""

from __future__ import annotations

import sys
from typing import cast

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget

from src.ui.base.icon_factory import svg_icon
from src.ui.base.theme import theme_manager

# 边缘缩放参数
_RESIZE_EDGE = 6            # 边缘识别宽度（px）
_EDGE_NONE = 0
_EDGE_LEFT = 1
_EDGE_RIGHT = 2
_EDGE_TOP = 4
_EDGE_BOTTOM = 8

# Windows 原生窗口命令（user32.ShowWindow）
_SW_MAXIMIZE = 3
_SW_RESTORE = 9


def _apply_win_corner_rounding(hwnd: int, rounded: bool) -> None:
    """Windows 11 DWM 圆角（DWMWA_WINDOW_CORNER_PREFERENCE）；其他系统忽略。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        DWMWCP_DONOTROUND = 1
        value = ctypes.c_int(DWMWCP_ROUND if rounded else DWMWCP_DONOTROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(hwnd),
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception:
        pass  # 非 Win11 / 无 dwmapi → 保持直角，静默降级


class TitleBar(QWidget):
    """可拖拽移动、双击最大化/还原的标题栏容器。

    Windows 上通过原生 HTCAPTION 拖拽触发系统 snap 布局 / 吸附最大化，
    其它平台回退为手动 move 拖拽。
    """

    DRAG_THRESHOLD = 4  # 移动超过该像素才进入拖拽（区分点击/双击）

    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._press_global: QPoint | None = None
        self._drag_started = False
        self._drag_offset: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._drag_started = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if not self._drag_started:
                if (event.globalPosition().toPoint() - self._press_global).manhattanLength() > self.DRAG_THRESHOLD:
                    self._drag_started = True
                    if sys.platform == "win32":
                        self._start_native_move()  # 原生拖拽：系统处理移动/snap/吸附
                    elif not self._window.is_chrome_maximized():
                        self._drag_offset = (
                            event.globalPosition().toPoint()
                            - self._window.frameGeometry().topLeft()
                        )
                        # 立即跟随，避免拖拽起始的跳变/迟滞
                        self._window.move(event.globalPosition().toPoint() - self._drag_offset)
                        self.grabMouse()
            elif self._drag_offset is not None and not self._window.is_chrome_maximized():
                self._window.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_global = None
        self._drag_started = False
        if self._drag_offset is not None:
            self._drag_offset = None
            self.releaseMouse()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._window.toggle_maximize_window()
        super().mouseDoubleClickEvent(event)

    def _start_native_move(self) -> None:
        """触发 Windows 原生标题栏拖拽（系统接管，支持 snap 布局/吸附最大化）。"""
        try:
            import ctypes
            WM_NCLBUTTONDOWN = 0x00A1
            HTCAPTION = 0x0002
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(
                int(self._window.winId()), WM_NCLBUTTONDOWN, HTCAPTION, 0
            )
        except Exception:
            pass  # 非 Windows 或调用失败：回退手动拖拽


class _WinButton(QPushButton):
    """窗口控制按钮：SVG 图标 + QSS hover 背景，主题自适应（Fluent 风格）。"""

    ICON_SIZE = 16  # SVG 实心图形视觉偏小，16px 与细线 18px 观感相当

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind  # "minimize" | "maximize" | "restore" | "close"
        self._hover = False
        self.setFixedSize(46, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._refresh_style()

    def set_kind(self, kind: str) -> None:
        if kind != self._kind:
            self._kind = kind
            self._refresh_icon()

    def refresh_theme(self) -> None:
        """主题切换后重设 hover 背景与图标颜色。"""
        self._refresh_style()

    def _refresh_style(self) -> None:
        """重设 QSS（hover 背景随主题）+ 图标颜色。"""
        tp = theme_manager.palette
        if self._kind == "close":
            self.setStyleSheet(
                "QPushButton { background: transparent; border: none; }"
                "QPushButton:hover { background: #e81123; }"
            )
        else:
            self.setStyleSheet(
                "QPushButton { background: transparent; border: none; }"
                f"QPushButton:hover {{ background: {tp.surface_hover.name()}; }}"
            )
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        tp = theme_manager.palette
        if self._kind == "close" and self._hover:
            color = QColor("#ffffff")
        else:
            color = tp.text_primary if self._hover else tp.text_secondary
        self.setIcon(svg_icon(self._kind, color, self.ICON_SIZE))

    def enterEvent(self, event) -> None:
        self._hover = True
        self._refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._refresh_icon()
        super().leaveEvent(event)


class WindowControls(QWidget):
    """最小化 / 最大化·还原 / 关闭 三个窗口控制按钮（Fluent 风格）。"""

    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self.setFixedHeight(40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._min_btn = _WinButton("minimize")
        self._max_btn = _WinButton("maximize")
        self._close_btn = _WinButton("close")

        self._min_btn.clicked.connect(window.showMinimized)
        self._max_btn.clicked.connect(window.toggle_maximize_window)
        self._close_btn.clicked.connect(window.close)

        layout.addWidget(self._min_btn)
        layout.addWidget(self._max_btn)
        layout.addWidget(self._close_btn)

        self.set_maximized(window.is_chrome_maximized())

    def refresh_theme(self) -> None:
        """主题切换后刷新所有窗口控制按钮。"""
        for btn in (self._min_btn, self._max_btn, self._close_btn):
            btn.refresh_theme()

    def set_maximized(self, maximized: bool) -> None:
        self._max_btn.set_kind("restore" if maximized else "maximize")


class _WindowChromeMixin:
    """无边框窗口装饰：标题栏拖拽、窗口控制、边缘缩放、DWM 圆角。"""

    def _setup_window_chrome(self) -> None:
        """应用无边框并初始化窗口装饰（在 _build_ui 之后调用）。"""
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        # 手动维护的最大化状态：Windows 无边框窗口首次 showMaximized 可能仅缩放
        # 未真正置位（isMaximized 滞后一拍），故不依赖平台状态作为 UI 事实源
        self._maximized_flag = False
        self._normal_geometry: QRect | None = None
        self._suppress_state_sync = False  # 屏蔽自身 showMaximized 触发的 changeEvent
        self._resizing_edge = _EDGE_NONE
        self._resize_start_global: QPoint | None = None
        self._resize_start_geom: QRect | None = None
        self._cursor_override = None
        # 应用级事件过滤器：检测窗口边缘缩放（无边框窗口无原生缩放边框）
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(cast(QObject, self))
        # Windows：补充系统样式以支持 Aero Snap 自动布局
        self._setup_win_native()
        self._apply_corner_state()

    def _build_window_controls(self) -> QWidget:
        """创建窗口控制按钮（最小化/最大化·还原/关闭）。"""
        self._win_controls = WindowControls(self)
        return self._win_controls

    def _apply_corner_state(self) -> None:
        """按最大化状态应用 Windows DWM 圆角。"""
        if self.windowHandle() is None:
            return
        hwnd = int(self.winId())
        if hwnd:
            _apply_win_corner_rounding(hwnd, rounded=not self.is_chrome_maximized())

    def changeEvent(self, event) -> None:
        # 仅在非自身触发（如原生 snap/拖拽吸附）时同步状态，避免与手动标志冲突
        if (
            event.type() == QEvent.Type.WindowStateChange
            and not getattr(self, "_suppress_state_sync", False)
        ):
            now_max = bool(self.windowState() & Qt.WindowState.WindowMaximized)
            if now_max and not self.is_chrome_maximized():
                # 原生路径（系统吸附/拖拽）进入最大化：快照当前几何供还原
                self._normal_geometry = QRect(self.frameGeometry())
            self._maximized_flag = now_max
            self._sync_window_chrome()
        super().changeEvent(event)

    def showEvent(self, event) -> None:
        self._setup_win_native()
        self._apply_corner_state()
        super().showEvent(event)

    # ── Windows 原生窗口支持 ──────────────────────────────

    def _setup_win_native(self) -> None:
        """Windows：为无边框窗口补充系统样式，启用 Aero Snap 自动布局。

        Qt 的 FramelessWindowHint 会拦截 WM_NCHITTEST（边缘缩放仍需自定义实现），
        但需要 WS_THICKFRAME / WS_MAXIMIZEBOX / WS_MINIMIZEBOX 才能让系统在
        HTCAPTION 拖拽时触发吸附最大化 / 侧边分屏等自动布局。
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            GWL_STYLE = -16
            WS_THICKFRAME = 0x00040000
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            hwnd = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            style |= WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
            # 使样式变更生效
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
        except Exception:
            pass  # 非 Windows / 无权限 → 静默降级

    def _native_window_command(self, command: int) -> None:
        """Windows：调用系统 ShowWindow 执行窗口命令（SW_MAXIMIZE / SW_RESTORE）。

        系统原生最大化会真正置位 WS_MAXIMIZE，绕开 Qt 无边框窗口
        showMaximized() 状态滞后的问题，并支持拖拽还原、snap 等原生行为。
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(int(self.winId()), command)
        except Exception:
            pass  # 调用失败 → 由调用方几何兜底

    # ── 最大化 / 还原（手动标志，兼容平台状态延迟）────────

    def is_chrome_maximized(self) -> bool:
        """窗口装饰视角是否处于最大化（手动标志，避免依赖滞后的 isMaximized）。"""
        return bool(getattr(self, "_maximized_flag", False))

    def toggle_maximize_window(self) -> None:
        if self.is_chrome_maximized():
            self._restore_window()
        else:
            self._maximize_window()

    def _maximize_window(self) -> None:
        self._normal_geometry = QRect(self.frameGeometry())
        self._maximized_flag = True
        self._suppress_state_sync = True
        try:
            if sys.platform == "win32":
                # 系统原生最大化：真正置位 WS_MAXIMIZE（解决 Qt 无边框状态滞后）
                self._native_window_command(_SW_MAXIMIZE)
            else:
                self.showMaximized()
        finally:
            self._suppress_state_sync = False
        # 兜底：仍未置位时手动铺满工作区（忽略最小尺寸约束，确保真正铺满）
        if not self.isMaximized():
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                self.setMinimumSize(0, 0)
                self.setGeometry(screen.availableGeometry())
        self._sync_window_chrome()

    def _restore_window(self) -> None:
        self._suppress_state_sync = True
        try:
            if sys.platform == "win32":
                self._native_window_command(_SW_RESTORE)
            else:
                self.showNormal()
        finally:
            self._suppress_state_sync = False
        # 强制恢复为最大化前的几何（兼容状态未置位的平台）
        if self._normal_geometry is not None and not self.isMaximized():
            self.setGeometry(self._normal_geometry)
        # 恢复最小尺寸约束
        if self.minimumWidth() != self.MIN_WINDOW_W or self.minimumHeight() != self.MIN_WINDOW_H:
            self.setMinimumSize(self.MIN_WINDOW_W, self.MIN_WINDOW_H)
        self._maximized_flag = False
        self._sync_window_chrome()

    def _sync_window_chrome(self) -> None:
        """按手动最大化标志同步窗口控制按钮 / 圆角 / 缩放光标。"""
        if hasattr(self, "_win_controls"):
            self._win_controls.set_maximized(self.is_chrome_maximized())
        self._apply_corner_state()
        if self.is_chrome_maximized():
            self._restore_resize_cursor()

    # ── 边缘缩放（应用级事件过滤器入口）──────────────────

    def _chrome_handle_event(self, watched, event) -> bool:
        """处理窗口边缘缩放相关鼠标事件；返回 True 表示已消费。"""
        if not isinstance(event, QMouseEvent):
            return False
        w = getattr(event, "widget", None)
        if w is None:
            w = watched
        if not self._is_descendant(w):
            return False

        etype = event.type()
        if etype == QEvent.Type.MouseMove:
            if self._resizing_edge:
                self._resize_window(event.globalPosition().toPoint())
                return True
            if not self.isMaximized():
                self._update_resize_cursor(self._edge_at(event.globalPosition().toPoint()))
            return False

        if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if not self.isMaximized():
                edge = self._edge_at(event.globalPosition().toPoint())
                if edge:
                    self._resizing_edge = edge
                    self._resize_start_global = event.globalPosition().toPoint()
                    self._resize_start_geom = QRect(self.frameGeometry())
                    return True
            return False

        if etype == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self._resizing_edge:
                self._resizing_edge = _EDGE_NONE
                self._resize_start_global = None
                self._resize_start_geom = None
                self._restore_resize_cursor()
                return True
            return False

        return False

    def _is_descendant(self, w) -> bool:
        """w 是否为本窗口的 QWidget 子孙。

        应用级事件过滤器会收到 QWindow（原生窗口句柄）等非 QWidget 事件对象
        （它们没有 parentWidget()），这类事件与边缘缩放无关，直接忽略。
        """
        if not isinstance(w, QWidget):
            return False
        if w is self:
            return True
        p = w.parentWidget()
        while p is not None:
            if p is self:
                return True
            p = p.parentWidget()
        return False

    def _edge_at(self, global_pos: QPoint) -> int:
        """返回 global_pos 命中的窗口边缘（位掩码）。"""
        local = self.mapFromGlobal(global_pos)
        r = self.rect()
        x, y = local.x(), local.y()
        edge = _EDGE_NONE
        if x <= _RESIZE_EDGE:
            edge |= _EDGE_LEFT
        elif x >= r.width() - _RESIZE_EDGE:
            edge |= _EDGE_RIGHT
        if y <= _RESIZE_EDGE:
            edge |= _EDGE_TOP
        elif y >= r.height() - _RESIZE_EDGE:
            edge |= _EDGE_BOTTOM
        return edge

    def _resize_window(self, global_pos: QPoint) -> None:
        if self._resize_start_global is None or self._resize_start_geom is None:
            return
        delta = global_pos - self._resize_start_global
        geom = QRect(self._resize_start_geom)
        edge = self._resizing_edge

        if edge & _EDGE_LEFT:
            geom.setLeft(self._resize_start_geom.left() + delta.x())
        if edge & _EDGE_RIGHT:
            geom.setRight(self._resize_start_geom.right() + delta.x())
        if edge & _EDGE_TOP:
            geom.setTop(self._resize_start_geom.top() + delta.y())
        if edge & _EDGE_BOTTOM:
            geom.setBottom(self._resize_start_geom.bottom() + delta.y())

        # 最小尺寸约束
        if geom.width() < self.MIN_WINDOW_W:
            if edge & _EDGE_LEFT:
                geom.setLeft(geom.right() - self.MIN_WINDOW_W)
            else:
                geom.setRight(geom.left() + self.MIN_WINDOW_W)
        if geom.height() < self.MIN_WINDOW_H:
            if edge & _EDGE_TOP:
                geom.setTop(geom.bottom() - self.MIN_WINDOW_H)
            else:
                geom.setBottom(geom.top() + self.MIN_WINDOW_H)

        self.setGeometry(geom)

    @staticmethod
    def _resize_cursor_for(edge: int):
        if edge in (_EDGE_LEFT, _EDGE_RIGHT):
            return Qt.CursorShape.SizeHorCursor
        if edge in (_EDGE_TOP, _EDGE_BOTTOM):
            return Qt.CursorShape.SizeVerCursor
        if edge in (_EDGE_TOP | _EDGE_LEFT, _EDGE_BOTTOM | _EDGE_RIGHT):
            return Qt.CursorShape.SizeFDiagCursor
        if edge in (_EDGE_TOP | _EDGE_RIGHT, _EDGE_BOTTOM | _EDGE_LEFT):
            return Qt.CursorShape.SizeBDiagCursor
        return Qt.CursorShape.ArrowCursor

    def _update_resize_cursor(self, edge: int) -> None:
        desired = self._resize_cursor_for(edge) if edge else Qt.CursorShape.ArrowCursor
        if desired == Qt.CursorShape.ArrowCursor:
            self._restore_resize_cursor()
            return
        app = QApplication.instance()
        if app is None:
            return
        if self._cursor_override is None:
            app.setOverrideCursor(desired)
            self._cursor_override = desired
        elif self._cursor_override != desired:
            app.changeOverrideCursor(desired)
            self._cursor_override = desired

    def _restore_resize_cursor(self) -> None:
        if self._cursor_override is not None:
            app = QApplication.instance()
            if app is not None:
                app.restoreOverrideCursor()
            self._cursor_override = None
