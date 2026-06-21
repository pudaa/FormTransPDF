"""
PDF 页面布局引擎 — 镜像 QPdfView 内部的文档布局计算。

QPdfView 的页面几何是私有的，必须自己维护一套镜像计算，
确保所有坐标与 QPdfView 内部渲染完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import QRectF, QMargins
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView


@dataclass
class PageLayout:
    """单页布局信息"""
    page_num: int
    rect: QRectF      # 内容坐标系中的矩形（像素）
    scale: float      # 该页的实际渲染缩放（像素/点）


class PdfLayoutEngine:
    """
    镜像 QPdfView 内部的文档布局计算。

    必须保证以下参数与 QPdfView 完全一致：
    - documentMargins
    - pageSpacing
    - pageMode
    - zoomMode / zoomFactor
    """

    def __init__(self, pdf_view: QPdfView):
        self._pdf_view = pdf_view
        self._pdf_view.setDocumentMargins(QMargins(0, 0, 0, 0))
        
        self._doc: Optional[QPdfDocument] = None
        

    def set_document(self, doc: Optional[QPdfDocument]):
        self._doc = doc

    def compute_layout(self, viewport_width: int, viewport_height: int,
                       explicit_scale: float | None = None) -> List[PageLayout]:
        """计算页面布局。

        Args:
            explicit_scale: 显式指定缩放比（物理像素/点）。

        pageSpacing 是原始物理像素值，QPdfView 不对其应用 DPI 缩放
        （与 zoomFactor 不同，后者会被解释为 72DPI 逻辑像素再乘 DPI 比率）。

        页面 x 位置基于 viewport_width 居中，与 QPdfView 内部行为一致。
        """
        if not self._doc or self._doc.pageCount() == 0:
            return []

        margins = self._pdf_view.documentMargins()
        page_mode = self._pdf_view.pageMode()
        zoom_mode = self._pdf_view.zoomMode()
        zoom_factor = self._pdf_view.zoomFactor()
        page_spacing = self._pdf_view.pageSpacing()

        layouts: List[PageLayout] = []

        if page_mode == QPdfView.PageMode.SinglePage:
            current_page = self._pdf_view.pageNavigator().currentPage()
            if 0 <= current_page < self._doc.pageCount():
                pt_size = self._doc.pagePointSize(current_page)
                scale = explicit_scale if explicit_scale is not None else self._compute_scale(
                    zoom_mode, zoom_factor, viewport_width, viewport_height,
                    margins, pt_size.width(), pt_size.height()
                )
                page_w = pt_size.width() * scale
                page_h = pt_size.height() * scale
                x = max((viewport_width - page_w) / 2, margins.left())
                y = margins.top()
                layouts.append(PageLayout(current_page, QRectF(x, y, page_w, page_h), scale))
        else:
            # ── MultiPage 垂直排列 ──
            all_pt = [self._doc.pagePointSize(i) for i in range(self._doc.pageCount())]
            if explicit_scale is not None:
                unified_scale = explicit_scale
            else:
                first_pt = all_pt[0]
                unified_scale = self._compute_scale(
                    zoom_mode, zoom_factor, viewport_width, viewport_height,
                    margins, first_pt.width(), first_pt.height()
                )

            y_offset = margins.top()
            for i, pt_size in enumerate(all_pt):
                page_w = pt_size.width() * unified_scale
                page_h = pt_size.height() * unified_scale
                # 基于 viewport_width 居中，与 QPdfView 一致
                x = max((viewport_width - page_w) / 2, margins.left())

                layouts.append(PageLayout(i, QRectF(x, y_offset, page_w, page_h), unified_scale))
                y_offset += page_h + page_spacing

        return layouts

    def _compute_scale(self, zoom_mode, zoom_factor, vp_w, vp_h, margins, page_w, page_h) -> float:
        if zoom_mode == QPdfView.ZoomMode.FitToWidth:
            available_w = max(vp_w - margins.left() - margins.right(), 1)
            return available_w / max(page_w, 1)
        elif zoom_mode == QPdfView.ZoomMode.FitInView:
            available_w = max(vp_w - margins.left() - margins.right(), 1)
            available_h = max(vp_h - margins.top() - margins.bottom(), 1)
            return min(available_w / max(page_w, 1), available_h / max(page_h, 1))
        else:
            return zoom_factor
