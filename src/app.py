"""
Application 类 — QApplication + 主题管理 + 事件循环
"""

from __future__ import annotations

import sys
import asyncio
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon, QPalette
from PySide6.QtWidgets import QApplication

import qasync

from src import __app_name__, __version__
from src.ui.theme import (
    ThemeManager,
    ThemeMode,
    _ColorsProxy,
    build_palette,
    build_stylesheet,
    create_body_font,
)

logger = logging.getLogger(__name__)


class FormTransPDFApp(QApplication):
    """FormTransPDF 应用实例"""

    def __init__(self, argv: list[str] | None = None) -> None:
        if argv is None:
            argv = sys.argv

        # ── Windows 任务栏图标：必须在 QApplication 创建之前设置 AppUserModelID ──
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "FormTransPDF.FormTransPDF"
                )
            except Exception:
                pass

        super().__init__(argv)

        self.setApplicationName(__app_name__)
        self.setApplicationVersion(__version__)
        self.setOrganizationName("FormTransPDF")

        # ── 应用图标 ──
        self._set_app_icon()

        self._theme_manager = ThemeManager()
        self._apply_theme()



    def _set_app_icon(self) -> None:
        """设置应用图标 — 必须在窗口创建之前调用，Windows 任务栏才生效"""
        if getattr(sys, "frozen", False):
            icon_path = Path(sys._MEIPASS) / "src" / "resources" / "icons" / "app.ico"
        else:
            icon_path = Path(__file__).resolve().parent / "resources" / "icons" / "app.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    # ── 主题 ────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        """应用当前主题"""
        tp = self._theme_manager.palette # 获取当前主题
        _ColorsProxy.set_palette(tp)  # ← 同步代理，确保 Colors.XXX 即时生效
        logger.info(f"Applying theme: {tp.name} ({tp.mode})")
        self.setPalette(build_palette(tp))
        self.setFont(create_body_font(11))
        self.setStyleSheet(build_stylesheet(tp))

    def toggle_theme(self) -> None:
        """切换亮/暗主题并刷新 UI"""
        self._theme_manager.toggle()
        self._apply_theme()

    @property
    def theme_manager(self) -> ThemeManager:
        return self._theme_manager

    # ── 启动 ────────────────────────────────────────────────

    def run(self) -> int:
        # ── 启动画面（在慢速 babeldoc 导入前展示）──
        from src.ui.splash import StartupSplash
        splash = StartupSplash()
        splash.show()
        self.processEvents()  # 强制绘制启动画面

        from src.ui.main_window import MainWindow

        loop = qasync.QEventLoop(self)
        asyncio.set_event_loop(loop)

        window = MainWindow()
        window.show()

        splash.finish(window)  # 关闭启动画面

        with loop:
            loop.run_forever()

        return 0
