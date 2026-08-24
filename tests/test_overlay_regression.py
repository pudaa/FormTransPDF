"""透明文本层（划词）回归测试：共享会话 spans 生命周期。

背景 bug：viewer.shutdown() 无条件 _text_spans.clear()，而缓存激活后
_text_spans 是共享 session["spans"] 的引用 —— 布局切换/重建 viewer 时
清空了会话池中的文本数据，且 extraction_complete 仍为 True 导致不再
重新提取 → 双栏左栏 / 取消粗译后无法划词复制原文。
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtTest import QTest

from src.ui.pdf.dual_viewer import DualRoughViewer
from src.ui.pdf.viewer import PDFViewer


def _make_pdf(path: Path, pages: int = 2) -> Path:
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 72), f"Page {i + 1} - transparent text layer test.")
        pg.insert_text((72, 96), "Selectable original text line.")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def small_pdf(tmp_path):
    return _make_pdf(tmp_path / "small.pdf")


def _wait_extracted(viewer: PDFViewer, timeout_ms: int = 8000) -> bool:
    waited = 0
    while not viewer.text_layer_done and waited < timeout_ms:
        QTest.qWait(50)
        waited += 50
    return viewer.text_layer_done


def _hit_count(viewer, top_ratio: float = 0.34) -> int:
    """顶部区域划词命中字符数（模拟用户在页首选原文）。"""
    vp = viewer.viewport()
    rect = QRectF(0, 0, vp.width(), vp.height() * top_ratio)
    content = rect.translated(
        viewer.horizontalScrollBar().value(),
        viewer.verticalScrollBar().value(),
    )
    return len(viewer._get_text_in_rect(content))


def test_shutdown_preserves_shared_spans(qapp, small_pdf):
    """shutdown 不得清空共享会话的 spans（布局切换后文本层丢失根因）。"""
    v1 = PDFViewer()
    v1.resize(900, 700)
    v1.load_pdf(str(small_pdf))
    assert _wait_extracted(v1), "首次提取应完成"
    assert _hit_count(v1) > 0, "基准：单栏应可划词"

    pool = v1.export_sessions()
    v1.shutdown()  # ← 曾在此处清空共享 spans

    assert pool and next(iter(pool.values()))["spans"], (
        "shutdown 后会话池中的 spans 数据必须保留"
    )


def test_rebuild_viewer_keeps_selection_alive(qapp, small_pdf):
    """模拟「单栏 → 双栏」布局切换：新 viewer 缓存激活后立即可划词。"""
    v1 = PDFViewer()
    v1.resize(900, 700)
    v1.load_pdf(str(small_pdf))
    assert _wait_extracted(v1)
    pool = v1.export_sessions()
    v1.shutdown()

    dual = DualRoughViewer()
    dual.resize(1200, 900)
    dual.inject_sessions(pool)
    dual.load_pdf(str(small_pdf))
    QTest.qWait(150)  # 延迟刷新兜底落地

    for tag, pane in (("左栏(原文)", dual._left), ("右栏", dual._right)):
        assert _hit_count(pane) > 0, f"缓存激活后{tag}应可划词"
        assert pane.text_layer_done, f"{tag} text_layer_done 应动态反映会话状态"

    # 粗译切换往返后划词依旧可用（取消粗译场景）
    dual.set_cover_mode("translated")
    QTest.qWait(50)
    dual.set_cover_mode("transparent")
    QTest.qWait(50)
    assert _hit_count(dual._right) > 0, "取消粗译后右栏应恢复原文划词"


def test_dual_text_layer_ready_forwarded(qapp, small_pdf):
    """双栏下提取由左栏执行，text_layer_ready 必须转发到 DualRoughViewer。"""
    dual = DualRoughViewer()
    dual.resize(1200, 900)
    fired = []
    dual.text_layer_ready.connect(lambda: fired.append(True))
    dual.show()
    dual.load_pdf(str(small_pdf))
    waited = 0
    while not fired and waited < 8000:
        QTest.qWait(50)
        waited += 50
    assert fired, "左栏提取完成后主窗口必须收到 text_layer_ready"
