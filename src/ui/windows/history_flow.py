"""
历史流程 — 历史记录回放与输出模式切换。

以 mixin 形式注入 MainWindow（windows/main_window.py），
依赖 self._viewer / self._doc_tabs / self._doc_tab_bar / self._download_btn /
self._settings 等由 MainWindow.__init__ 初始化的状态。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from src.ui.widgets.document_tab_bar import DocumentTab


class _HistoryFlowMixin:
    """历史记录选择与双栏/单栏切换。"""

    def _apply_history_result(self, entry) -> bool:
        """加载一条已有翻译结果（复用检测），作用于当前文档标签页。

        成功返回 True；结果文件缺失返回 False。
        """
        tab = self._active_doc_tab()
        if tab is None:
            return False
        tab.dual_pdf = entry.dual_pdf
        tab.mono_pdf = entry.mono_pdf
        tab.view = "result"

        target = self._result_target(tab)
        if target and target.exists():
            self._apply_doc_view(tab)
            self._settings.set_status(f"已复用翻译结果：{entry.display_name}")
            return True

        QMessageBox.warning(self, "结果缺失", "已有翻译结果文件缺失，无法复用。")
        return False

    def _on_history_selected(self, dual_path: str, mono_path: str, name: str) -> None:
        """点击历史记录中的翻译 → 以新文档标签页打开该翻译结果。"""
        target = dual_path or mono_path
        if not target:
            return
        path = Path(target)
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", f"历史文件已失效:\n{path}")
            return

        # 去重：该结果文件已作为标签打开则切换到已有标签页
        for i, tab in enumerate(self._doc_tabs):
            if tab.source_pdf and tab.source_pdf.resolve() == path.resolve():
                self._activate_doc_tab(i)
                return

        # 历史结果标签页：无真实源文件，仅展示译文
        tab = DocumentTab(
            title=name,
            source_pdf=path,
            dual_pdf=Path(dual_path) if dual_path else None,
            mono_pdf=Path(mono_path) if mono_path else None,
            view="result",
            has_source=False,
        )
        self._doc_tabs.append(tab)
        self._doc_tab_bar.add_tab(tab.title)
        self._activate_doc_tab(len(self._doc_tabs) - 1)
        self._settings.set_pdf_loaded(name, loaded=False)  # 历史结果不可再翻译
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
