"""
历史流程 — 历史记录回放与输出模式切换。

以 mixin 形式注入 MainWindow（windows/main_window.py），
依赖 self._viewer / self._tab_bar / self._download_btn / self._settings /
self._dual_path / self._mono_path 等由 MainWindow.__init__ 初始化的状态。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox


class _HistoryFlowMixin:
    """历史记录选择与双栏/单栏切换。"""

    def _on_history_selected(self, dual_path: str, mono_path: str, name: str) -> None:
        """点击历史记录中的翻译"""
        target = dual_path or mono_path
        if not target:
            return
        path = Path(target)
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", f"历史文件已失效:\n{path}")
            return
        self._dual_path = Path(dual_path) if dual_path else None
        self._mono_path = Path(mono_path) if mono_path else None

        self._viewer.load_pdf(target)
        self._setup_minimap()
        self._tab_bar.setCurrentIndex(0)
        self._tab_bar.setTabEnabled(1, True)
        self._download_btn.setEnabled(bool(target))
        self._settings.set_pdf_loaded(name, loaded=False)
        self._settings.set_status(f"历史: {name}")

    def _on_output_mode_changed(self) -> None:
        """输出模式下拉框变更时，若正在查看历史记录则切换双栏/单栏"""
        # 仅在有历史双栏+单栏文件时生效
        if not (self._dual_path and self._mono_path):
            return
        mode = self._settings._output_mode_combo.currentData()
        if mode == "mono" and self._mono_path.exists():
            self._viewer.load_pdf(str(self._mono_path))
            self._setup_minimap()
        elif mode == "dual" and self._dual_path.exists():
            self._viewer.load_pdf(str(self._dual_path))
            self._setup_minimap()
