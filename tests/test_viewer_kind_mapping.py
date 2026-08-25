"""输出模式 → 查看器类型映射回归测试。

背景：输出模式配置合并后（粗糙翻译与 BabelDoc 共用一个"双栏/纯译文"
配置），同一配置需映射为两种呈现实现：
- 粗糙翻译（原文视图）：dual = 左右双视口（DualRoughViewer）；
- BabelDoc（译文视图）：dual 结果本身是"原文+译文同页双栏"的单个 PDF，
  必须用单视口渲染 —— 旧实现把该文件塞进双栏查看器导致同一文件显示两遍。

预期切换流（用户规格）：
  译文(BabelDoc dual，单视口) → 原文(dual 配置下重建为双视口)
  → 译文(回到单视口渲染双栏 PDF)；粗译按钮在译文视图点击时先切回原文。
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from src.ui.widgets.document_tab_bar import DocumentTab


def _make_pdf(path: Path, text: str) -> Path:
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def pdfs(tmp_path):
    return {
        "source": _make_pdf(tmp_path / "src.pdf", "Original source text."),
        "dual": _make_pdf(tmp_path / "dual.pdf", "Original | Translation"),
        "mono": _make_pdf(tmp_path / "mono.pdf", "Translation only."),
    }


@pytest.fixture
def window(monkeypatch, qapp):
    """最小化启动的 MainWindow（跳过引擎后台加载）。"""
    from src.ui.windows.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_start_engine_load", lambda self: None)
    w = MainWindow()
    w.resize(1200, 900)
    w.show()
    yield w
    w.close()


def _set_output_mode(window, mode: str) -> None:
    combo = window._settings.output_mode_combo
    idx = combo.findData(mode)
    assert idx >= 0
    combo.setCurrentIndex(idx)


def _load_source(window, src: Path) -> DocumentTab:
    window._load_pdf(str(src))
    tab = window._active_doc_tab()
    assert tab is not None
    return tab


def test_mapping_pure_logic(window):
    """_required_viewer_kind：译文视图一律单栏；原文视图跟随配置。"""
    from src.ui.pdf.dual_viewer import DualRoughViewer

    # 无标签页时跟随配置
    _set_output_mode(window, "dual")
    assert window._required_viewer_kind() == "dual"

    tab = DocumentTab(title="t", source_pdf=None, view="result", has_source=False)
    window._doc_tabs.append(tab)
    window._active_doc_index = 0
    assert window._required_viewer_kind() == "mono", "译文视图必须单视口"

    tab.view = "source"
    assert window._required_viewer_kind() == "dual"

    _set_output_mode(window, "mono")
    assert window._required_viewer_kind() == "mono"


def test_dual_config_source_result_toggle(qapp, window, pdfs):
    """dual 配置：原文视图=双视口 ⇄ 译文视图=单视口渲染双栏 PDF。"""
    from src.ui.pdf.dual_viewer import DualRoughViewer
    from src.ui.pdf.viewer import PDFViewer

    _set_output_mode(window, "dual")
    tab = _load_source(window, pdfs["source"])
    assert isinstance(window._viewer, DualRoughViewer), "原文视图应为双视口查看器"

    tab.dual_pdf = pdfs["dual"]
    tab.mono_pdf = pdfs["mono"]

    # 切到译文：单视口 + 加载的是 BabelDoc 双栏结果文件本身
    window._set_active_view("result")
    QTest_qwait = 50
    qapp.processEvents()
    assert isinstance(window._viewer, PDFViewer), "译文视图必须单视口（BabelDoc dual 结果是单个 PDF）"
    assert window._viewer_kind == "mono"
    assert window._viewer._source_path == str(pdfs["dual"])

    # 切回原文：重建为双视口（粗糙翻译工作区）
    window._set_active_view("source")
    qapp.processEvents()
    assert isinstance(window._viewer, DualRoughViewer)
    assert window._viewer_kind == "dual"


def test_mono_config_always_single(qapp, window, pdfs):
    """mono 配置：任何视图都是单视口。"""
    from src.ui.pdf.viewer import PDFViewer

    _set_output_mode(window, "mono")
    tab = _load_source(window, pdfs["source"])
    assert isinstance(window._viewer, PDFViewer)

    tab.dual_pdf = pdfs["dual"]
    tab.mono_pdf = pdfs["mono"]
    window._set_active_view("result")
    qapp.processEvents()
    assert isinstance(window._viewer, PDFViewer)
    # mono 模式优先展示纯译文文件
    assert window._viewer._source_path == str(pdfs["mono"])


def test_rough_button_from_result_view_rebuilds_safely(qapp, window, pdfs):
    """译文视图点粗译：先切回原文（触发重建），不得使用失效的旧 viewer 引用。"""
    from src.ui.pdf.dual_viewer import DualRoughViewer

    _set_output_mode(window, "dual")
    tab = _load_source(window, pdfs["source"])
    tab.dual_pdf = pdfs["dual"]
    tab.mono_pdf = pdfs["mono"]

    window._set_active_view("result")
    qapp.processEvents()
    old_viewer = window._viewer

    # 模拟在译文视图点击「粗译」按钮
    window._on_rough_toggled(True)
    qapp.processEvents()

    assert tab.view == "source", "粗译必须基于原文视图"
    assert window._viewer is not old_viewer, "应重建为双视口查看器"
    assert isinstance(window._viewer, DualRoughViewer)


def test_sessions_survive_kind_switch(qapp, window, pdfs):
    """类型切换经会话池迁移：文本层不重新提取（extraction_complete 保持）。"""
    from PySide6.QtTest import QTest

    _set_output_mode(window, "dual")
    tab = _load_source(window, pdfs["source"])
    waited = 0
    while not window._viewer.text_layer_done and waited < 8000:
        QTest.qWait(50)
        waited += 50
    assert window._viewer.text_layer_done, "首次提取应完成"

    tab.dual_pdf = pdfs["dual"]
    tab.mono_pdf = pdfs["mono"]
    window._set_active_view("result")
    qapp.processEvents()
    window._set_active_view("source")
    qapp.processEvents()

    sess = window._rough_sessions.get(str(pdfs["source"]))
    assert sess is not None and sess.get("extraction_complete"), (
        "切换单/双视口后共享会话必须保留提取状态（避免重复提取/丢失划词层）"
    )
