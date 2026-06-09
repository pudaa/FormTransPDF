"""
PDF 页面渲染与查看组件

基于 PyMuPDF（fitz），将 PDF 页面渲染为 QImage，由 QLabel 承载展示。
支持缩放、分页渲染、自适应宽度。
"""

from __future__ import annotations

import fitz  # PyMuPDF
from PyQt6.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import (
    QImage,
    QPainter,
    QPixmap,
    QWheelEvent,
    QMouseEvent,
    QResizeEvent,
    QColor,
)
from PyQt6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from src.ui.theme import Colors


# ═══════════════════════════════════════════════════════════════
# 单页渲染组件
# ═══════════════════════════════════════════════════════════════

class PDFPageWidget(QLabel):
    """单页 PDF 渲染组件。

    将 PyMuPDF 的 page.get_pixmap() 输出转为 QPixmap 显示。
    """

    page_clicked = pyqtSignal(int)  # page_number

    def __init__(self, page_number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page_number = page_number
        self._source_image: QImage | None = None
        self._source_scale: float = 1.0   # 源图渲染时的缩放比（用于缩放时计算）
        self._scale: float = 1.0

        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # 页间分隔线效果（底部 margin 模拟）
        self.setContentsMargins(0, 0, 0, 6)

    # ── properties ──────────────────────────────────────────

    @property
    def page_number(self) -> int:
        return self._page_number

    @property
    def scale(self) -> float:
        return self._scale

    # ── 渲染 ────────────────────────────────────────────────

    def render_from_page(
        self,
        page: fitz.Page,
        target_width: int | None = None,
        scale: float = 3.0,
    ) -> None:
        """从 PyMuPDF Page 渲染（重量级 — 仅首次加载时调用）。

        scale 控制实际渲染分辨率；
        target_width 为 None 时按原始比例显示（真缩放），
        否则自适应到指定宽度。
        """
        # 动态计算渲染缩放比：确保渲染分辨率 ≥ 目标显示宽度
        if target_width and target_width > 0:
            page_w = page.rect.width
            min_scale = target_width / page_w if page_w > 0 else scale
            scale = max(scale, min_scale)
        self._source_scale = scale
        self._scale = scale

        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)

        img = QImage(
            pix.samples,
            pix.width,
            pix.height,
            pix.stride,
            QImage.Format.Format_RGB888,
        )
        self._source_image = img.copy()

        if target_width and target_width > 0:
            self._display_scaled(target_width)
        else:
            self.setPixmap(QPixmap.fromImage(self._source_image))
            self.setFixedSize(self._source_image.size())

    def render_from_source(
        self,
        target_width: int | None = None,
        scale: float | None = None,
    ) -> None:
        """从已有高分辨率源图缩放显示（轻量级 — 缩放时调用）。

        :param target_width: 自适应宽度模式下的目标宽度
        :param scale: 真缩放模式下的绝对缩放比（相对 PDF 页面）
        """
        src = self._source_image
        if src is None:
            return

        if scale is not None:
            self._scale = scale

        if target_width and target_width > 0:
            self._display_scaled(target_width)
        elif scale is not None:
            # 真缩放：显示宽度 = 源图宽 × (目标scale / 源scale)
            w = int(src.width() * scale / self._source_scale)
            self._display_scaled(w)

    # ── 尺寸适配 ────────────────────────────────────────────

    def fit_to_width(self, width: int) -> None:
        """按宽度缩放显示"""
        if self._source_image is None:
            return
        self._display_scaled(width)

    def _display_scaled(self, target_width: int) -> None:
        """内部——缩放并设置 pixmap"""
        src = self._source_image
        if src is None:
            return

        ratio = target_width / src.width()
        h = int(src.height() * ratio)

        scaled = src.scaled(
            target_width,
            h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(QPixmap.fromImage(scaled))
        self.setFixedSize(scaled.size())

    # ── 事件 ────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        # 仅双击时触发页面定位；单击留给文本选择 / 滚动等操作
        pass

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        if event and event.button() == Qt.MouseButton.LeftButton:
            self.page_clicked.emit(self._page_number)
        super().mouseDoubleClickEvent(event)


# ═══════════════════════════════════════════════════════════════
# 可滚动多页查看器
# ═══════════════════════════════════════════════════════════════

class PDFViewer(QScrollArea):
    """
    可滚动的 PDF 多页查看器。

    usage::

        viewer = PDFViewer()
        viewer.load_pdf("/path/to/doc.pdf")
    """

    DEFAULT_SCALE = 3.0   # 提高默认渲染精度，避免高分辨率屏幕下模糊
    MIN_SCALE = 0.3
    MAX_SCALE = 8.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._doc: fitz.Document | None = None
        self._pages: list[PDFPageWidget] = []
        self._scale: float = self.DEFAULT_SCALE
        self._fit_width: bool = True  # True = 自适应宽度；False = 真缩放

        # 容器
        self._container = QWidget()
        self._container.setObjectName("pdfContainer")
        self._container.setStyleSheet(
            f"QWidget#pdfContainer {{ background-color: {Colors.INK.name()}; }}"
        )

        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(12, 12, 12, 24)
        self._layout.setSpacing(4)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self.setWidget(self._container)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet(
            f"QScrollArea {{ background-color: {Colors.INK.name()}; border: none; }}"
        )

        # 空状态提示
        self._placeholder = QLabel("拖拽 PDF 文件到此处")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            f"color: {Colors.CHAR.name()};"
            "font-size: 14pt;"
            "font-style: italic;"
            "padding: 80px;"
        )
        self._layout.addWidget(self._placeholder)

    # ── properties ──────────────────────────────────────────

    @property
    def document(self) -> fitz.Document | None:
        return self._doc

    @property
    def page_count(self) -> int:
        return len(self._doc) if self._doc else 0

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def is_fit_width(self) -> bool:
        return self._fit_width

    # ── 公开方法 ────────────────────────────────────────────

    def load_pdf(self, path: str) -> None:
        """加载 PDF 并渲染所有页（默认自适应宽度）"""
        self._clear_pages()
        self._placeholder.setVisible(False)

        self._doc = fitz.open(path)
        self._fit_width = True
        self._scale = self.DEFAULT_SCALE

        # 改用 self.width() 作为主要参考——此时父布局已完成
        viewport_w = max(
            self.viewport().width() if self.viewport() else 0,
            self.width(),
            self.parent().width() if self.parent() else 0,
            600,  # 最低保底
        )
        target_w = viewport_w - 24 if self._fit_width else None

        for i in range(len(self._doc)):
            page_widget = PDFPageWidget(i)
            page_widget.render_from_page(
                self._doc[i],
                target_width=target_w,
                scale=self._scale,
            )
            page_widget.page_clicked.connect(self._on_page_clicked)
            self._layout.addWidget(page_widget)
            self._pages.append(page_widget)

    def clear(self) -> None:
        self._clear_pages()
        self._placeholder.setVisible(True)
        self._doc = None

    def zoom_in(self) -> None:
        if self._fit_width:
            self._scale = self._effective_display_scale()
        self._fit_width = False
        self._set_zoom(self._scale + 0.3)

    def zoom_out(self) -> None:
        if self._fit_width:
            self._scale = self._effective_display_scale()
        self._fit_width = False
        self._set_zoom(self._scale - 0.3)

    def _effective_display_scale(self) -> float:
        """计算当前自适应宽度对应的等效缩放比（相对 PDF 页面尺寸）。

        从 fit-width 切换为手动缩放时，以此作为基准避免跳跃。
        """
        if not self._pages:
            return 1.0
        pw = self._pages[0]
        src = pw._source_image
        if src is None:
            return 1.0
        return pw.width() * pw._source_scale / max(src.width(), 1)

    def zoom_reset(self) -> None:
        """重置为自适应宽度"""
        self._fit_width = True
        self._scale = self.DEFAULT_SCALE
        if not self._doc:
            return
        viewport_w = max(
            self.viewport().width() if self.viewport() else 0,
            self.width(),
            600,
        )
        self._render_all_with_width(viewport_w - 24)

    # ── 内部方法 ────────────────────────────────────────────

    def _clear_pages(self) -> None:
        for pw in self._pages:
            self._layout.removeWidget(pw)
            pw.deleteLater()
        self._pages.clear()

    def _render_all_with_width(self, target_w: int) -> None:
        """以自适应宽度渲染所有页 —— 从已缓存的源图缩放（快）"""
        if not self._doc:
            return
        target_w = max(target_w, 300)
        for pw in self._pages:
            pw.render_from_source(target_width=target_w)

    def _set_zoom(self, new_scale: float) -> None:
        """真缩放：从已缓存的源图按比例缩放（快），不重渲染 PDF"""
        self._scale = max(self.MIN_SCALE, min(self.MAX_SCALE, new_scale))
        if not self._doc:
            return
        for pw in self._pages:
            pw.render_from_source(scale=self._scale)

    def _on_page_clicked(self, page_number: int) -> None:
        if 0 <= page_number < len(self._pages):
            pw = self._pages[page_number]
            self.ensureWidgetVisible(pw, 0, 0)

    # ── 事件 ────────────────────────────────────────────────

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        super().resizeEvent(event)
        if self._doc and self._pages and self._fit_width:
            viewport_w = max(
                self.viewport().width() if self.viewport() else 0,
                self.width(),
                600,
            )
            self._render_all_with_width(viewport_w - 24)

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        if event is None:
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)
