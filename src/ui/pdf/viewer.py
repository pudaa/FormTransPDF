"""
PDF 页面渲染与查看组件 — 基于 QPdfView (PySide6 QtPdf)
新增：双层渲染文本选中、异步文本提取、精准坐标映射

特性：
  - QPdfView 原生渲染 + 零侵入式文本选择（左键拖选，选中后弹出浮动工具栏）
  - 透明覆盖层绘制高亮，PyMuPDF 后台异步提取文本
  - doc_id 版本控制保证切换 PDF 时线程安全
  - 信号驱动视口跟踪（无 QTimer），实时同步文本坐标
  - MultiPage 连续滚动 + FitToWidth/Custom 缩放
  - 空状态 placeholder 提示
  - 中键拖拽平移（事件过滤器实现）
"""

from __future__ import annotations

import logging
from pathlib import Path

import shiboken6
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent, QMargins, QRectF, QTimer, Signal
from PySide6.QtGui import (
    QColor, QMouseEvent, QPalette, QWheelEvent, QScreen,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from src.ui.base.theme import Colors, theme_manager
from src.ui.pdf.layout_engine import PdfLayoutEngine, PageLayout
from src.ui.pdf.text_extractor import PdfTextExtractor, TextSpan
from src.ui.pdf.text_overlay import TextOverlay
from src.ui.pdf.selection import TextSelectionMixin
from src.ui.pdf.cover import (
    build_segments,
    COVER_TRANSPARENT,
    COVER_ORIGINAL,
    COVER_TRANSLATED,
)
from src.core.translation.rough import RoughTranslator

logger = logging.getLogger(__name__)


class PDFViewerCore(QWidget):
    """PDF 查看器核心 — QPdfView + 文档生命周期 + 缩放 + 视口跟踪。

    StackedLayout:
        [0] placeholder — 无 PDF 时展示
        [1] QPdfView   — 原生渲染图像层
            └── viewport() → TextOverlay 透明覆盖层（高亮/工具栏）

    架构：
        - PdfLayoutEngine: 计算每页在内容坐标系中的 QRect
        - PdfTextExtractor: 后台线程提取文本，带 doc_id 版本控制
        - 文本选择 / 平移交互：TextSelectionMixin（selection.py），
          由最终类 PDFViewer 组合
    """

    # ========== 信号 ==========
    text_selected = Signal(str)          # 当选中文本时发射
    translate_requested = Signal(str)    # 浮动工具栏「翻译」→ 主窗口即时翻译
    rough_status = Signal(str)           # 粗糙翻译状态（进度/完成/异常）
    rough_ready = Signal()               # 粗糙翻译全部完成
    rough_progress = Signal(int, int)    # 粗糙翻译进度（done, total，含最终失败段）
    rough_stats = Signal(int, int)       # 粗糙翻译结束统计（成功段数, 失败段数）
    text_layer_ready = Signal()          # 后台文本提取完成（文本层就绪）

    DEFAULT_SCALE = 1.0
    MIN_SCALE = 0.25
    MAX_SCALE = 8.0
    # 会话缓存上限（超出按插入序淘汰最旧；当前会话不淘汰）
    MAX_SESSIONS = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._doc: QPdfDocument | None = None
        self._scale: float = self.DEFAULT_SCALE
        self._fit_width: bool = True
        self._pages: list = []  # 兼容旧 API

        # ── StackedLayout ──
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedLayout()
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(0)
        root.addLayout(self._stack)

        # ① placeholder 空状态提示
        self._placeholder = QLabel("拖拽 PDF 文件到此处")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self._placeholder)  # index 0

        # ② QPdfView 渲染区域
        self._pdf_view = QPdfView()
        self._pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self._pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        # 页面间距设为 0：消除页与页之间的空隙（用户要求），同时天然保证
        # 布局引擎与 QPdfView 的页间距完全一致（引擎动态读取 pageSpacing()）
        self._pdf_view.setPageSpacing(0)
        self._pdf_view.setDocumentMargins(QMargins(0, 0, 0, 0))
        self._pdf_view.setViewportMargins(QMargins(0, 0, 0, 0))
        self._stack.addWidget(self._pdf_view)  # index 1

        # ③ 透明覆盖层（放在 viewport 上）
        self._text_overlay = TextOverlay(self._pdf_view.viewport())
        self._text_overlay.hide()

        # 布局引擎 & 异步提取器
        self._layout_engine = PdfLayoutEngine(self._pdf_view)
        self._text_extractor = PdfTextExtractor()
        self._text_extractor.page_ready.connect(self._on_text_page_ready)
        self._text_extractor.all_ready.connect(self._on_text_all_ready)

        # 文本选中状态
        self._doc_id = 0
        self._text_spans: dict[int, list[TextSpan]] = {}  # page -> spans
        self._selecting = False
        self._select_start = QPoint()
        self._selected_text = ""
        self._selected_content_rect: QRectF | None = None  # 内容坐标系中的选区
        self._drag_threshold_met = False  # 拖拽超过阈值才进入选择模式

        # ── 粗糙翻译（覆盖层）状态 ──
        self._sessions: dict[str, dict] = {}   # path -> 文档会话（doc+spans+segments+译态）
        self._shared_store: dict | None = None  # 双栏共享会话存储（非 None 时 _sessions 即它）
        self._session: dict | None = None      # 当前激活会话
        self._source_path: str | None = None
        self._cover_segments: dict[int, list] = {}   # page -> [CoverSegment]
        self._rough_translations: dict = {}          # (page, idx) -> 译文
        self._cover_mode: str = COVER_TRANSPARENT
        self._last_layouts: list = []
        self._text_layer_done: bool = False          # 当前文档文本提取是否完成
        self._rough = RoughTranslator(self)
        self._rough.segment_done.connect(self._on_rough_segment)
        self._rough.page_done.connect(self._on_rough_page)
        self._rough.progress.connect(self._on_rough_progress)
        self._rough.stats.connect(self._on_rough_stats)
        self._rough.finished.connect(self._on_rough_finished)
        self._rough.failed.connect(self._on_rough_failed)
        # 译文到位时合并重绘（150ms 防抖，避免逐块清空页缓存）
        self._cover_debounce = QTimer(self)
        self._cover_debounce.setSingleShot(True)
        self._cover_debounce.setInterval(150)
        self._cover_debounce.timeout.connect(self._flush_cover)
        # 流式期间收到译文的脏页累积器：防抖到期只重建这些页的缓存；
        # None 表示「全量刷新」（模式切换/完成时）
        self._pending_dirty: set[int] | None = None

        # ── 字块拖拽（复杂布局下把被遮盖的段拖出来查看）──
        # Alt+左键拖拽命中段 → 段整体平移（offset_x/y），松开保持，双击复位。
        self._drag_seg = None
        self._drag_start_pos = QPoint()
        self._drag_orig_offset = (0.0, 0.0)

        # 连接浮动工具栏按钮
        toolbar = self._text_overlay.toolbar
        toolbar.copy_btn.clicked.connect(self._copy_selected_text)
        toolbar.translate_btn.clicked.connect(self._on_toolbar_translate)
        toolbar.search_btn.clicked.connect(self._on_toolbar_search)
        toolbar.close_btn.clicked.connect(self._clear_selection)

        # 事件过滤器：
        #   - QPdfView 本体：拦截 Ctrl+滚轮（在 QPdfView 内部处理之前）
        #   - viewport：拦截中键拖拽 + 左键文本选择
        self._panning = False
        self._pan_start: QPoint | None = None
        self._pan_scroll_start: QPoint | None = None
        self._pdf_view.installEventFilter(self)
        self._pdf_view.viewport().installEventFilter(self)
        self._pdf_view.viewport().setMouseTracking(True)

        # 视口实时跟踪（不用 QTimer）
        self._connect_viewport_tracking()

        self._apply_bg_style()
        self._stack.setCurrentIndex(0)  # 初始显示 placeholder

    # ── 样式 ────────────────────────────────────────────────

    def _apply_bg_style(self) -> None:
        white = "#ffffff"
        text = Colors.CHAR.name()
        self.setStyleSheet(f"background-color: {white}; border: none;")
        self._placeholder.setStyleSheet(
            f"color: {text}; font-size: 14pt; font-style: italic;"
            f"background-color: {white}; padding: 80px; border: none;"
        )
        self._pdf_view.setStyleSheet(
            f"QPdfView {{ background-color: {white}; border: none; }}"
        )

    def refresh_theme(self) -> None:
        """根据当前主题刷新背景色。"""
        tp = theme_manager.palette
        bg = tp.canvas.name()
        text = tp.text_secondary.name()
        self.setStyleSheet(f"background-color: {bg}; border: none;")
        self._placeholder.setStyleSheet(
            f"color: {text}; font-size: 14pt; font-style: italic;"
            f"background-color: {bg}; padding: 80px; border: none;"
        )
        self._pdf_view.setStyleSheet(
            f"QPdfView {{ background-color: {bg}; border: none; }}"
        )

    # ── properties ──────────────────────────────────────────

    @property
    def _dpi_ratio(self) -> float:
        """屏幕 DPI 与 PDF 标准 72 DPI 的比率。

        QPdfView 将 zoomFactor 解释为「每点对应的逻辑像素数」（@72 DPI），
        然后乘以本比率得到实际物理像素。我们的 self._scale 存储的是视觉缩放比
        （物理像素/点），传给 setZoomFactor 前需除以本比率。
        """
        screen = QApplication.primaryScreen()
        return screen.logicalDotsPerInch() / 72.0 if screen else 1.0

    @property
    def document(self) -> QPdfDocument | None:
        return self._doc

    def _doc_is_valid(self) -> bool:
        """文档存在且底层 C++ 对象未被销毁。

        窗口关闭/组件销毁过程中 QPdfView 可能已删除 QPdfDocument，
        Python 侧 self._doc 仍非 None，但访问会抛
        RuntimeError: Internal C++ object already deleted。
        """
        return self._doc is not None and shiboken6.isValid(self._doc)

    @property
    def page_count(self) -> int:
        doc = self._doc
        return doc.pageCount() if doc is not None and shiboken6.isValid(doc) else 0

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def is_fit_width(self) -> bool:
        return self._fit_width

    def verticalScrollBar(self):
        return self._pdf_view.verticalScrollBar()

    def horizontalScrollBar(self):
        return self._pdf_view.horizontalScrollBar()

    @property
    def content_widget(self):
        return self._pdf_view

    def viewport(self):
        return self._pdf_view.viewport()

    # ── 公开方法 ────────────────────────────────────────────

    def inject_sessions(self, pool: dict) -> None:
        """注入外部文档会话缓存（布局 mono↔dual 重建后复用，避免重新提取/翻译）。"""
        for k, v in pool.items():
            if k not in self._sessions:
                self._sessions[k] = v

    def set_shared_sessions(self, store: dict) -> None:
        """用外部共享存储替换本实例的会话缓存。

        双栏查看器让左右栏指向同一个 dict：任一栏 load_pdf 新建的会话
        对另一栏立即可见 —— 消灭同文件双重提取/双份 QPdfDocument。
        必须在本实例尚未持有任何会话时调用。
        """
        if store is None or self._sessions:
            return
        self._sessions = store
        self._shared_store = store

    def export_sessions(self) -> dict:
        """导出当前文档会话缓存（供重建后的新 viewer 复用）。"""
        out = dict(self._sessions)
        if self._session is not None:
            p = self._session.get("path")
            if p:
                out[p] = self._session
        return out

    def load_pdf(self, path: str) -> None:
        """加载 PDF（带文档会话缓存：同一路径复用 doc/文本/粗糙翻译状态）。

        切换 tab / 回到原文视图时不再重新提取文本，粗糙翻译层状态得以保留。
        """
        self._source_path = path

        # ── 命中会话缓存：直接激活（不重载、不重提取）──
        session = self._sessions.get(path)
        if session is not None and shiboken6.isValid(session["doc"]):
            self._activate_session(session)
            return

        # ── 全新加载 ──
        self._rough.cancel()
        self._text_extractor.cancel()
        self._text_overlay.clear_highlights()
        self._text_overlay.hide()

        self._doc_id += 1
        current_id = self._doc_id
        old_doc = self._doc
        self._doc = QPdfDocument()
        self._doc.load(path)
        if self._doc.status() != QPdfDocument.Status.Ready:
            self._doc = None
            if old_doc is None:
                self._pdf_view.setDocument(None)
            self._stack.setCurrentIndex(0)
            return

        # 新建会话：提取结果 / 粗糙翻译状态随会话保留
        self._text_spans = {}
        self._cover_segments = {}
        self._rough_translations = {}
        self._cover_mode = COVER_TRANSPARENT
        self._text_layer_done = False
        self._session = {
            "path": path,
            "doc": self._doc,
            "doc_id": current_id,
            "spans": self._text_spans,
            "segments": self._cover_segments,
            "translations": self._rough_translations,
            "extraction_complete": False,
            # 提取进行中标记：双栏共享会话时，另一栏据此不再重复起提取任务
            "extracting": True,
        }
        self._sessions[path] = self._session
        self._evict_sessions(self.MAX_SESSIONS)

        self._layout_engine.set_document(self._doc)
        self._fit_width = True
        self._scale = self.DEFAULT_SCALE
        self._pdf_view.setDocument(self._doc)
        self._pdf_view.setDocumentMargins(QMargins(0, 0, 0, 0))
        self._pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self._stack.setCurrentIndex(1)

        # 显示覆盖层
        self._sync_overlay_geometry()
        self._text_overlay.set_cover_mode(self._cover_mode)
        self._text_overlay.show()
        self._text_overlay.raise_()

        # 启动后台文本提取
        self._text_extractor.extract(path, current_id)

        del old_doc

    def _activate_session(self, session: dict) -> None:
        """激活已缓存的文档会话（tab 切换/重载同一文档时调用，不重新提取）。"""
        if self._rough.active_doc_id not in (-1, session["doc_id"]):
            self._rough.cancel()  # 正在翻译别的文档 → 作废
        self._text_overlay.clear_highlights()
        self._text_overlay.hide()

        self._session = session
        self._doc = session["doc"]
        self._doc_id = session["doc_id"]
        self._text_spans = session["spans"]
        self._cover_segments = session["segments"]
        self._rough_translations = session["translations"]
        # 视图模式（原文/译文）是「视图状态」而非文档数据，不随 session 共享：
        # 双栏下左右栏必须独立（左栏固定原文），若从 session 恢复 mode，
        # 同一 session dict 被左右栏共享 → 左右都变译文 / 互相污染。
        # 因此激活会话一律回到原文态，由 UI 层（切换按钮）决定是否呈现译文。
        self._cover_mode = COVER_TRANSPARENT
        self._text_layer_done = bool(session.get("extraction_complete"))

        self._layout_engine.set_document(self._doc)
        self._pdf_view.setDocument(self._doc)
        self._pdf_view.setDocumentMargins(QMargins(0, 0, 0, 0))
        self._pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self._stack.setCurrentIndex(1)

        self._sync_overlay_geometry()
        self._text_overlay.set_cover_mode(self._cover_mode)
        self._text_overlay.show()
        self._text_overlay.raise_()

        # 会话提取若被中途打断（切走时取消），恢复时补跑；
        # 共享会话下另一栏正在提取（extracting=True）则不重复起任务
        if not session.get("extraction_complete") and not session.get("extracting"):
            if not self._text_extractor.is_active(self._doc_id):
                session["extracting"] = True
                self._text_extractor.extract(session["path"], self._doc_id)

        self._on_viewport_changed()

    def clear(self) -> None:
        # 清空时取消后台任务与粗糙翻译（会话缓存保留，重载同路径可复用）
        self._rough.cancel()
        self._doc_id += 1
        self._text_extractor.cancel()
        self._text_layer_done = False
        self._text_overlay.clear_highlights()
        self._text_overlay.clear_cover()
        self._text_overlay.hide()

        self._pdf_view.setDocument(None)
        self._doc = None
        self._session = None
        self._stack.setCurrentIndex(0)

    def release_sessions(self, paths) -> None:
        """释放指定路径的文档会话（销毁 QPdfDocument，解除 Windows 文件句柄占用）。

        QPdfDocument 加载 PDF 后在 Windows 上持有文件句柄，且实测
        close()/deleteLater()/GC 均无法释放（句柄随 C++ 对象析构才归还），
        只能通过 shiboken6.delete() 立即销毁 C++ 对象。由于会话缓存
        （_sessions）会长期保留文档对象，即使切换/清空视图，被查看过的
        文件仍无法删除；删除外部文件前必须调用本方法。

        :param paths: 文件路径集合（str 或 Path，可包含不存在的路径）
        """
        if not self._sessions:
            return
        targets = {str(Path(p).resolve()) for p in paths}
        for key in list(self._sessions.keys()):
            try:
                key_resolved = str(Path(key).resolve())
            except Exception:
                key_resolved = key
            if key_resolved in targets:
                self._teardown_session(key)

    def _teardown_session(self, key: str) -> None:
        """弹出并销毁单个会话（若正是当前显示文档则先卸载全部引用）。"""
        session = self._sessions.pop(key, None)
        if session is None:
            return
        doc = session.get("doc")
        if doc is None:
            return
        try:
            if not shiboken6.isValid(doc):
                return
        except RuntimeError:
            return
        if self._doc is doc:
            self._rough.cancel()
            self._text_extractor.cancel()
            self._pdf_view.setDocument(None)
            self._layout_engine.set_document(None)
            self._doc = None
            self._session = None
            self._stack.setCurrentIndex(0)
        try:
            shiboken6.delete(doc)  # 立即析构 → 归还文件句柄
        except Exception:
            logger.debug("Failed to delete QPdfDocument for %s", key, exc_info=True)

    def _evict_sessions(self, max_keep: int) -> None:
        """按插入序淘汰最旧会话（跳过当前会话），防止长会话内存无界增长。"""
        overflow = len(self._sessions) - max_keep
        if overflow <= 0:
            return
        for key in list(self._sessions.keys()):
            if overflow <= 0:
                break
            if self._sessions.get(key) is self._session:
                continue
            self._teardown_session(key)
            overflow -= 1

    def shutdown(self) -> None:
        """窗口关闭前调用：断开视口跟踪信号、取消后台任务、卸载文档。

        防止窗口销毁过程中 QPdfView 触发 resize/滚动/缩放等信号时，
        _on_viewport_changed 访问到已被 Qt 删除的 QPdfDocument，
        抛出 RuntimeError: Internal C++ object already deleted。
        """
        self._rough.cancel()
        self._disconnect_viewport_tracking()
        self._doc_id += 1
        self._text_extractor.cancel()
        self._text_spans.clear()
        # 共享会话下另一栏可能还要补跑提取：解除本实例的"提取中"占位
        if self._session is not None:
            try:
                if not self._session.get("extraction_complete"):
                    self._session["extracting"] = False
            except RuntimeError:
                pass
        if getattr(self, "_shared_store", None) is None:
            self._sessions.clear()
        else:
            # 共享存储归 DualRoughViewer 所有：仅脱离引用，不清空内容
            self._sessions = {}
        try:
            if shiboken6.isValid(self._pdf_view):
                self._pdf_view.setDocument(None)
        except RuntimeError:
            pass
        self._doc = None
        self._layout_engine.set_document(None)
        if shiboken6.isValid(self._text_overlay):
            self._text_overlay.clear_highlights()
            self._text_overlay.hide()

    def _disconnect_viewport_tracking(self) -> None:
        """断开 _connect_viewport_tracking() 建立的所有信号连接。"""
        if not shiboken6.isValid(self._pdf_view):
            return
        for getter in (self._pdf_view.verticalScrollBar, self._pdf_view.horizontalScrollBar):
            try:
                bar = getter()
                if shiboken6.isValid(bar):
                    bar.valueChanged.disconnect(self._on_viewport_changed)
            except (RuntimeError, TypeError):
                pass
        for sig in (
            self._pdf_view.zoomFactorChanged,
            self._pdf_view.pageSpacingChanged,
            self._pdf_view.documentMarginsChanged,
            self._pdf_view.pageModeChanged,
        ):
            try:
                sig.disconnect(self._on_viewport_changed)
            except (RuntimeError, TypeError):
                pass
        # 移除事件过滤器，避免销毁期间 viewport Resize 等事件仍被处理
        try:
            self._pdf_view.removeEventFilter(self)
            vp = self._pdf_view.viewport()
            if shiboken6.isValid(vp):
                vp.removeEventFilter(self)
        except RuntimeError:
            pass

    def _exit_fit_width(self) -> float:
        """从 FitToWidth 切换到 Custom 模式，返回视觉缩放比（物理像素/点）。

        QPdfView 的 zoomFactor 是「逻辑像素/点」(72 DPI)，实际渲染 = zoomFactor × DPI比率。
        因此 setZoomFactor 需要传入 视觉缩放比 / DPI比率。

        视觉缩放比直接取自布局引擎的 Qt 精确计算（含 qRound 舍入），
        保证从 FitToWidth 切换到 Custom 时不产生跳变。
        """
        if not self._fit_width:
            return self._scale

        vp = self._pdf_view.viewport()
        visual_scale = self._layout_engine.current_scale(vp.width(), vp.height())

        self._scale = visual_scale
        self._fit_width = False
        self._pdf_view.setZoomMode(QPdfView.ZoomMode.Custom)
        # ★ 转换为 QPdfView 的逻辑 zoomFactor
        self._pdf_view.setZoomFactor(visual_scale / self._dpi_ratio)
        return visual_scale

    def zoom_in(self) -> None:
        self._scale = self._exit_fit_width()
        self._scale = min(self._scale * 1.25, self.MAX_SCALE)
        self._pdf_view.setZoomFactor(self._scale / self._dpi_ratio)

    def zoom_out(self) -> None:
        self._scale = self._exit_fit_width()
        self._scale = max(self._scale / 1.25, self.MIN_SCALE)
        self._pdf_view.setZoomFactor(self._scale / self._dpi_ratio)

    def zoom_reset(self) -> None:
        self._fit_width = True
        self._scale = self.DEFAULT_SCALE
        self._pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def goto_page(self, page_number: int) -> None:
        nav = self._pdf_view.pageNavigator()
        nav.jump(page_number, nav.currentLocation())

    # ── 视口跟踪（信号驱动，无 QTimer）────────────────────

    def _connect_viewport_tracking(self):
        """用信号替代 QTimer，实现零延迟视口跟踪"""
        vs = self.verticalScrollBar()
        hs = self.horizontalScrollBar()

        vs.valueChanged.connect(self._on_viewport_changed)
        hs.valueChanged.connect(self._on_viewport_changed)

        # QPdfView 属性变化时，内部布局会改变
        self._pdf_view.zoomFactorChanged.connect(self._on_viewport_changed)
        self._pdf_view.pageSpacingChanged.connect(self._on_viewport_changed)
        self._pdf_view.documentMarginsChanged.connect(self._on_viewport_changed)
        self._pdf_view.pageModeChanged.connect(self._on_viewport_changed)

    def _current_scale(self) -> float:
        """获取与 QPdfView 完全一致的当前渲染缩放比（物理像素/点）。

        由布局引擎按 Qt 的 calculateDocumentLayout() 精确计算：
        - FitToWidth: res * (vp_w - 边距) / qRound(pt_w * res)
        - Custom:     res * zoomFactor() == self._scale
        """
        if not self._doc_is_valid():
            return self._scale
        vp = self._pdf_view.viewport()
        if vp is None or not shiboken6.isValid(vp):
            return self._scale
        return self._layout_engine.current_scale(vp.width(), vp.height())

    def _on_viewport_changed(self):
        """滚动、缩放、resize 时调用，实时更新文本层坐标"""
        if not self._doc_is_valid() or not shiboken6.isValid(self._pdf_view):
            return

        vp = self._pdf_view.viewport()
        if vp is None or not shiboken6.isValid(vp):
            return
        layouts = self._layout_engine.compute_layout(vp.width(), vp.height())
        self._last_layouts = layouts

        # ── 诊断：对比引擎计算的 content 尺寸与 QPdfView 实际 scrollbar ──
        self._diagnose_layout(layouts, vp)

        # 更新所有页面的 span 坐标
        for layout in layouts:
            if layout.page_num in self._text_spans:
                self._update_page_spans(layout)

        # 更新覆盖层 segment 坐标并推送
        for layout in layouts:
            if layout.page_num in self._cover_segments:
                self._update_page_cover(layout)
        self._push_cover()

        # 同步覆盖层大小
        self._sync_overlay_geometry()

        # 如果存在已确认的选区，刷新 viewport 高亮（跟随滚动/缩放）
        if self._selected_content_rect is not None and self._selected_text:
            self._refresh_highlights()

    def _update_page_spans(self, layout: PageLayout):
        """将单页 TextSpan 的 PDF 坐标转换为内容坐标"""
        spans = self._text_spans.get(layout.page_num, [])
        for span in spans:
            x = layout.rect.x() + span.pdf_x * layout.scale
            y = layout.rect.y() + span.pdf_y * layout.scale
            w = span.pdf_width * layout.scale
            h = span.pdf_height * layout.scale
            span.content_rect = QRectF(x, y, w, h)

    # ── 覆盖层（粗糙翻译）──────────────────────────────────

    @property
    def cover_mode(self) -> str:
        return self._cover_mode

    @property
    def text_layer_done(self) -> bool:
        """当前文档的文本层是否已提取完成。"""
        return self._text_layer_done

    def rough_segment_count(self) -> int:
        """当前文档的段落级 segment 总数。"""
        return sum(len(v) for v in self._cover_segments.values())

    def has_rough_segments(self) -> bool:
        """当前文档是否存在可覆盖的文本层。"""
        return bool(self._cover_segments)

    def has_rough_translations(self) -> bool:
        """当前文档是否已有译文在内存中（可复用呈现，无需重新翻译）。"""
        return bool(self._rough_translations)

    def rough_export_text(self) -> str:
        """导出粗糙翻译结果（按页顺序，译文缺失回退原文，段间空行）。"""
        lines: list[str] = []
        for pg in sorted(self._cover_segments):
            lines.append(f"── 第 {pg + 1} 页 ──")
            for seg in self._cover_segments[pg]:
                txt = (seg.display_text or seg.text or "").strip()
                if txt:
                    lines.append(txt)
                lines.append("")
            lines.append("")
        return "\n".join(lines).strip()

    def rough_is_running(self) -> bool:
        """粗糙翻译是否正在后台进行。"""
        return self._rough.is_running

    def set_cover_mode(self, mode: str) -> None:
        """切换覆盖层渲染模式（原文 ⇄ 译文），仅重绘不重载。

        视图模式是 viewer 实例的本地状态，不写入 session：
        ① 双栏下左右栏各自独立控制（左栏原文、右栏译文），共享 session
           的 mode 会互相污染；
        ② 布局切换（mono↔dual）重建 viewer 后回到原文态，由 UI 按钮决定。
        """
        if mode not in (COVER_TRANSPARENT, COVER_ORIGINAL, COVER_TRANSLATED):
            return
        if mode == self._cover_mode:
            return
        self._cover_mode = mode
        self._text_overlay.set_cover_mode(mode)
        self._push_cover(bump=True)

    def start_rough_translation(self, profile, lang_in: str, lang_out: str) -> bool:
        """基于当前文档文本层启动粗糙翻译，立即进入译文态流式呈现。

        续传语义：已翻译过的段（translations 中已有译文）自动跳过，
        只翻译缺失段 —— 布局切换/重复点击不会重复消耗翻译额度。
        """
        if not self._doc_is_valid():
            return False
        if self._rough.is_running:
            self._rough.cancel()
        all_segments = [
            (pg, idx, seg.text, bool(seg.is_heading))
            for pg in sorted(self._cover_segments)
            for idx, seg in enumerate(self._cover_segments[pg])
            if seg.text.strip()
        ]
        if not all_segments:
            return False
        # 续传：跳过已翻译的段（(pg, idx) 在 translations 中已有译文）
        pending = [
            item for item in all_segments
            if (item[0], item[1]) not in self._rough_translations
        ]
        reused = len(all_segments) - len(pending)
        if not pending:
            # 全部已译：直接呈现译文态，不重新翻译
            self.set_cover_mode(COVER_TRANSLATED)
            self.rough_status.emit(
                f"复用内存译文：{len(all_segments)} 个文本块（未重新翻译）"
            )
            return True
        self._rough.start(self._doc_id, pending, profile, lang_in, lang_out)
        self.set_cover_mode(COVER_TRANSLATED)
        # 初始进度：调用方（主窗口）据此初始化进度条量程
        self.rough_progress.emit(0, len(pending))
        suffix = f"（{reused} 个已译复用）" if reused else ""
        self.rough_status.emit(
            f"粗糙翻译启动：{len(pending)} 个文本块待译{suffix}，按页流式呈现…"
        )
        return True

    def cancel_rough_translation(self) -> None:
        self._rough.cancel()

    # ── 粗译持久化（与 main_window 的 sidecar 读写配合）──────

    def collect_rough_result(self) -> dict[int, list[tuple[int, str, str | None]]]:
        """导出 {page: [(idx, 原文, 译文|None), ...]}，供 sidecar 持久化。"""
        out: dict[int, list[tuple[int, str, str | None]]] = {}
        for pg in sorted(self._cover_segments):
            rows = []
            for idx, seg in enumerate(self._cover_segments[pg]):
                if not seg.text.strip():
                    continue
                rows.append((idx, seg.text, self._rough_translations.get((pg, idx))))
            if rows:
                out[pg] = rows
        return out

    def apply_rough_translations(self, translations: dict) -> int:
        """注入历史粗译（仅接受当前分段中存在的 (page, idx) 键），返回采纳数。"""
        applied = 0
        pages: set[int] = set()
        for key, dst in (translations or {}).items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            pg, idx = key
            segs = self._cover_segments.get(pg)
            if not segs or not (0 <= idx < len(segs)):
                continue
            if isinstance(dst, str) and dst.strip():
                self._rough_translations[(pg, idx)] = dst
                pages.add(pg)
                applied += 1
        if applied and self._cover_mode == COVER_TRANSLATED:
            self._schedule_flush(pages, immediate=True)
        return applied

    def rough_progress_counts(self) -> tuple[int, int]:
        """(已译段数, 总段数) — 供「点击继续翻译（已有 N/M 段）」提示使用。

        已译数只统计当前分段中仍然有效的键（分段算法升级后旧键自动失效）。
        """
        total = self.rough_segment_count()
        done = sum(
            1
            for (pg, idx) in self._rough_translations
            if pg in self._cover_segments and 0 <= idx < len(self._cover_segments[pg])
        )
        return done, total

    def _update_page_cover(self, layout: PageLayout):
        """将单页 CoverSegment 的 PDF 坐标转换为内容坐标"""
        segs = self._cover_segments.get(layout.page_num, [])
        for seg in segs:
            seg.content_rect = QRectF(
                layout.rect.x() + seg.pdf_x * layout.scale,
                layout.rect.y() + seg.pdf_y * layout.scale,
                seg.pdf_width * layout.scale,
                seg.pdf_height * layout.scale,
            )

    def _resolve_display(self) -> None:
        """按当前模式解析每个 segment 的显示文本（译文 or 原文）。"""
        translated = self._cover_mode == COVER_TRANSLATED
        for pg, segs in self._cover_segments.items():
            for idx, seg in enumerate(segs):
                seg.display_text = (
                    self._rough_translations.get((pg, idx)) if translated else None
                )

    def _push_cover(
        self, bump: bool = False, dirty_pages: set[int] | None = None
    ) -> None:
        """把覆盖层数据推给 TextOverlay。

        :param bump: 内容/模式变化需重建页缓存
        :param dirty_pages: 仅这些页数据变化（流式增量）；None = 全部页
        """
        if not self._doc_is_valid():
            return
        if self._cover_mode != COVER_TRANSPARENT:
            self._resolve_display()
        self._text_overlay.set_cover(
            self._cover_segments,
            {l.page_num: l for l in self._last_layouts},
            bump=bump,
            bump_pages=dirty_pages,
        )

    def _schedule_flush(self, pages: set[int], immediate: bool = False) -> None:
        """累积脏页并调度合并重绘；immediate=True 跳过防抖立即刷新。"""
        if self._pending_dirty is not None:
            self._pending_dirty |= pages
        if immediate:
            self._flush_cover()
        else:
            self._cover_debounce.start()

    def _flush_cover(self, all_pages: bool = False) -> None:
        """把累积的脏页（或全部页）推给覆盖层重建页缓存。"""
        if self._cover_debounce.isActive():
            self._cover_debounce.stop()
        if all_pages:
            self._pending_dirty = None
        dirty = self._pending_dirty
        self._pending_dirty = set()
        # dirty=None → 全量；空集合 → 仅同步引用不失效缓存
        self._push_cover(bump=True, dirty_pages=dirty)

    def cover_segment_at(self, vp_pos) -> "CoverSegment | None":
        """命中测试：返回 viewport 坐标处（偏移后位置）命中的 cover segment。

        从后向前遍历（浮动段视觉在最上层 → 优先命中），供 Alt+拖拽使用。
        """
        hs = self.horizontalScrollBar().value()
        vs = self.verticalScrollBar().value()
        cpos = QPoint(vp_pos.x() + hs, vp_pos.y() + vs)
        for pg in sorted(self._cover_segments):
            for seg in reversed(self._cover_segments[pg]):
                rect = seg.content_rect
                if rect is None or rect.isEmpty():
                    continue
                if (
                    getattr(seg, "offset_x", 0.0) != 0.0
                    or getattr(seg, "offset_y", 0.0) != 0.0
                ):
                    rect = rect.translated(seg.offset_x, seg.offset_y)
                if rect.contains(QPointF(cpos)):
                    return seg
        return None

    def _on_rough_segment(self, doc_id: int, page: int, idx: int, text: str) -> None:
        if doc_id != self._doc_id:
            return
        self._rough_translations[(page, idx)] = text
        if self._cover_mode != COVER_TRANSPARENT:
            self._schedule_flush({page})  # 防抖合并逐块重绘（仅脏页）

    def _on_rough_page(self, doc_id: int, page: int) -> None:
        if doc_id != self._doc_id:
            return
        self._schedule_flush({page}, immediate=True)
        self.rough_status.emit(f"粗糙翻译：第 {page + 1} 页完成")

    def _on_rough_progress(self, doc_id: int, done: int, total: int) -> None:
        if doc_id != self._doc_id:
            return
        self.rough_progress.emit(done, total)

    def _on_rough_stats(self, doc_id: int, ok: int, failed: int) -> None:
        if doc_id != self._doc_id:
            return
        self.rough_stats.emit(ok, failed)

    def _on_rough_finished(self, doc_id: int) -> None:
        if doc_id != self._doc_id:
            return
        self._flush_cover(all_pages=True)
        # 完成文案由 rough_stats 的接收方负责（含成败明细），这里只同步状态
        self.rough_ready.emit()

    def _on_rough_failed(self, doc_id: int, message: str) -> None:
        if doc_id != self._doc_id:
            return
        self.rough_status.emit(f"粗糙翻译异常：{message}")

    def _diagnose_layout(self, layouts: list, vp) -> None:
        """诊断：对比引擎计算的 content 尺寸与 QPdfView 实际 scrollbar 值。

        只在缩放模式变化或首次加载时打印，用于排查内容坐标系偏移。
        """
        if not layouts:
            return
        last = layouts[-1]
        margins = self._pdf_view.documentMargins()
        our_height = last.rect.bottom() + margins.bottom()
        qpdf_height = self.verticalScrollBar().maximum() + vp.height()

        max_w = max(l.rect.right() + margins.right() for l in layouts)
        qpdf_width = self.horizontalScrollBar().maximum() + vp.width()

        # 只在差异显著时打印（诊断用，正常情况差异 < 30px）
        h_diff = abs(our_height - qpdf_height)
        w_diff = abs(max_w - qpdf_width)
        if h_diff > 50 or w_diff > 50:
            logger.debug(
                "[Layout 诊断] scale=%.3f fit_width=%s vp=(%s,%s) "
                "我们的 content=(%.0f,%.0f) QPdfView content=(%.0f,%.0f) "
                "差异=(%.0f,%.0f) margins=(%s,%s,%s,%s)",
                self._current_scale(), self._fit_width, vp.width(), vp.height(),
                max_w, our_height, qpdf_width, qpdf_height, w_diff, h_diff,
                margins.left(), margins.top(), margins.right(), margins.bottom(),
            )

    def _sync_overlay_geometry(self):
        """保证覆盖层始终覆盖整个 viewport"""
        if not self._text_overlay or not self._pdf_view.viewport():
            return
        vp = self._pdf_view.viewport()
        self._text_overlay.setGeometry(0, 0, vp.width(), vp.height())
        self._text_overlay.raise_()

    # ── 异步提取回调 ───────────────────────────────────────

    def _on_text_page_ready(self, page_num: int, spans: list, doc_id: int):
        """后台线程返回单页文本"""
        if doc_id != self._doc_id or not self._doc_is_valid():
            return  # 丢弃过期结果

        self._text_spans[page_num] = spans
        # 页尺寸用于垃圾段过滤（页眉/页脚带、竖排水印判定）
        page_size = None
        doc = self._doc
        if doc is not None:
            try:
                pt = doc.pagePointSize(page_num)
                page_size = (pt.width(), pt.height())
            except Exception:
                page_size = None
        self._cover_segments[page_num] = build_segments(spans, page_size=page_size)

        # 立即计算该页坐标（如果布局已就绪）
        self._on_viewport_changed()

    def _on_text_all_ready(self, doc_id: int):
        if doc_id == self._doc_id:
            self._text_layer_done = True
            if self._session is not None and self._session["doc_id"] == doc_id:
                self._session["extraction_complete"] = True
                self._session["extracting"] = False
            logger.info("PDF 文本提取完成，共 %d 页", len(self._text_spans))
            self.text_layer_ready.emit()

    # ── resizeEvent ─────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_overlay_geometry()
        self._on_viewport_changed()


