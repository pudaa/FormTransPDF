"""
PDF 页面渲染与查看组件 — 基于 QPdfView (PySide6 QtPdf)

特性：
  - QPdfView 原生渲染 + 文本选择（左键拖选，Ctrl+C 复制）
  - MultiPage 连续滚动 + FitToWidth/Custom 缩放
  - 空状态 placeholder 提示
  - 中键拖拽平移（事件过滤器实现）
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QEvent, QMargins
from PySide6.QtGui import QColor, QMouseEvent, QPalette, QWheelEvent
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QLabel,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from src.ui.theme import Colors


class PDFViewer(QWidget):
    """PDF 查看器 — QPdfView + placeholder 空状态。

    StackedLayout:
        [0] placeholder — 无 PDF 时展示
        [1] QPdfView   — 原生渲染 + 文本选择
    """

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

        self._stack = QStackedLayout()
        root.addLayout(self._stack)

        # ① placeholder
        self._placeholder = QLabel("拖拽 PDF 文件到此处")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self._placeholder)  # index 0

        # ② QPdfView
        self._pdf_view = QPdfView()
        self._pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self._pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self._stack.addWidget(self._pdf_view)  # index 1

        # 事件过滤器：
        #   - QPdfView 本体：拦截 Ctrl+滚轮（在 QPdfView 内部处理之前）
        #   - viewport：拦截中键拖拽
        self._panning = False
        self._pan_start: QPoint | None = None
        self._pan_scroll_start: QPoint | None = None
        self._pdf_view.installEventFilter(self)
        self._pdf_view.viewport().installEventFilter(self)
        self._pdf_view.viewport().setMouseTracking(True)

        self._apply_bg_style()
        self._stack.setCurrentIndex(0)  # 初始显示 placeholder

    # ── 样式 ────────────────────────────────────────────────

    def _apply_bg_style(self) -> None:
        bg = Colors.SLATE.name()
        text = Colors.CHAR.name()
        # PDFViewer 自身背景
        self.setStyleSheet(f"background-color: {bg};")
        # placeholder
        self._placeholder.setStyleSheet(
            f"color: {text}; font-size: 14pt; font-style: italic;"
            f"background-color: {bg}; padding: 80px;"
        )
        # QPdfView (QAbstractScrollArea) — stylesheet 控制 frame + palette 透传 viewport
        self._pdf_view.setAutoFillBackground(True)
        self._pdf_view.setStyleSheet(
            f"QAbstractScrollArea {{ background-color: {bg}; border: none; }}"
        )
        pal = self._pdf_view.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(bg))
        pal.setColor(QPalette.ColorRole.Base, QColor(bg))
        self._pdf_view.setPalette(pal)

    def _force_viewport_bg(self) -> None:
        """强制设置 viewport 背景（document 加载后调用）"""
        bg = Colors.SLATE.name()
        vp = self._pdf_view.viewport()
        vp.setAutoFillBackground(True)
        pal = vp.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(bg))
        pal.setColor(QPalette.ColorRole.Window, QColor(bg))
        vp.setPalette(pal)
        vp.update()

    def refresh_theme(self) -> None:
        self._apply_bg_style()
        self._force_viewport_bg()

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
        # ① 保留旧文档引用，防止 GC 在 QPdfView 切换期间回收
        old_doc = self._doc
        # ② 创建并加载新文档
        self._doc = QPdfDocument()
        self._doc.load(path)
        if self._doc.status() != QPdfDocument.Status.Ready:
            self._doc = None
            # 加载失败时确保 QPdfView 与内部状态一致
            if old_doc is None:
                # 首次加载失败：无旧文档可回退，解除 QPdfView 引用
                self._pdf_view.setDocument(None)
            self._stack.setCurrentIndex(0)
            return

        self._fit_width = True
        self._scale = self.DEFAULT_SCALE
        # ③ setDocument 内部自动断开旧文档，避免手动 setDocument(None)
        self._pdf_view.setDocument(self._doc)
        self._pdf_view.setDocumentMargins(QMargins(0, 0, 0, 0))
        self._pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self._stack.setCurrentIndex(1)

        # 强制 viewport 背景
        self._force_viewport_bg()
        # ④ 旧文档安全释放
        del old_doc

    def clear(self) -> None:
        # 先解除引用再清空
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
        # 计算真实视觉缩放
        vp_w = max(self._pdf_view.viewport().width(), 1)
        if self._doc and self._doc.pageCount() > 0:
            pt_w = max(self._doc.pagePointSize(0).width(), 1)
            visual_scale = vp_w / pt_w
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

    # ── 事件过滤器（中键拖拽 + Ctrl+滚轮缩放）─────────────

    def eventFilter(self, obj, event: QEvent | None) -> bool:
        if event is None:
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

        # 中键拖拽
        if event.type() == QEvent.Type.MouseButtonPress:
            me = event  # type: QMouseEvent
            if me.button() == Qt.MouseButton.MiddleButton:
                self._panning = True
                self._pan_start = me.globalPosition().toPoint()
                h = self.horizontalScrollBar()
                v = self.verticalScrollBar()
                self._pan_scroll_start = QPoint(
                    h.value() if h else 0, v.value() if v else 0)
                self._pdf_view.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                return True
        elif event.type() == QEvent.Type.MouseMove:
            if self._panning and self._pan_start and self._pan_scroll_start:
                me = event  # type: QMouseEvent
                delta = me.globalPosition().toPoint() - self._pan_start
                h = self.horizontalScrollBar()
                v = self.verticalScrollBar()
                if h:
                    h.setValue(self._pan_scroll_start.x() - delta.x())
                if v:
                    v.setValue(self._pan_scroll_start.y() - delta.y())
                return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            me = event  # type: QMouseEvent
            if me.button() == Qt.MouseButton.MiddleButton:
                self._panning = False
                self._pan_start = None
                self._pan_scroll_start = None
                self._pdf_view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
                return True
        return super().eventFilter(obj, event)
