"""历史原文路径全链路 + 粗糙导出验证。"""
import os
import sys
import json
import shutil
import tempfile
import glob
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"D:/Codes/FormTransPDF")

from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)

# ── 1. records round-trip with source_path ──
from src.core.translation.records import (
    SourceInfo, write_source_info, read_source_info, sidecar_path
)
tmpdir = Path(tempfile.mkdtemp(prefix="ftpdf_records_"))
try:
    info = SourceInfo(
        source_hash="abc", source_bytes_hash="def", source_name="paper.pdf",
        source_path=r"D:\Zotero\paper.pdf",
        lang_in="en", lang_out="zh",
    )
    write_source_info(sidecar_path(tmpdir, "paper"), info)
    loaded = read_source_info(sidecar_path(tmpdir, "paper"))
    print("[1] records round-trip source_path:", loaded.source_path, "->", loaded.source_path == r"D:\Zotero\paper.pdf")

    # ── 2. HistoryPanel.scan 解析含 source_path 的 sidecar ──
    from src.ui.widgets.history_panel import HistoryPanel
    out = Path(tempfile.mkdtemp(prefix="ftpdf_hist_"))
    # 模拟历史：dual/mono 译文 + sidecar
    (out / "paper.zh.dual.pdf").write_bytes(b"%PDF-1.4 dummy")
    (out / "paper.zh.mono.pdf").write_bytes(b"%PDF-1.4 dummy")
    write_source_info(sidecar_path(out, "paper"), info)
    panel = HistoryPanel(out)
    panel.refresh()  # 扫描
    entries = panel._entries
    assert len(entries) == 1, f"expected 1 entry, got {len(entries)}"
    e = entries[0]
    print("[2] history entry source_path:", e.source_path)
    print("[2] dual/mono:", e.dual_pdf.name if e.dual_pdf else None, "|", e.mono_pdf.name if e.mono_pdf else None)

    # ── 3. 模拟 _on_history_selected 构造 tab（直接执行其核心逻辑）──
    src = Path(e.source_path)
    has_real_source = src.exists()
    print("[3] src file exists:", has_real_source, "(路径 D:/Zotero/paper.pdf 在本测试机不存在，应退化为仅译文)")

    # 路径存在则 source_pdf=src, has_source=True；否则 source_pdf=译文文件, has_source=False
    # 直接调用 history_flow._on_history_selected 验证（需要 Mixin 实例，简化：手动复制逻辑）
    from src.ui.widgets.document_tab_bar import DocumentTab
    tab = DocumentTab(
        title=e.display_name,
        source_pdf=src if has_real_source else e.dual_pdf,
        dual_pdf=e.dual_pdf, mono_pdf=e.mono_pdf,
        view="result",
        has_source=has_real_source,
    )
    print("[3] tab.source_pdf:", tab.source_pdf.name, "| has_source:", tab.has_source)

    # ── 4. viewer.rough_export_text ──
    from types import SimpleNamespace
    from src.ui.pdf.cover import build_segments, COVER_TRANSLATED
    from src.ui.pdf.text_overlay import TextOverlay
    spans = [
        SimpleNamespace(page=0, text="原文段 0",
                        pdf_x=10, pdf_y=10, pdf_width=200, pdf_height=20,
                        font_size=12, block_id=0, content_rect=None),
        SimpleNamespace(page=0, text="原文段 1",
                        pdf_x=10, pdf_y=50, pdf_width=200, pdf_height=20,
                        font_size=12, block_id=1, content_rect=None),
    ]
    segs = build_segments(spans)
    segs[0].display_text = "译文段 0"
    # segs[1] 无译文 → 回退原文
    from PySide6.QtCore import QRectF
    for seg in segs:
        seg.content_rect = QRectF(10, 10, 200, 20) if seg.pdf_y == 10 else QRectF(10, 50, 200, 20)
    from PySide6.QtGui import QFontDatabase
    for fp in glob.glob('C:/Windows/Fonts/msyh.ttc') + glob.glob('C:/Windows/Fonts/segoeui.ttf'):
        QFontDatabase.addApplicationFont(fp)
    ov = TextOverlay()
    ov.set_cover({0: segs}, {0: type('L',(),{'rect':QRectF(0,0,1000,500),'scale':1.0})()}, bump=True)
    ov.set_cover_mode(COVER_TRANSLATED)
    # 用 viewer 的 rough_export_text 接口（用 TextOverlay 没有该方法，用 cover 内的覆盖段）
    # 构造一个模拟 viewer
    from src.ui.pdf.viewer import PDFViewer
    v = PDFViewer.__new__(PDFViewer)
    v._cover_segments = {0: segs}
    v._rough_translations = {(0, 0): "译文段 0"}
    txt = v.rough_export_text()
    print("[4] rough export text:")
    for ln in txt.splitlines()[:6]:
        print("    " + ln)
    assert "译文段 0" in txt
    assert "原文段 1" in txt  # 未译段回退
    assert "── 第 1 页 ──" in txt
    print("[4] OK: 译文 + 原文回退 + 页分隔 全部正确")

    shutil.rmtree(out, ignore_errors=True)
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
print("REGRESSION PASS")