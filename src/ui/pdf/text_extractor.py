"""
PDF 文本异步提取器 — 使用 PyMuPDF 后台提取文本，支持取消和版本控制。

每次 load_pdf 递增 doc_id，后台任务携带 doc_id 返回，
主线程槽函数中比对 doc_id，丢弃过期结果。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool, QRectF
import fitz


@dataclass
class TextSpan:
    """单个文本片段"""
    page: int
    text: str
    pdf_x: float      # 左上角原点（PyMuPDF 坐标系）
    pdf_y: float
    pdf_width: float
    pdf_height: float
    font_size: float
    # 运行时由主线程填充
    content_rect: Optional[QRectF] = field(default=None)


class PdfTextExtractor(QObject):
    """
    异步 PDF 文本提取器，支持取消和版本控制。

    每次 load_pdf 递增 doc_id，后台任务携带 doc_id 返回，
    主线程槽函数中比对 doc_id，丢弃过期结果。
    """
    page_ready = Signal(int, list, int)   # page_num, spans, doc_id
    all_ready = Signal(int)               # doc_id
    error = Signal(str, int)              # message, doc_id

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._current_doc_id = 0
        self._cancelled = False

    def extract(self, path: str, doc_id: int):
        with self._lock:
            self._cancelled = True
            self._current_doc_id = doc_id
            self._cancelled = False

        worker = _ExtractWorker(path, doc_id, self)
        QThreadPool.globalInstance().start(worker)

    def is_valid(self, doc_id: int) -> bool:
        with self._lock:
            return not self._cancelled and self._current_doc_id == doc_id

    def cancel(self):
        with self._lock:
            self._cancelled = True


class _ExtractWorker(QRunnable):
    def __init__(self, path: str, doc_id: int, extractor: PdfTextExtractor):
        super().__init__()
        self.path = path
        self.doc_id = doc_id
        self.extractor = extractor
        self.setAutoDelete(True)

    def run(self):
        try:
            doc = fitz.open(self.path)
            for i in range(len(doc)):
                if not self.extractor.is_valid(self.doc_id):
                    doc.close()
                    return

                page = doc[i]
                spans: List[TextSpan] = []

                blocks = page.get_text("dict")["blocks"]
                for block in blocks:
                    if block.get("type") != 0:
                        continue
                    for line in block["lines"]:
                        for span in line["spans"]:
                            bbox = span["bbox"]
                            spans.append(TextSpan(
                                page=i,
                                text=span["text"],
                                pdf_x=bbox[0],
                                pdf_y=bbox[1],
                                pdf_width=bbox[2] - bbox[0],
                                pdf_height=bbox[3] - bbox[1],
                                font_size=span["size"]
                            ))

                if self.extractor.is_valid(self.doc_id):
                    self.extractor.page_ready.emit(i, spans, self.doc_id)

            doc.close()
            if self.extractor.is_valid(self.doc_id):
                self.extractor.all_ready.emit(self.doc_id)

        except Exception as e:
            if self.extractor.is_valid(self.doc_id):
                self.extractor.error.emit(str(e), self.doc_id)
