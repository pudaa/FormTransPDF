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

from PySide6.QtCore import Qt, QPoint, QEvent, QMargins, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPalette, QWheelEvent, QScreen
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

        # 连接浮动工具栏按钮
        toolbar = self._text_overlay.toolbar
        toolbar.copy_btn.clicked.connect(self._copy_selected_text)
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

    # ── 坐标转换工具 ───────────────────────────────────────

    def _viewport_rect_to_content(self, vp_rect: QRectF) -> QRectF:
        """将 viewport 坐标矩形转换为内容坐标矩形"""
        sx = self.horizontalScrollBar().value()
        sy = self.verticalScrollBar().value()
        return vp_rect.translated(sx, sy)

    def _content_rect_to_viewport(self, content_rect: QRectF) -> QRectF:
        """将内容坐标矩形转换为 viewport 坐标矩形"""
        sx = self.horizontalScrollBar().value()
        sy = self.verticalScrollBar().value()
        return content_rect.translated(-sx, -sy)

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
        """从 FitToWidth 切换到 Custom 模式，返回视觉缩放比（物理像素/点）。

        QPdfView 的 zoomFactor 是「逻辑像素/点」(72 DPI)，实际渲染 = zoomFactor × DPI比率。
        因此 setZoomFactor 需要传入 视觉缩放比 / DPI比率。
        """
        if not self._fit_width:
            return self._scale

        vp_w = max(self._pdf_view.viewport().width(), 1)
        margins = self._pdf_view.documentMargins()
        available_w = max(vp_w - margins.left() - margins.right(), 1)
        if self._doc and self._doc.pageCount() > 0:
            pt_w = max(self._doc.pagePointSize(0).width(), 1)
            visual_scale = available_w / pt_w
        else:
            visual_scale = 1.0

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
        """获取当前一致的渲染缩放比，供布局引擎使用。

        返回 self._scale 作为权威缩放比。在 FitToWidth 模式下，
        QPdfView.zoomFactor() 恒为 1.0，因此不能直接使用。
        改用 viewport 宽度推导实际显示缩放比。
        """
        if self._fit_width and self._doc and self._doc.pageCount() > 0:
            vp = self._pdf_view.viewport()
            vp_w = max(vp.width(), 1)
            margins = self._pdf_view.documentMargins()
            available_w = max(vp_w - margins.left() - margins.right(), 1)
            pt_w = max(self._doc.pagePointSize(0).width(), 1)
            return available_w / pt_w
        return self._scale

    def _on_viewport_changed(self):
        """滚动、缩放、resize 时调用，实时更新文本层坐标"""
        if not self._doc:
            return

        vp = self._pdf_view.viewport()
        scale = self._current_scale()
        layouts = self._layout_engine.compute_layout(vp.width(), vp.height(), explicit_scale=scale)

        # ── 诊断：对比引擎计算的 content 尺寸与 QPdfView 实际 scrollbar ──
        self._diagnose_layout(layouts, vp)

        # 更新所有页面的 span 坐标
        for layout in layouts:
            if layout.page_num in self._text_spans:
                self._update_page_spans(layout)

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

        # 只在差异较大时打印（避免滚动时刷屏）
        h_diff = abs(our_height - qpdf_height)
        w_diff = abs(max_w - qpdf_width)
        # if h_diff > 2 or w_diff > 2:
        #     print(
        #         f"[Layout 诊断] scale={self._current_scale():.3f} fit_width={self._fit_width} "
        #         f"vp=({vp.width()},{vp.height()}) "
        #         f"我们的 content=({max_w:.0f},{our_height:.0f}) "
        #         f"QPdfView content=({qpdf_width:.0f},{qpdf_height:.0f}) "
        #         f"差异=({w_diff:.0f},{h_diff:.0f}) "
        #         f"margins=({margins.left()},{margins.top()},{margins.right()},{margins.bottom()})"
        #     )

    def _sync_overlay_geometry(self):
        """保证覆盖层始终覆盖整个 viewport"""
        if not self._text_overlay or not self._pdf_view.viewport():
            return
        vp = self._pdf_view.viewport()
        self._text_overlay.setGeometry(0, 0, vp.width(), vp.height())
        self._text_overlay.raise_()

    # ── 坐标转换工具（已替换为 QGraphicsView 映射）──────────

    # _viewport_rect_to_content 和 _content_rect_to_viewport 已移到上面

    # ── 异步提取回调 ───────────────────────────────────────

    def _on_text_page_ready(self, page_num: int, spans: list, doc_id: int):
        """后台线程返回单页文本"""
        if doc_id != self._doc_id or not self._doc:
            return  # 丢弃过期结果

        self._text_spans[page_num] = spans

        # 立即计算该页坐标（如果布局已就绪）
        self._on_viewport_changed()

    def _on_text_all_ready(self, doc_id: int):
        if doc_id == self._doc_id:
            print(f"PDF 文本提取完成，共 {len(self._text_spans)} 页")

    # ── 文本选中逻辑 ───────────────────────────────────────

    def _update_highlights_for_selection(self, content_rect: QRectF):
        """根据内容坐标系的选择矩形，计算并设置 viewport 高亮"""
        highlights = []
        for spans in self._text_spans.values():
            for span in spans:
                if span.content_rect and content_rect.intersects(span.content_rect):
                    vp_rect = self._content_rect_to_viewport(span.content_rect)
                    highlights.append(vp_rect)
        self._text_overlay.set_highlights(highlights)

    def _refresh_highlights(self):
        """根据存储的内容选区刷新 viewport 高亮（用于滚动/缩放时同步）"""
        if self._selected_content_rect is None:
            return
        self._update_highlights_for_selection(self._selected_content_rect)

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
            self._text_overlay.toolbar.copy_btn.setText("✓ 已复制")
            QTimer.singleShot(1000, lambda: self._text_overlay.toolbar.copy_btn.setText("复制"))

    def _search_selected_text(self):
        """搜索选中文本"""
        if self._selected_text:
            print(f"搜索: {self._selected_text[:50]}")

    def _clear_selection(self):
        """清除当前选择"""
        self._selected_text = ""
        self._selected_content_rect = None
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

        # 左键文本选择（带拖拽阈值，避免误触发和干扰 QPdfView）
        if event.type() == QEvent.Type.MouseButtonPress:
            me = event
            if me.button() == Qt.MouseButton.LeftButton:
                self._selecting = True
                self._drag_threshold_met = False
                self._select_start = me.pos()
                self._selected_text = ""
                self._selected_content_rect = None
                self._text_overlay.clear_highlights()
                return False  # 不消耗事件：让 QPdfView 正常处理单击，滚动由拖拽触发
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
                delta = me.pos() - self._select_start
                # 拖拽超过 5px 阈值才进入选择模式
                if not self._drag_threshold_met:
                    if abs(delta.x()) < 5 and abs(delta.y()) < 5:
                        return False  # 未达阈值，事件继续传递给 QPdfView
                    self._drag_threshold_met = True
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

                if not self._drag_threshold_met:
                    # 纯点击（无拖拽），清空状态，事件传递回 QPdfView
                    self._text_overlay.clear_highlights()
                    return False

                rect = QRectF(self._select_start, me.pos()).normalized()
                content_rect = self._viewport_rect_to_content(rect)

                # 计算最终选中文本
                text = self._get_text_in_rect(content_rect)
                if text:
                    self._selected_text = text
                    self._selected_content_rect = content_rect
                    self.text_selected.emit(text)
                    QApplication.clipboard().setText(text)
                    # 选中完成后显示浮动工具栏
                    self._text_overlay.show_toolbar_at(self._text_overlay._highlights)
                else:
                    self._selected_content_rect = None
                    self._text_overlay.clear_highlights()

                return True
            elif me.button() == Qt.MouseButton.MiddleButton:
                self._panning = False
                self._pan_start = None
                self._pan_scroll_start = None
                self._pdf_view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
                return True

        return super().eventFilter(obj, event)