class PDFViewer(TextSelectionMixin, PDFViewerCore):
    """最终 PDF 查看器 — 组合文档核心与文本选择交互。

    事件过滤器统一分发：
    - 视口 resize → 覆盖层同步
    - Ctrl+滚轮 → 缩放
    - 左键按下/拖动/释放 → TextSelectionMixin 划词选择
    - 中键按下/拖动/释放 → TextSelectionMixin 平移
    """

    # ── 事件过滤器（中键拖拽 + Ctrl+滚轮缩放 + 左键文本选择）──

    def eventFilter(self, obj, event: QEvent | None) -> bool:
        if event is None:
            return False

        # viewport resize 时同步覆盖层
        if obj is self._pdf_view.viewport() and event.type() == QEvent.Type.Resize:
            self._sync_overlay_geometry()
            self._on_viewport_changed()
            return False

        # Ctrl+滚轮缩放
        if event.type() == QEvent.Type.Wheel:
            we = event
            if we.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = we.angleDelta().y()
                if delta > 0:
                    self.zoom_in()
                else:
                    self.zoom_out()
                return True
            return False  # 普通滚轮交给 QPdfView

        # 文本选择仅处理 viewport 自身事件：浮动工具栏等子控件按下时
        # 不进入选择逻辑（否则按下工具栏按钮会清空选中文本/隐藏工具栏）
        if obj is not self._pdf_view.viewport() and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
        ):
            return False

        # 左键：Alt+拖拽 = 字块拖拽（把被遮盖的段拖出来查看）；普通左键 = 划词
        if event.type() == QEvent.Type.MouseButtonPress:
            me = event
            if me.button() == Qt.MouseButton.LeftButton:
                if me.modifiers() & Qt.KeyboardModifier.AltModifier:
                    if self._on_drag_press(me):
                        return True  # 命中段 → 进入拖拽，不划词
                self._on_select_press(me)
                return False  # 不消耗事件：让 QPdfView 正常处理单击，滚动由拖拽触发
            # 中键拖拽
            elif me.button() == Qt.MouseButton.MiddleButton:
                self._on_pan_begin(me)
                return True

        elif event.type() == QEvent.Type.MouseMove:
            # 字块拖拽优先于划词/平移
            if self._drag_seg is not None:
                return self._on_drag_move(event)
            if self._selecting:
                return self._on_select_move(event)
            if self._panning and self._pan_start and self._pan_scroll_start:
                return self._on_pan_move(event)

        elif event.type() == QEvent.Type.MouseButtonRelease:
            me = event
            if self._drag_seg is not None and me.button() == Qt.MouseButton.LeftButton:
                return self._on_drag_release(me)
            if self._selecting and me.button() == Qt.MouseButton.LeftButton:
                self._selecting = False
                return self._on_select_release(me)
            elif me.button() == Qt.MouseButton.MiddleButton:
                self._on_pan_end(me)
                return True

        elif event.type() == QEvent.Type.MouseButtonDblClick:
            me = event
            if me.button() == Qt.MouseButton.LeftButton and self._on_drag_double_click(me):
                return True

        return super().eventFilter(obj, event)