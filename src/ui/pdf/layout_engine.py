"""
PDF 页面布局引擎 — 镜像 QPdfView 内部的文档布局计算。

QPdfView 的页面几何是私有的，必须自己维护一套镜像计算，
确保所有坐标与 QPdfView 内部渲染完全一致。

本实现精确复刻 Qt 6.11 的 `QPdfViewPrivate::calculateDocumentLayout()`（源码位于
qt/qtwebengine/src/pdfwidgets/qpdfview.cpp）。关键行为：

- `m_screenResolution = logicalDotsPerInch() / 72.0`（每点的物理像素数）
- 基准尺寸 `base = qRound(pt * res)` —— 注意 `QSizeF::toSize()` 实际是**四舍五入**
  而非文档所说的截断（已实测验证）
- FitToWidth：`pageScale = (vp_w - 左右边距) / base_w`，尺寸 `qRound(base * pageScale)`
- Custom：尺寸 `qRound(pt * res * zoomFactor)`
- FitInView：视口高度减的是 `pageSpacing`（不减边距），按 KeepAspectRatio 缩放
- `pageSpacing` / `documentMargins` 原样使用，不乘任何 DPI / 缩放系数
- 水平居中 `x = (max(文档总宽, 视口宽) - 页宽) / 2`
- y 从 `margins.top()` 开始，逐页累加 `page_h + pageSpacing`
- 文本内容缩放（物理像素/点）= `res * pageScale`，对应 Qt 的 `screenScaleTransform`
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import QRectF, QMargins
from PySide6.QtGui import QGuiApplication
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView


@dataclass
class PageLayout:
    """单页布局信息"""
    page_num: int
    rect: QRectF      # 内容坐标系中的矩形（物理像素）
    scale: float      # 该页文本内容渲染缩放（物理像素/点）


def _qround(value: float) -> int:
    """Qt qRound()：四舍五入，.5 向远离零的方向取整。"""
    return int(math.floor(value + 0.5)) if value >= 0 else int(math.ceil(value - 0.5))


class PdfLayoutEngine:
    """
    镜像 QPdfView 内部的文档布局计算。

    必须保证以下参数与 QPdfView 完全一致：
    - documentMargins
    - pageSpacing
    - pageMode
    - zoomMode / zoomFactor
    - 屏幕分辨率（logicalDotsPerInch / 72）
    """

    def __init__(self, pdf_view: QPdfView):
        self._pdf_view = pdf_view
        self._pdf_view.setDocumentMargins(QMargins(0, 0, 0, 0))

        self._doc: Optional[QPdfDocument] = None

    def set_document(self, doc: Optional[QPdfDocument]):
        self._doc = doc

    # ── 公共 API ──────────────────────────────────────────

    def compute_layout(self, viewport_width: int, viewport_height: int) -> List[PageLayout]:
        """计算页面布局（内容坐标系，物理像素）。

        与 QPdfView 的 calculateDocumentLayout() 一致：
        - 每页独立计算尺寸与缩放（含 qRound 舍入）
        - x 基于 (max(文档总宽, 视口宽)) 居中
        - y 从 margins.top() 起，每页累加 页高 + pageSpacing
        """
        if not self._doc or self._doc.pageCount() == 0:
            return []

        margins = self._pdf_view.documentMargins()
        page_mode = self._pdf_view.pageMode()
        zoom_mode = self._pdf_view.zoomMode()
        zoom_factor = self._pdf_view.zoomFactor()
        page_spacing = self._pdf_view.pageSpacing()
        res = self._screen_resolution()

        if page_mode == QPdfView.PageMode.SinglePage:
            current_page = self._pdf_view.pageNavigator().currentPage()
            if 0 <= current_page < self._doc.pageCount():
                pt = self._doc.pagePointSize(current_page)
                page_w, page_h, scale = self._page_geometry(
                    pt.width(), pt.height(), zoom_mode, zoom_factor,
                    viewport_width, viewport_height, margins, page_spacing, res
                )
                total_width = page_w + margins.left() + margins.right()
                x = (max(total_width, viewport_width) - page_w) / 2
                y = margins.top()
                return [PageLayout(current_page, QRectF(x, y, page_w, page_h), scale)]
            return []

        # ── MultiPage 垂直排列 ──
        geoms = [
            self._page_geometry(
                pt.width(), pt.height(), zoom_mode, zoom_factor,
                viewport_width, viewport_height, margins, page_spacing, res
            )
            for pt in (self._doc.pagePointSize(i) for i in range(self._doc.pageCount()))
        ]
        total_width = max((w for w, _, _ in geoms), default=0) + margins.left() + margins.right()

        layouts: List[PageLayout] = []
        y_offset = margins.top()
        for i, (page_w, page_h, scale) in enumerate(geoms):
            x = (max(total_width, viewport_width) - page_w) / 2
            layouts.append(PageLayout(i, QRectF(x, y_offset, page_w, page_h), scale))
            y_offset += page_h + page_spacing

        return layouts

    def current_scale(self, viewport_width: int, viewport_height: int) -> float:
        """当前模式下页面文本内容的渲染缩放（物理像素/点）。

        对应 Qt 的 screenScaleTransform：
        - Custom: res * zoomFactor
        - FitToWidth / FitInView: res * pageScale
        """
        if not self._doc or self._doc.pageCount() == 0:
            return 1.0

        margins = self._pdf_view.documentMargins()
        zoom_mode = self._pdf_view.zoomMode()
        zoom_factor = self._pdf_view.zoomFactor()
        page_spacing = self._pdf_view.pageSpacing()
        res = self._screen_resolution()
        pt = self._doc.pagePointSize(0)
        _, _, scale = self._page_geometry(
            pt.width(), pt.height(), zoom_mode, zoom_factor,
            viewport_width, viewport_height, margins, page_spacing, res
        )
        return scale

    # ── 内部实现（复刻 Qt）─────────────────────────────

    def _screen_resolution(self) -> float:
        """m_screenResolution：每点的物理像素数 = logicalDotsPerInch / 72。"""
        screen = QGuiApplication.primaryScreen()
        return screen.logicalDotsPerInch() / 72.0 if screen else 1.0

    def _page_geometry(self, pt_w: float, pt_h: float, zoom_mode, zoom_factor,
                       vp_w: int, vp_h: int, margins, page_spacing: int, res: float):
        """复刻 Qt 的单页尺寸与缩放计算，返回 (page_w, page_h, content_scale)。"""
        base_w = _qround(pt_w * res)
        base_h = _qround(pt_h * res)

        if zoom_mode == QPdfView.ZoomMode.Custom:
            # Qt: pageSize = QSizeF(pt * res * zoomFactor).toSize()
            return (
                _qround(pt_w * res * zoom_factor),
                _qround(pt_h * res * zoom_factor),
                res * zoom_factor,
            )

        if zoom_mode == QPdfView.ZoomMode.FitToWidth:
            page_scale = (vp_w - margins.left() - margins.right()) / max(base_w, 1)
            return (
                _qround(base_w * page_scale),
                _qround(base_h * page_scale),
                res * page_scale,
            )

        # FitInView：Qt 用视口尺寸减 (左右边距, pageSpacing) 后按 KeepAspectRatio 缩放
        avail_w = vp_w - margins.left() - margins.right()
        avail_h = vp_h - page_spacing
        if base_w <= 0 or base_h <= 0:
            return 0, 0, res
        rw = avail_w / base_w
        rh = avail_h / base_h
        if rh < rw:  # useHeight 分支（复刻 QSize::scaled）
            page_w = _qround(base_w * rh)
            page_h = avail_h
        else:
            page_w = avail_w
            page_h = _qround(base_h * rw)
        page_scale = page_w / base_w
        return page_w, page_h, res * page_scale
