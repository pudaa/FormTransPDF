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

    def _apply_history_result(self, entry) -> bool:
        """加载一条已有翻译结果（复用检测 / 历史回放），按当前输出模式展示。

        与 _on_finished 的展示逻辑一致。成功返回 True；结果文件缺失返回 False。
        """
        dual = entry.dual_pdf
        mono = entry.mono_pdf
        self._dual_path = dual
        self._mono_path = mono

        mode = "dual"
        if getattr(self, "_current_pdf", None):
            try:
                mode = self._settings.build_task(str(self._current_pdf)).output_mode
            except Exception:
                mode = "dual"

        target = mono if mode == "mono" else (dual or mono)
        if target and target.exists():
            self._viewer.load_pdf(str(target))
            self._setup_minimap()
            self._tab_bar.setCurrentIndex(1)
            self._tab_bar.setTabEnabled(1, True)
            self._download_btn.setEnabled(True)
            self._update_zoom_label()
            self._settings.set_status(f"已复用翻译结果：{entry.display_name}")
            return True

        QMessageBox.warning(self, "结果缺失", "已有翻译结果文件缺失，无法复用。")
        return False

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
