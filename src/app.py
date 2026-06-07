"""
Application 类 — QApplication + 主题管理 + 事件循环
"""

from __future__ import annotations

import sys
import asyncio
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPalette
from PyQt6.QtWidgets import QApplication

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

        super().__init__(argv)

        self.setApplicationName(__app_name__)
        self.setApplicationVersion(__version__)
        self.setOrganizationName("FormTransPDF")

        self._theme_manager = ThemeManager()
        self._apply_theme()

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
        from src.ui.main_window import MainWindow

        loop = qasync.QEventLoop(self)
        asyncio.set_event_loop(loop)

        window = MainWindow()
        window.show()

        with loop:
            loop.run_forever()

        return 0
