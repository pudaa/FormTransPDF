"""
PDF 文本选择交互 — 划词选择、中键拖拽、浮动工具栏动作。

以 mixin 形式与 PDFViewerCore 组合为最终 PDFViewer：

    class PDFViewer(TextSelectionMixin, PDFViewerCore):
        ...

依赖 PDFViewerCore.__init__ 初始化的状态：
- self._pdf_view / self._text_overlay / self._text_spans
- self._selecting / self._select_start / self._selected_text 等选择状态
- self._panning / self._pan_start / self._pan_scroll_start 等平移状态

事件分发由最终类 PDFViewer.eventFilter 完成，本 mixin 仅实现状态机。
"""

from __future__ import annotations

import urllib.parse

from PySide6.QtCore import QPoint, QRectF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QMouseEvent
from PySide6.QtWidgets import QApplication


class TextSelectionMixin:
    """文本选择 / 中键拖拽 / 浮动工具栏交互逻辑。"""

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

    # ── 高亮计算 ───────────────────────────────────────────

    def _update_highlights_for_selection(self, content_rect: QRectF):
        """根据内容坐标系的选择矩形，计算并设置 viewport 高亮。

        译文态（COVER_TRANSLATED）下选区对应的是译文段落（封面 segment），
        高亮与显示的译文块对齐；其他模式仍按原始 span 高亮。
        """
        highlights = []
        if self._cover_mode == "translated" and self._cover_segments:
            for page_num in sorted(self._cover_segments):
                for seg in self._cover_segments[page_num]:
                    if seg.content_rect and content_rect.intersects(seg.content_rect):
                        vp_rect = self._content_rect_to_viewport(seg.content_rect)
                        highlights.append(vp_rect)
        else:
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
        """获取选择矩形内的文本（按页面顺序拼接）。

        译文态（COVER_TRANSLATED）下选区对应译文段落，复制得到译文；
        其他模式返回原文 span 文本。
        """
        texts = []
        if self._cover_mode == "translated" and self._cover_segments:
            for page_num in sorted(self._cover_segments):
                for seg in self._cover_segments[page_num]:
                    if seg.content_rect and content_rect.intersects(seg.content_rect):
                        texts.append(seg.display_text or seg.text)
            return "".join(texts)
        for page_num in sorted(self._text_spans.keys()):
            spans = self._text_spans[page_num]
            for span in spans:
                if span.content_rect and content_rect.intersects(span.content_rect):
                    texts.append(span.text)
        return "".join(texts)

    # ── 工具栏动作 ─────────────────────────────────────────

    def _copy_selected_text(self):
        """复制选中文本到剪贴板（带「已复制」按钮态 + 弹窗气泡提示）"""
        if not self._selected_text:
            return
        QApplication.clipboard().setText(self._selected_text)
        self._text_overlay.toolbar.show_copied()
        # 弹窗提示「已复制」（定位在选区下方）
        rects = self._text_overlay._highlights
        anchor = None
        if rects:
            united = rects[0]
            for r in rects[1:]:
                united = united.united(r)
            anchor = united
        self._text_overlay.show_toast("已复制", anchor)

    def _on_toolbar_translate(self):
        """浮动工具栏「翻译」：把选中文本交给主窗口的即时翻译。"""
        if self._selected_text:
            self.translate_requested.emit(self._selected_text)

    def _on_toolbar_search(self):
        """浮动工具栏「搜索」：在浏览器中用 Google Scholar 检索选中文本。"""
        if not self._selected_text:
            return
        query = urllib.parse.quote(self._selected_text.strip())
        QDesktopServices.openUrl(QUrl(f"https://scholar.google.com/scholar?q={query}"))

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

    # ── 字块拖拽（Alt+左键：把被遮盖的字块拖出来查看）──────────

    def _on_drag_press(self, me: QMouseEvent) -> bool:
        """Alt+左键按下：命中段则进入拖拽（整段平移）。返回 True 表示消费。

        仅在覆盖层可见的模式（译文/原文覆盖）下启用 —— 透明态没有白底可拖。
        """
        if self._cover_mode == "transparent":
            return False
        seg = self.cover_segment_at(me.pos())
        if seg is None:
            return False
        self._drag_seg = seg
        self._drag_start_pos = me.pos()
        self._drag_orig_offset = (seg.offset_x, seg.offset_y)
        vp = self._pdf_view.viewport()
        if vp is not None:
            vp.setCursor(Qt.CursorShape.ClosedHandCursor)
        return True

    def _on_drag_move(self, me: QMouseEvent) -> bool:
        """拖拽移动：按鼠标位移更新命中段偏移并重绘。返回 True 表示消费。"""
        if self._drag_seg is None:
            return False
        delta = me.pos() - self._drag_start_pos
        self._drag_seg.offset_x = self._drag_orig_offset[0] + float(delta.x())
        self._drag_seg.offset_y = self._drag_orig_offset[1] + float(delta.y())
        self._push_cover(bump=True)  # 每次移动重建页缓存（段多时仍可接受）
        return True

    def _on_drag_release(self, me: QMouseEvent) -> bool:
        """拖拽释放：结束拖拽（保持偏移），恢复光标。返回 True 表示消费。"""
        if self._drag_seg is None:
            return False
        self._drag_seg = None
        self._drag_start_pos = QPoint()
        vp = self._pdf_view.viewport()
        if vp is not None:
            vp.setCursor(Qt.CursorShape.ArrowCursor)
        return True

    def _on_drag_double_click(self, me: QMouseEvent) -> bool:
        """双击命中段：复位其拖拽偏移。返回 True 表示消费。"""
        seg = self.cover_segment_at(me.pos())
        if seg is None:
            return False
        seg.offset_x = 0.0
        seg.offset_y = 0.0
        self._push_cover(bump=True)
        return True

    # ── 左键划词状态机 ────────────────────────────────────

    def _on_select_press(self, me: QMouseEvent) -> None:
        """左键按下：初始化选择状态（不消耗事件，让 QPdfView 正常处理单击）。"""
        self._selecting = True
        self._drag_threshold_met = False
        self._select_start = me.pos()
        self._selected_text = ""
        self._selected_content_rect = None
        self._text_overlay.clear_highlights()

    def _on_select_move(self, me: QMouseEvent) -> bool:
        """左键拖动：超过阈值后实时高亮选区，返回是否消费事件。"""
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

    def _on_select_release(self, me: QMouseEvent) -> bool:
        """左键释放：计算最终选中文本并发射 text_selected 信号。"""
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

    # ── 中键拖拽平移 ──────────────────────────────────────

    def _on_pan_begin(self, me: QMouseEvent) -> None:
        """中键按下：记录起始位置并切换抓手光标。"""
        self._panning = True
        self._pan_start = me.globalPosition().toPoint()
        h = self.horizontalScrollBar()
        v = self.verticalScrollBar()
        self._pan_scroll_start = QPoint(h.value() if h else 0, v.value() if v else 0)
        self._pdf_view.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def _on_pan_move(self, me: QMouseEvent) -> bool:
        """中键拖动：按位移更新滚动条，返回 True 消费事件。"""
        delta = me.globalPosition().toPoint() - self._pan_start
        h = self.horizontalScrollBar()
        v = self.verticalScrollBar()
        if h:
            h.setValue(self._pan_scroll_start.x() - delta.x())
        if v:
            v.setValue(self._pan_scroll_start.y() - delta.y())
        return True

    def _on_pan_end(self, me: QMouseEvent) -> None:
        """中键释放：结束平移并恢复光标。"""
        self._panning = False
        self._pan_start = None
        self._pan_scroll_start = None
        self._pdf_view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
