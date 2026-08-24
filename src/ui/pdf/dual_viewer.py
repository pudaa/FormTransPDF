"""
双栏粗糙翻译查看器 — 左栏原文（透明层）+ 右栏译文（覆盖层）。

复用两个 PDFViewer（文档会话缓存 / 文本层 / 覆盖层渲染全部复用）：
- 右栏是"逻辑主栏"：承担粗糙翻译的启动 / 状态 / 覆盖层渲染；
- 左栏始终为原文透明层（可划词）；
- 两栏绑定同一 PDF 路径，滚动按比例同步（带防回环 guard）。

对外 API 与单栏 PDFViewer 对齐，MainWindow 无需区分单/双栏。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QSplitter, QWidget

from src.ui.pdf.viewer import PDFViewer


class DualRoughViewer(QWidget):
    """双栏查看器：原文 | 译文（对照阅读）。"""

    # 与 PDFViewer 对齐的信号（转发左右两栏 + 右栏粗糙翻译状态）
    text_selected = Signal(str)
    translate_requested = Signal(str)
    rough_status = Signal(str)
    rough_ready = Signal()
    rough_progress = Signal(int, int)
    rough_stats = Signal(int, int)
    text_layer_ready = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._left = PDFViewer(self)
        self._right = PDFViewer(self)

        # 左右栏共享同一份会话存储：同文件只提取一次、只持有一份 QPdfDocument，
        # 粗糙翻译状态（右栏写入）对两栏同时可见
        shared: dict = {}
        self._left.set_shared_sessions(shared)
        self._right.set_shared_sessions(shared)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._left)
        splitter.addWidget(self._right)
        splitter.setSizes([500, 500])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter)
        self._splitter = splitter

        # 信号转发：任一侧划词都可即时翻译；粗糙翻译状态来自右栏。
        # text_layer_ready 两栏都转发：双栏下提取任务由左栏承担（load_pdf
        # 先左后右，右栏命中共享会话跳过），只转发右栏会导致主窗口永远
        # 收不到"文本层就绪"通知（历史粗译缓存不加载 / 按钮态不同步）。
        for src, dst in (
            (self._left.text_selected, self.text_selected),
            (self._right.text_selected, self.text_selected),
            (self._left.translate_requested, self.translate_requested),
            (self._right.translate_requested, self.translate_requested),
            (self._right.rough_status, self.rough_status),
            (self._right.rough_ready, self.rough_ready),
            (self._right.rough_progress, self.rough_progress),
            (self._right.rough_stats, self.rough_stats),
            (self._left.text_layer_ready, self.text_layer_ready),
            (self._right.text_layer_ready, self.text_layer_ready),
        ):
            src.connect(dst)

        self._syncing = False
        self._connect_scroll_sync()

    # ── 滚动同步 ───────────────────────────────────────────

    def _connect_scroll_sync(self) -> None:
        for src, dst in (
            (self._left.verticalScrollBar(), self._right.verticalScrollBar()),
            (self._right.verticalScrollBar(), self._left.verticalScrollBar()),
            (self._left.horizontalScrollBar(), self._right.horizontalScrollBar()),
            (self._right.horizontalScrollBar(), self._left.horizontalScrollBar()),
        ):
            src.valueChanged.connect(
                lambda _v, _s=src, _d=dst: self._sync_scroll(_s, _d)
            )

    def _sync_scroll(self, src, dst) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            span = src.maximum() - src.minimum()
            if span > 0:
                ratio = (src.value() - src.minimum()) / span
                dst_span = dst.maximum() - dst.minimum()
                dst.setValue(dst.minimum() + int(ratio * dst_span))
        finally:
            self._syncing = False

    # ── 文档 / 缩放（委托左栏，右栏同步）──────────────────

    def inject_sessions(self, pool: dict) -> None:
        """注入共享会话缓存（右栏承担粗糙翻译，译文随右栏会话保留）。"""
        self._left.inject_sessions(pool)
        self._right.inject_sessions(pool)

    def export_sessions(self) -> dict:
        """导出会话缓存：以右栏为准（右栏含完整译文；左栏同路径 doc 不覆盖右栏）。"""
        return self._right.export_sessions()

    @property
    def document(self):
        return self._left.document

    @property
    def page_count(self) -> int:
        return self._left.page_count

    @property
    def is_fit_width(self) -> bool:
        return self._left.is_fit_width

    @property
    def scale(self) -> float:
        return self._left.scale

    @property
    def content_widget(self):
        return self._left.content_widget

    def viewport(self):
        return self._left.viewport()

    def verticalScrollBar(self):
        return self._left.verticalScrollBar()

    def horizontalScrollBar(self):
        return self._left.horizontalScrollBar()

    def load_pdf(self, path: str) -> None:
        self._left.load_pdf(path)
        self._right.load_pdf(path)

    def release_sessions(self, paths) -> None:
        """释放左右两栏缓存的文档句柄（删除历史文件前调用）。"""
        self._left.release_sessions(paths)
        self._right.release_sessions(paths)

    def clear(self) -> None:
        self._left.clear()
        self._right.clear()

    def shutdown(self) -> None:
        self._left.shutdown()
        self._right.shutdown()

    def refresh_theme(self) -> None:
        self._left.refresh_theme()
        self._right.refresh_theme()

    def zoom_in(self) -> None:
        self._left.zoom_in()
        self._right.zoom_in()

    def zoom_out(self) -> None:
        self._left.zoom_out()
        self._right.zoom_out()

    def zoom_reset(self) -> None:
        self._left.zoom_reset()
        self._right.zoom_reset()

    def goto_page(self, page_number: int) -> None:
        self._left.goto_page(page_number)
        self._right.goto_page(page_number)

    # ── 粗糙翻译（委托右栏；左栏保持原文透明）────────────

    @property
    def cover_mode(self) -> str:
        return self._right.cover_mode

    @property
    def text_layer_done(self) -> bool:
        return self._right.text_layer_done

    def has_rough_segments(self) -> bool:
        return self._right.has_rough_segments()

    def rough_segment_count(self) -> int:
        return self._right.rough_segment_count()

    def has_rough_translations(self) -> bool:
        return self._right.has_rough_translations()

    def rough_export_text(self) -> str:
        return self._right.rough_export_text()

    def rough_is_running(self) -> bool:
        return self._right.rough_is_running()

    def rough_progress_counts(self) -> tuple[int, int]:
        return self._right.rough_progress_counts()

    def collect_rough_result(self) -> dict:
        return self._right.collect_rough_result()

    def apply_rough_translations(self, translations: dict) -> int:
        return self._right.apply_rough_translations(translations)

    def set_cover_mode(self, mode: str) -> None:
        # 左栏始终原文透明层；右栏承载译文覆盖层
        self._right.set_cover_mode(mode)

    def start_rough_translation(self, profile, lang_in: str, lang_out: str) -> bool:
        return self._right.start_rough_translation(profile, lang_in, lang_out)

    def cancel_rough_translation(self) -> None:
        self._right.cancel_rough_translation()
