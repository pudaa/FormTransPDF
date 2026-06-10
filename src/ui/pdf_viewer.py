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

from PySide6.QtCore import Qt, QPoint, QEvent, QMargins, QRectF, QSizeF, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPalette, QWheelEvent
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from src.ui.theme import Colors
from src.ui.pdf_layout_engine import PdfLayoutEngine, PageLayout
from src.ui.pdf_text_extractor import PdfTextExtractor, TextSpan
from src.ui.pdf_text_overlay import TextOverlay


class PDFViewer(QWidget):
    """PDF 查看器 — QPdfView + 双层渲染文本选中。

    StackedLayout:
        [0] placeholder — 无 PDF 时展示
        [1] QPdfView   — 原生渲染图像层
            └── viewport() → TextOverlay 透明覆盖层（高亮/工具栏）

    架构：
        - PdfLayoutEngine: 计算每页在内容坐标系中的 QRect
        - PdfTextExtractor: 后台线程提取文本，带 doc_id 版本控制
        - eventFilter: 统一处理中键拖拽、Ctrl+滚轮、左键文本选择
    """

    # ========== 信号 ==========
    text_selected = Signal(str)  # 当选中文本时发射

    DEFAULT_SCALE = 1.0
    MIN_SCALE = 0.25
    MAX_SCALE = 8.0

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
        # 显式设置页面间距，确保与布局引擎一致
        self._pdf_view.setPageSpacing(8)
        self._pdf_view.setDocumentMargins(QMargins(0, 0, 0, 0))
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

        # 连接浮动工具栏按钮
        overlay = self._text_overlay
        overlay.toolbar.copy_btn.clicked.connect(self._copy_selected_text)
        overlay.toolbar.search_btn.clicked.connect(self._search_selected_text)
        overlay.toolbar.close_btn.clicked.connect(self._clear_selection)
        overlay.toolbar.highlight_btn.clicked.connect(self._add_permanent_highlight)

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

    # ── properties ──────────────────────────────────────────

    @property
    def document(self) -> QPdfDocument | None:
        return self._doc

    @property
    def page_count(self) -> int:
        return self._doc.pageCount() if self._doc else 0

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

    def load_pdf(self, path: str) -> None:
        # 切换文档时，使旧提取任务失效
        self._doc_id += 1
        current_id = self._doc_id
        self._text_extractor.cancel()
        self._text_spans.clear()
        self._text_overlay.clear_highlights()
        self._text_overlay.hide()

        old_doc = self._doc
        self._doc = QPdfDocument()
        self._doc.load(path)
        if self._doc.status() != QPdfDocument.Status.Ready:
            self._doc = None
            if old_doc is None:
                self._pdf_view.setDocument(None)
            self._stack.setCurrentIndex(0)
            return

        self._layout_engine.set_document(self._doc)
        self._fit_width = True
        self._scale = self.DEFAULT_SCALE
        self._pdf_view.setDocument(self._doc)
        self._pdf_view.setDocumentMargins(QMargins(0, 0, 0, 0))
        self._pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self._stack.setCurrentIndex(1)

        # 显示覆盖层
        self._sync_overlay_geometry()
        self._text_overlay.show()
        self._text_overlay.raise_()

        # 启动后台文本提取
        self._text_extractor.extract(path, current_id)

        del old_doc

    def clear(self) -> None:
        # 清空时取消后台任务
        self._doc_id += 1
        self._text_extractor.cancel()
        self._text_spans.clear()
        self._text_overlay.clear_highlights()
        self._text_overlay.hide()

        self._pdf_view.setDocument(None)
        self._doc = None
        self._stack.setCurrentIndex(0)

    def _exit_fit_width(self) -> float:
        """从 FitToWidth 切换到 Custom 模式，返回视觉缩放比。

        QPdfView 在 FitToWidth 下 zoomFactor() 恒为 1.0，
        因此需从 viewport/page 几何计算真实视觉缩放比。
        """
        if not self._fit_width:
            return self._scale
        self._fit_width = False
        self._pdf_view.setZoomMode(QPdfView.ZoomMode.Custom)
        # 计算真实视觉缩放（考虑 documentMargins）
        vp_w = max(self._pdf_view.viewport().width(), 1)
        margins = self._pdf_view.documentMargins()
        available_w = max(vp_w - margins.left() - margins.right(), 1)
        if self._doc and self._doc.pageCount() > 0:
            pt_w = max(self._doc.pagePointSize(0).width(), 1)
            visual_scale = available_w / pt_w
        else:
            visual_scale = 1.0
        self._scale = visual_scale
        self._pdf_view.setZoomFactor(visual_scale)
        return visual_scale

    def zoom_in(self) -> None:
        self._scale = self._exit_fit_width()
        self._scale = min(self._scale * 1.25, self.MAX_SCALE)
        self._pdf_view.setZoomFactor(self._scale)

    def zoom_out(self) -> None:
        self._scale = self._exit_fit_width()
        self._scale = max(self._scale / 1.25, self.MIN_SCALE)
        self._pdf_view.setZoomFactor(self._scale)

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

    def _on_viewport_changed(self):
        """滚动、缩放、resize 时调用，实时更新文本层坐标"""
        if not self._doc:
            return

        vp = self._pdf_view.viewport()
        layouts = self._layout_engine.compute_layout(vp.width(), vp.height())

        # 视口在内容坐标系中的矩形
        sx = self.horizontalScrollBar().value()
        sy = self.verticalScrollBar().value()
        view_rect = QRectF(sx, sy, vp.width(), vp.height())

        # 只更新可见页面的 span 坐标（性能关键）
        for layout in layouts:
            if not view_rect.intersects(layout.rect):
                continue
            if layout.page_num in self._text_spans:
                self._update_page_spans(layout)

        # 同步覆盖层大小
        self._sync_overlay_geometry()

    def _update_page_spans(self, layout: PageLayout):
        """将单页 TextSpan 的 PDF 坐标转换为内容坐标"""
        spans = self._text_spans.get(layout.page_num, [])
        for span in spans:
            x = layout.rect.x() + span.pdf_x * layout.scale
            y = layout.rect.y() + span.pdf_y * layout.scale
            w = span.pdf_width * layout.scale
            h = span.pdf_height * layout.scale
            span.content_rect = QRectF(x, y, w, h)

    def _sync_overlay_geometry(self):
        """保证覆盖层始终覆盖整个 viewport"""
        if not self._text_overlay or not self._pdf_view.viewport():
            return
        vp = self._pdf_view.viewport()
        self._text_overlay.setGeometry(0, 0, vp.width(), vp.height())
        self._text_overlay.raise_()

    # ── 坐标转换工具 ───────────────────────────────────────

    def _viewport_rect_to_content(self, vp_rect: QRectF) -> QRectF:
        sx = self.horizontalScrollBar().value()
        sy = self.verticalScrollBar().value()
        return vp_rect.translated(sx, sy)

    def _content_rect_to_viewport(self, content_rect: QRectF) -> QRectF:
        sx = self.horizontalScrollBar().value()
        sy = self.verticalScrollBar().value()
        return content_rect.translated(-sx, -sy)

    # ── 异步提取回调 ───────────────────────────────────────

    def _on_text_page_ready(self, page_num: int, spans: list, doc_id: int):
        """后台线程返回单页文本"""
        if doc_id != self._doc_id or not self._doc:
            return  # 丢弃过期结果

        self._text_spans[page_num] = spans

        # 立即计算该页坐标（如果布局已就绪）
        vp = self._pdf_view.viewport()
        layouts = self._layout_engine.compute_layout(vp.width(), vp.height())
        for layout in layouts:
            if layout.page_num == page_num:
                self._update_page_spans(layout)
                break

    def _on_text_all_ready(self, doc_id: int):
        if doc_id == self._doc_id:
            print(f"PDF 文本提取完成，共 {len(self._text_spans)} 页")

    # ── 文本选中逻辑 ───────────────────────────────────────

    def _update_highlights_for_selection(self, content_rect: QRectF):
        """根据内容坐标系的选择矩形，计算 viewport 高亮"""
        highlights = []
        for spans in self._text_spans.values():
            for span in spans:
                if span.content_rect and content_rect.intersects(span.content_rect):
                    vp_rect = self._content_rect_to_viewport(span.content_rect)
                    highlights.append(vp_rect)
        self._text_overlay.set_highlights(highlights)

    def _get_text_in_rect(self, content_rect: QRectF) -> str:
        """获取选择矩形内的文本（按页面顺序拼接）"""
        texts = []
        # 按页码排序，保证阅读顺序
        for page_num in sorted(self._text_spans.keys()):
            spans = self._text_spans[page_num]
            for span in spans:
                if span.content_rect and content_rect.intersects(span.content_rect):
                    texts.append(span.text)
        return "".join(texts)

    # ── 工具栏动作 ─────────────────────────────────────────

    def _copy_selected_text(self):
        """复制选中文本到剪贴板"""
        if self._selected_text:
            QApplication.clipboard().setText(self._selected_text)
            self._text_overlay.toolbar.copy_btn.setText("\u2713 已复制")
            QTimer.singleShot(1000, lambda: self._text_overlay.toolbar.copy_btn.setText("\U0001F4CB 复制"))

    def _search_selected_text(self):
        """搜索选中文本"""
        if self._selected_text:
            print(f"搜索: {self._selected_text[:50]}")

    def _clear_selection(self):
        """清除当前选择"""
        self._selected_text = ""
        self._text_overlay.clear_highlights()

    def _add_permanent_highlight(self):
        """添加永久高亮"""
        print(f"标记高亮: {self._selected_text[:50]}")

    # ── resizeEvent ─────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_overlay_geometry()
        self._on_viewport_changed()

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

        # 左键文本选择
        if event.type() == QEvent.Type.MouseButtonPress:
            me = event
            if me.button() == Qt.MouseButton.LeftButton:
                self._selecting = True
                self._select_start = me.pos()
                self._selected_text = ""
                self._text_overlay.clear_highlights()
                return True
            # 中键拖拽
            elif me.button() == Qt.MouseButton.MiddleButton:
                self._panning = True
                self._pan_start = me.globalPosition().toPoint()
                h = self.horizontalScrollBar()
                v = self.verticalScrollBar()
                self._pan_scroll_start = QPoint(
                    h.value() if h else 0, v.value() if v else 0)
                self._pdf_view.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                return True

        elif event.type() == QEvent.Type.MouseMove:
            if self._selecting:
                me = event
                # 实时高亮（不绘制拖动选择框）
                rect = QRectF(self._select_start, me.pos()).normalized()
                content_rect = self._viewport_rect_to_content(rect)
                self._update_highlights_for_selection(content_rect)
                return True
            if self._panning and self._pan_start and self._pan_scroll_start:
                me = event
                delta = me.globalPosition().toPoint() - self._pan_start
                h = self.horizontalScrollBar()
                v = self.verticalScrollBar()
                if h:
                    h.setValue(self._pan_scroll_start.x() - delta.x())
                if v:
                    v.setValue(self._pan_scroll_start.y() - delta.y())
                return True

        elif event.type() == QEvent.Type.MouseButtonRelease:
            me = event
            if self._selecting and me.button() == Qt.MouseButton.LeftButton:
                self._selecting = False
                rect = QRectF(self._select_start, me.pos()).normalized()

                # 计算最终选中文本
                content_rect = self._viewport_rect_to_content(rect)
                text = self._get_text_in_rect(content_rect)
                if text:
                    self._selected_text = text
                    self.text_selected.emit(text)
                    QApplication.clipboard().setText(text)

                return True
            elif me.button() == Qt.MouseButton.MiddleButton:
                self._panning = False
                self._pan_start = None
                self._pan_scroll_start = None
                self._pdf_view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
                return True

        return super().eventFilter(obj, event)
