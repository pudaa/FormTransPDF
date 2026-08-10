# -*- coding: utf-8 -*-
import os, sys, tempfile, shutil
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"D:/Codes/FormTransPDF")
from pathlib import Path
import time
from unittest.mock import patch
from PySide6.QtWidgets import QApplication

app = QApplication([])
tmp = Path(tempfile.mkdtemp(prefix="ftpdf_lock2_"))

def make_pdf(name):
    p = tmp / name
    import fitz
    d = fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), name); d.save(str(p)); d.close()
    return p

from src.ui.pdf.viewer import PDFViewer
viewer = PDFViewer()

# 1) 当前文档：查看 → release → 可删
pdf = make_pdf("paperA.zh.dual.pdf")
viewer.load_pdf(str(pdf))
app.processEvents(); time.sleep(0.2); app.processEvents()
try:
    pdf.unlink()
    print("[警告] 未被占用，无法复现")
except OSError as e:
    print("[复现] 查看后占用:", type(e).__name__)
viewer.release_sessions([str(pdf)])
app.processEvents()
pdf.unlink()
assert str(pdf) not in viewer._sessions
print("[PASS] 当前文档 release 后删除成功")

# 2) 非当前缓存会话：切换文档后释放
pdf2 = make_pdf("paperB.zh.dual.pdf")
viewer.load_pdf(str(pdf2))
app.processEvents(); time.sleep(0.2); app.processEvents()
pdf3 = make_pdf("paperC.pdf")
viewer.load_pdf(str(pdf3))
app.processEvents(); time.sleep(0.2); app.processEvents()
assert str(pdf2) in viewer._sessions
try:
    pdf2.unlink()
    print("[警告] pdf2 未被占用")
except OSError:
    print("[复现] 非当前缓存会话也占用文件")
viewer.release_sessions([str(pdf2)])
app.processEvents()
pdf2.unlink()
print("[PASS] 非当前缓存会话 release 后删除成功")

# 3) 未加载路径无副作用
viewer.release_sessions([str(tmp / "nope.pdf")])
print("[PASS] 未加载路径无副作用")

# 4) 释放当前文档后 viewer 可继续加载新文档（无悬垂引用崩溃）
pdf4 = make_pdf("paperD.zh.dual.pdf")
viewer.load_pdf(str(pdf4))
app.processEvents()
viewer.release_sessions([str(pdf4)])
app.processEvents()
pdf5 = make_pdf("paperE.pdf")
viewer.load_pdf(str(pdf5))
app.processEvents()
assert viewer.document is not None
print("[PASS] 释放后重新加载正常（无崩溃）")

# 5) HistoryPanel 信号 → MainWindow 联动全链路
from src.ui.widgets.history_panel import HistoryPanel, QMessageBox
from src.ui.windows.main_window import MainWindow
win = MainWindow()
win.show()
pdf6 = make_pdf("paperF.zh.dual.pdf")
# 打开历史标签并加载到 viewer
from src.ui.widgets.document_tab_bar import DocumentTab
win._doc_tabs.append(DocumentTab(title="paperF", source_pdf=pdf6, dual_pdf=pdf6, view="result", has_source=False))
win._doc_tab_bar.add_tab("paperF")
win._activate_doc_tab(len(win._doc_tabs) - 1)
win._viewer.load_pdf(str(pdf6))
app.processEvents(); time.sleep(0.2); app.processEvents()
assert len(win._doc_tabs) == 1
# 通过 HistoryPanel 走真实删除路径（信号链：about_to_delete_files → _on_history_delete_files）
panel = HistoryPanel(tmp)
panel.show()
panel.refresh()
with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
    # 找到 paperF 记录
    entry = next(e for e in panel._entries if "paperF" in e.display_name)
    panel._confirm_and_delete([entry])
app.processEvents()
assert not pdf6.exists(), "全链路删除后文件应被删除"
assert len(win._doc_tabs) == 0, "引用被删文件的标签应被关闭"
assert str(pdf6) not in win._viewer._sessions
print("[PASS] 全链路：查看历史 → 删除 → 句柄释放 + 标签关闭 + 文件删除成功")
win.close()

shutil.rmtree(tmp, ignore_errors=True)
print("\n文件占用修复完整测试通过")
