"""
拖拽区组件 — 支持拖入 PDF 文件的接收区
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPainter, QPen, QColor
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from src.ui.theme import Colors


class DropZone(QWidget):
    """
    文件拖拽接收区。

    当用户拖入 PDF 文件时高亮边框。
    仅接受 .pdf 后缀的文件。

    Signal:
        pdf_dropped(str) — 传入 PDF 文件绝对路径
    """

    pdf_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hovered = False

        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        self.setObjectName("dropZone")

        # 布局
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon = QLabel("⊞")
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setStyleSheet(
            f"font-size: 32pt; color: {Colors.ASH.name()}; background: transparent;"
        )
        layout.addWidget(self._icon)

        self._label = QLabel("拖拽 PDF 文件到此处\n或点击「选择文件」")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            f"color: {Colors.ASH.name()}; font-size: 11pt; background: transparent;" 
        )
        layout.addWidget(self._label)

        self._update_style()

    # ── 样式 ────────────────────────────────────────────────

    def _update_style(self) -> None:
        border_color = Colors.GOLD.name() if self._hovered else Colors.DIVIDER.name()
        bg = Colors.GOLD_MUTED.name() if self._hovered else Colors.SLATE.name()
        icon_color = Colors.GOLD.name() if self._hovered else Colors.ASH.name()

        self.setStyleSheet(
            f"QWidget#dropZone {{"
            f"  background-color: {bg};"
            f"  border: 2px dashed {border_color};"
            f"  border-radius: 6px;"
            f"}}"
        )
        self._icon.setStyleSheet(
            f"font-size: 32pt; color: {icon_color}; background: transparent;"
        )

    # ── 拖拽事件 ────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is None:
            return
        if self._has_pdf(event.mimeData().urls()):
            self._hovered = True
            self._update_style()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._hovered = False
        self._update_style()

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        self._hovered = False
        self._update_style()

        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".pdf" and path.exists():
                self.pdf_dropped.emit(str(path.resolve()))
                event.acceptProposedAction()
                return

    # ── 辅助 ────────────────────────────────────────────────

    @staticmethod
    def _has_pdf(urls: list) -> bool:
        for url in urls:
            if Path(url.toLocalFile()).suffix.lower() == ".pdf":
                return True
        return False
