"""
缩略图导航（minimap）控制 — 缩略图生成、定位、视口同步与跳转。

以 mixin 形式注入 MainWindow（windows/main_window.py），
依赖 self._viewer / self._minimap / self._minimap_synced 等
由 MainWindow.__init__ 初始化的状态。
"""

from __future__ import annotations

import logging

try:
    from PySide6 import shiboken6  # pip 安装的 PySide6 通常在此
except ImportError:  # conda 安装的 PySide6 把 shiboken6 作为顶层包
    import shiboken6
from PySide6.QtCore import QTimer
from PySide6.QtPdf import QPdfDocument

from src.ui.widgets.minimap import generate_thumbnails

logger = logging.getLogger(__name__)


class _MinimapControllerMixin:
    """缩略图导航与主窗口的集成控制。"""

    def _minimap_alive(self) -> bool:
        """minimap 是否存在且 C++ 对象仍有效（布局切换重建窗口期可能为 None/已删）。"""
        mm = getattr(self, "_minimap", None)
        return mm is not None and shiboken6.isValid(mm)

    def _setup_minimap(self) -> None:
        """为当前 PDF 生成缩略图并加载到 minimap（默认隐藏，通过按钮唤起）"""
        if not self._minimap_alive():
            return
        doc = self._viewer.document
        if doc is None or doc.status() != QPdfDocument.Status.Ready:
            return
        try:
            thumbs = generate_thumbnails(
                doc, self._viewer.page_count, thumb_scale=self._minimap.THUMB_SCALE
            )
            self._minimap.load_document(self._viewer.page_count, thumbs)
            self._position_minimap()
            # 监听滚动条变化（仅首次连接，避免重复）
            if not self._minimap_synced:
                self._viewer.verticalScrollBar().valueChanged.connect(
                    self._update_minimap_viewport
                )
                self._minimap_synced = True
            # 立即初始化视口指示器（布局未完成时内部自动重试）
            self._update_minimap_viewport()
        except Exception:
            logger.debug("Failed to generate thumbnails", exc_info=True)

    def _position_minimap(self) -> None:
        """将 minimap 定位到 viewer 右上角（并随高度重算面板与滚动范围）"""
        if not self._minimap_alive():
            return
        self._minimap.refresh_geometry()
        x = self._viewer.width() - self._minimap.width() - 8
        y = 8
        self._minimap.move(x, y)
        self._minimap.raise_()

    def _update_minimap_viewport(self) -> None:
        """根据当前滚动位置更新 minimap 的视口指示器。

        比值统一以「内容总高 = scrollbar_max + pageStep」为分母，
        保证指示器高度恒定（修复拖到最下方时指示器缩成一条线）。

        注意：布局切换（mono↔dual）重建 viewer 的窗口期，旧 viewer 的
        scrollbar valueChanged 信号仍可能触发本方法，而 minimap 已被销毁
        置 None —— 必须先防御，否则 AttributeError 崩掉事件分发。
        """
        if not self._minimap_alive():
            return
        vbar = self._viewer.verticalScrollBar()
        page = vbar.pageStep()
        total_h = vbar.maximum() + page
        if total_h <= 0:
            # 布局尚未完成：文档已加载则稍后重试
            if self._viewer.page_count > 0:
                QTimer.singleShot(100, self._update_minimap_viewport)
            return
        ratio_start = vbar.value() / total_h
        ratio_end = min((vbar.value() + page) / total_h, 1.0)
        self._minimap.set_visible_range(ratio_start, ratio_end)

    def _on_minimap_page_clicked(self, page_number: int) -> None:
        """点击 minimap 缩略图 → 跳转到对应页面"""
        self._viewer.goto_page(page_number)

    def _on_minimap_dragged(self, ratio: float) -> None:
        """拖拽 minimap 视口指示器 → 实时滚动 PDF（视口中心对齐拖拽点）"""
        vbar = self._viewer.verticalScrollBar()
        page = vbar.pageStep()
        total_h = vbar.maximum() + page
        if total_h <= 0:
            return
        v = int(ratio * total_h - page / 2)
        vbar.setValue(max(0, min(v, vbar.maximum())))
