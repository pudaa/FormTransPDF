"""
历史流程 — 历史记录回放与输出模式切换。

以 mixin 形式注入 MainWindow（windows/main_window.py），
依赖 self._viewer / self._doc_tabs / self._doc_tab_bar / self._download_btn /
self._settings 等由 MainWindow.__init__ 初始化的状态。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from src.ui.widgets.document_tab_bar import DocumentTab

logger = logging.getLogger(__name__)


class _HistoryFlowMixin:
    """历史记录选择与双栏/单栏切换。"""

    def _on_history_delete_files(self, files) -> None:
        """历史记录删除前：释放 viewer 缓存的文档句柄，关闭引用被删文件的标签页。

        QPdfDocument 在 Windows 上持有文件句柄且被 viewer 会话缓存长期引用，
        被查看过的历史文件因此无法删除；此处先释放句柄再允许 HistoryPanel
        执行 unlink。同时关闭引用被删文件的文档标签页，避免残留失效引用。
        """
        paths: set[str] = set()
        for p in files:
            try:
                paths.add(str(Path(p).resolve()))
            except Exception:
                paths.add(str(p))

        # 1. 释放 QPdfDocument 句柄（Windows 文件锁根因）
        try:
            self._viewer.release_sessions(paths)
        except Exception:
            logger.debug("release_sessions failed", exc_info=True)

        # 2. 关闭引用被删文件的文档标签页（从后往前删，避免索引错乱）
        for i in range(len(self._doc_tabs) - 1, -1, -1):
            tab = self._doc_tabs[i]
            refs = [p for p in (tab.dual_pdf, tab.mono_pdf, tab.source_pdf) if p]
            try:
                hit = any(str(p.resolve()) in paths for p in refs)
            except Exception:
                hit = False
            if hit:
                self._close_doc_tab(i)

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

    def _on_history_selected(self, dual_path: str, mono_path: str, name: str,
                             source_path: str = "") -> None:
        """点击历史记录中的翻译 → 以新文档标签页打开该翻译结果。

        若 sidecar 记录了原文件路径且文件仍存在，则以原文件作为标签页的
        source（「原文」视图可切回原文档）；否则退化为仅译文展示。
        """
        target = dual_path or mono_path
        if not target:
            return
        path = Path(target)
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", f"历史文件已失效:\n{path}")
            return

        # 去重：该结果文件已作为标签打开则切换到已有标签页（按译文结果比对）
        for i, tab in enumerate(self._doc_tabs):
            result_files = [p for p in (tab.dual_pdf, tab.mono_pdf) if p]
            if any(p.resolve() == path.resolve() for p in result_files):
                self._activate_doc_tab(i)
                return

        # 历史标签页：优先绑定原文件（可切原文），否则仅译文
        src = Path(source_path) if source_path else None
        if src is not None and not src.exists():
            src = None
        tab = DocumentTab(
            title=name,
            source_pdf=src if src is not None else path,
            dual_pdf=Path(dual_path) if dual_path else None,
            mono_pdf=Path(mono_path) if mono_path else None,
            view="result",
            has_source=src is not None,
        )
        self._doc_tabs.append(tab)
        self._doc_tab_bar.add_tab(tab.title)
        self._activate_doc_tab(len(self._doc_tabs) - 1)
        self._settings.set_pdf_loaded(name, loaded=False)  # 历史结果不可再翻译
        self._settings.set_status(f"历史: {name}")

    def _on_output_mode_changed(self) -> None:
        """输出模式变更：按新配置刷新当前文档视图。

        输出模式是单一配置项：BabelDoc 精确翻译与粗糙翻译共用它。同一配置
        映射为两种呈现实现 —— 原文视图=dual 时用双视口粗糙翻译查看器；
        译文视图一律单视口渲染对应结果文件（dual 结果本身是双栏同页 PDF）。
        查看器类型的切换由 _apply_doc_view 的守卫自动完成（含会话迁移）。
        """
        tab = self._active_doc_tab()
        if tab is not None:
            self._apply_doc_view(tab)
        else:
            self._show_empty_state()
        mode = self._settings.output_mode_combo.currentData()
        label = "双语对照（原文+译文双栏）" if mode == "dual" else "纯译文（译文单栏）"
        self._settings.set_status(f"输出模式已切换为{label}")
