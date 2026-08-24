"""大文档性能修复测试：提取去抖合并 / 可见窗口坐标更新 / 缩略图分批生成。"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtTest import QTest

from src.ui.pdf.layout_engine import PageLayout
from src.ui.pdf.viewer import PDFViewer
from src.ui.widgets.minimap import MinimapPanel, ThumbnailGenerator


def _make_pdf(path: Path, pages: int) -> Path:
    """生成带文本行的合成 PDF（供真实加载路径使用）。"""
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 72), f"Page {i + 1} - The quick brown fox jumps.")
        pg.insert_text((72, 96), "Second line for segment clustering.")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def small_pdf(tmp_path):
    return _make_pdf(tmp_path / "small.pdf", 3)


def _wait_extracted(viewer: PDFViewer, timeout_ms: int = 8000) -> bool:
    waited = 0
    while not viewer.text_layer_done and waited < timeout_ms:
        QTest.qWait(50)
        waited += 50
    return viewer.text_layer_done


# ── 提取信号去抖：N 页到达只触发一次全量重算 ────────────────

def test_extract_refresh_coalesces(qapp, small_pdf):
    viewer = PDFViewer()
    viewer.resize(900, 700)
    viewer.load_pdf(str(small_pdf))
    assert _wait_extracted(viewer), "文本层应就绪"

    # 用计数器替换视口重算入口（重连去抖定时器）
    calls = {"n": 0}
    orig = viewer._on_viewport_changed

    def counting() -> None:
        calls["n"] += 1
        orig()

    viewer._on_viewport_changed = counting  # type: ignore[method-assign]
    viewer._extract_refresh.timeout.disconnect(orig)
    viewer._extract_refresh.timeout.connect(counting)

    # 同步灌入 200 个页到达事件（模拟大文档提取风暴）
    for i in range(200):
        viewer._on_text_page_ready(i, [], viewer._doc_id)
    assert calls["n"] == 0, "去抖期内不应立即重算"

    QTest.qWait(viewer._extract_refresh.interval() + 80)
    assert calls["n"] == 1, f"200 页应合并为 1 次重算，实际 {calls['n']}"
    assert len(viewer._cover_segments) == 200, "轻量存储不丢页"


# ── 坐标更新限定可见窗口 ────────────────────────────────────

def test_visible_window_limits_updates(qapp, small_pdf):
    viewer = PDFViewer()
    viewer.resize(900, 700)
    viewer.load_pdf(str(small_pdf))
    assert _wait_extracted(viewer)

    # 桩掉布局引擎：300 页、每页高 900，纵向铺开
    fake = [
        PageLayout(page_num=i, rect=QRectF(0, i * 900, 800, 850), scale=1.0)
        for i in range(300)
    ]
    viewer._layout_engine.compute_layout = lambda w, h: fake  # type: ignore[method-assign]

    seen: list[int] = []
    orig_spans = viewer._update_page_spans

    def spy(layout):
        seen.append(layout.page_num)
        orig_spans(layout)

    viewer._update_page_spans = spy  # type: ignore[method-assign]

    viewer.verticalScrollBar().setValue(0)
    viewer._on_viewport_changed()

    assert seen, "首屏附近页面必须被更新"
    assert len(seen) < 60, f"应只更新可见窗口附近页面，实际 {len(seen)}"
    assert 0 in seen
    assert all(p < 60 for p in seen), f"远端页面不应被更新: max={max(seen)}"


# ── 缩略图分批生成器 ────────────────────────────────────────

def test_thumbnail_generator_batches(qapp, tmp_path):
    pdf = _make_pdf(tmp_path / "thumbs.pdf", 7)
    from PySide6.QtPdf import QPdfDocument
    doc = QPdfDocument()
    doc.load(str(pdf))
    assert doc.status() == QPdfDocument.Status.Ready

    gen = ThumbnailGenerator(doc, 7, thumb_scale=0.16,
                             batch_size=3, interval_ms=10, parent=qapp)
    received: dict[int, object] = {}
    finished = {"done": False}
    gen.batch_ready.connect(lambda s, lst: received.update(
        {s + i: p for i, p in enumerate(lst)}
    ))
    gen.finished.connect(lambda: finished.update(done=True))
    gen.start()

    deadline = 5000
    while not finished["done"] and deadline > 0:
        QTest.qWait(50)
        deadline -= 50

    assert finished["done"], "生成器应在超时前完成"
    assert sorted(received.keys()) == list(range(7))
    for pix in received.values():
        assert not pix.isNull()

    # stop() 后不再产生新批次
    gen2 = ThumbnailGenerator(doc, 7, thumb_scale=0.16,
                              batch_size=2, interval_ms=10, parent=qapp)
    got2: list[int] = []
    gen2.batch_ready.connect(lambda s, lst: got2.append(s))
    gen2.start()
    gen2.stop()
    QTest.qWait(80)
    assert got2 == [], "stop 后不应有批次到达"


# ── 面板占位加载与增量回填 ──────────────────────────────────

def test_minimap_begin_load_placeholders(qapp):
    panel = MinimapPanel(None)
    panel.resize(panel.PANEL_WIDTH, 600)
    panel.begin_load(50, QSize(595, 842))

    assert panel.page_count == 50
    assert len(panel._thumbnails) == 50
    assert len({t.size().height() for t in panel._thumbnails}) == 1, "占位图尺寸一致"
    assert panel._content_height > 0

    pix = panel._thumbnails[0]
    panel.set_thumbnail(3, pix)
    assert not panel._page_pixmaps[3].isNull()
    assert len(panel._page_pixmaps) == 50
