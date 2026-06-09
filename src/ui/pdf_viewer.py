"""
PDF 页面渲染与查看组件

基于 PyMuPDF（fitz），将 PDF 页面渲染为 QImage，由 QLabel 承载展示。
支持缩放、分页渲染、自适应宽度。

渲染策略：
  Zotero / PDF.js 使用浏览器原生 DirectWrite（ClearType 次像素渲染），
  文字边缘锐利、颜色深。PyMuPDF 使用 FreeType 灰阶抗锯齿，天生偏软。
  为弥补差距，采用 2× 超采样渲染 + 高质量降采样：
  - 显示：PyMuPDF 按 2× 目标尺寸渲染 → QImage.scaled(SmoothTransformation) 降采样
  - 缩放：2.5× 高分辨率源图缓存 → 缩放时从缓存渲染
  2× 超采样相当于每个输出像素各 4 个采样点，效果接近次像素渲染。
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

    双缓冲策略：
    - _source_image: 高分辨率源图（≥ 2.5× 显示宽度），用于手动缩放
    - 显示：直接从 PyMuPDF 按精确分辨率渲染，无 QImage 二次缩放，文字锐利
    """

    page_clicked = pyqtSignal(int)  # page_number

    def __init__(self, page_number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page_number = page_number
        self._page: fitz.Page | None = None      # PyMuPDF 页面引用，用于精确渲染
        self._source_image: QImage | None = None  # 高分辨率缓存，用于手动缩放
        self._source_scale: float = 1.0
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
        """从 PyMuPDF Page 渲染。

        - target_width 非 None：自适应宽度，2× 超采样显示 + 缓源图供缩放。
        - target_width 为 None：真缩放模式，直接按 scale 显示。
        """
        self._page = page
        self._scale = scale

        if target_width and target_width > 0:
            page_w = page.rect.width
            # 源图缓存：至少 2.5× 显示宽度
            self._source_scale = max(scale, target_width * 2.5 / page_w)
            mat = fitz.Matrix(self._source_scale, self._source_scale)
            pix = page.get_pixmap(matrix=mat)
            self._source_image = QImage(
                pix.samples, pix.width, pix.height,
                pix.stride, QImage.Format.Format_RGB888,
            ).copy()
            # 显示：2× 超采样 → 降采样（补偿 FreeType 无次像素渲染）
            self._render_supersampled(target_width)
        else:
            self._source_scale = scale
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat)
            img = QImage(
                pix.samples, pix.width, pix.height,
                pix.stride, QImage.Format.Format_RGB888,
            )
            self._source_image = img.copy()
            self.setPixmap(QPixmap.fromImage(self._source_image))
            self.setFixedSize(self._source_image.size())

    def render_from_source(
        self,
        target_width: int | None = None,
        scale: float | None = None,
    ) -> None:
        """缩放显示：有 target_width 时精确渲染，否则从源图缩放。"""
        src = self._source_image
        if src is None:
            return

        if scale is not None:
            self._scale = scale

        if target_width and target_width > 0:
            # 适配宽度 → 2× 超采样渲染
            self._render_supersampled(target_width)
        elif scale is not None:
            # 手动缩放 → 从高分辨率源图缩放
            w = int(src.width() * scale / self._source_scale)
            self._display_scaled(w)

    # ── 尺寸适配 ────────────────────────────────────────────

    def _render_supersampled(self, target_width: int) -> None:
        """2× 超采样渲染 + SmoothTransformation 降采样。

        Zotero / PDF.js 使用 DirectWrite（ClearType 次像素渲染），
        文字边缘锐利、墨色深。PyMuPDF 依赖 FreeType 灰阶抗锯齿，
        天生边缘偏软。以 2× 超采样（每输出像素 4 采样点）弥补差距。
        """
        if self._page is None:
            return
        page_w = self._page.rect.width
        # 2× 超采样
        render_scale = target_width * 2.0 / page_w
        mat = fitz.Matrix(render_scale, render_scale)
        pix = self._page.get_pixmap(matrix=mat)
        hires = QImage(
            pix.samples, pix.width, pix.height,
            pix.stride, QImage.Format.Format_RGB888,
        ).copy()
        # 降采样到目标宽度
        disp_h = int(hires.height() * target_width / hires.width())
        display = hires.scaled(
            target_width, disp_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(QPixmap.fromImage(display))
        self.setFixedSize(display.size())

    def _display_scaled(self, target_width: int) -> None:
        """从高分辨率源图缩放（手动缩放时使用）"""
        src = self._source_image
        if src is None:
            return
        ratio = target_width / src.width()
        h = int(src.height() * ratio)
        scaled = src.scaled(
            target_width, h,
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
