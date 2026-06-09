"""
翻译历史记录组件 — 扫描 output/ 目录展示已完成翻译
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.theme import Colors


# ═══════════════════════════════════════════════════════════════
# 一条历史记录的数据
# ═══════════════════════════════════════════════════════════════

@dataclass
class HistoryEntry:
    """output 目录中的一组翻译结果"""
    display_name: str          # 显示名（原始文件名去掉语言后缀）
    pdf_path: str | None       # 外部 PDF 路径
    mono_pdf: Path | None      # 仅译文
    dual_pdf: Path | None      # 双语对照
    csv_path: Path | None      # 词汇表
    timestamp: float            # 文件修改时间


# ═══════════════════════════════════════════════════════════════
# 历史记录列表组件
# ═══════════════════════════════════════════════════════════════

class HistoryPanel(QWidget):
    """扫描 output/ 目录，以列表展示历史翻译记录"""

    result_selected = Signal(str, str, str)  # dual_path, mono_path, display_name

    def __init__(self, output_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._output_dir = output_dir
        self._entries: list[HistoryEntry] = []

        self.setObjectName("historyPanel")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 1, 0, 0)
        layout.setSpacing(4) # 列表项间距

        # 标题行
        # header = QHBoxLayout()
        # title = QLabel("历史记录")
        # title.setStyleSheet(
        #     f"color: {Colors.ASH.name() if hasattr(Colors, 'ASH') else '#8a8578'};"
        #     "font-size: 10pt; font-weight: 600;"
        # )
        # header.addWidget(title)

        # self._refresh_btn = QPushButton("↻")
        # self._refresh_btn.setFixedSize(24, 24)
        # self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # self._refresh_btn.setToolTip("刷新历史记录")
        # self._refresh_btn.clicked.connect(self.refresh)
        # self._refresh_btn.setStyleSheet(
        #     "QPushButton { background: transparent; border: none; font-size: 12pt; }"
        #     "QPushButton:hover { color: #d4a853; }"
        # )
        # header.addWidget(self._refresh_btn)
        # header.addStretch()
        # layout.addLayout(header)

        # 列表
        self._list = QListWidget()
        self._list.setAlternatingRowColors(False)
        self._list.setSpacing(2)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.setStyleSheet(
            f"QListWidget {{ background: transparent; border: 1px solid {Colors.DIVIDER.name() if hasattr(Colors, 'DIVIDER') else '#2a2a30'};"
            f"border-radius: 4px; padding: 2px; }}"
            f"QListWidget::item {{ padding: 4px 6px; border-radius: 2px; font-size: 9pt; }}"
            f"QListWidget::item:hover {{ background: {Colors.GOLD_MUTED.name() if hasattr(Colors, 'GOLD_MUTED') else '#3d3524'}; }}"
            f"QListWidget::item:selected {{ background: {Colors.GOLD.name() if hasattr(Colors, 'GOLD') else '#d4a853'};"
            f"color: #0d0d0d; }}"
        )
        layout.addWidget(self._list)

        # 空状态提示
        self._empty_label = QLabel("暂无翻译记录")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color: {Colors.CHAR.name() if hasattr(Colors, 'CHAR') else '#4a4640'};"
            "font-size: 9pt; font-style: italic; padding: 8px;"
        )
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label)

    # ═══════════════════════════════════════════════════════════
    # 扫描与刷新
    # ═══════════════════════════════════════════════════════════

    def refresh(self) -> None:
        """重新扫描 output 目录"""
        self._entries = self._scan_output_dir()
        self._rebuild_list()

    def _scan_output_dir(self) -> list[HistoryEntry]:
        """扫描目录，按文件分组返回"""
        entries: dict[str, HistoryEntry] = {}

        if not self._output_dir.exists():
            return []

        for f in sorted(self._output_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            name = f.name

            # 匹配 *.zh.dual.pdf / *.zh.mono.pdf / *.zh.glossary.csv
            if name.endswith(".zh.dual.pdf"):
                base = name[:-len(".zh.dual.pdf")]
                # 提取更干净的名字
                display = _clean_name(base)
                if base not in entries:
                    entries[base] = HistoryEntry(
                        display_name=display,
                        pdf_path=None,
                        mono_pdf=None, dual_pdf=None, csv_path=None,
                        timestamp=f.stat().st_mtime,
                    )
                entries[base].dual_pdf = f
                entries[base].timestamp = max(entries[base].timestamp, f.stat().st_mtime)

            elif name.endswith(".zh.mono.pdf"):
                base = name[:-len(".zh.mono.pdf")]
                display = _clean_name(base)
                if base not in entries:
                    entries[base] = HistoryEntry(
                        display_name=display,
                        pdf_path=None,
                        mono_pdf=None, dual_pdf=None, csv_path=None,
                        timestamp=f.stat().st_mtime,
                    )
                entries[base].mono_pdf = f
                entries[base].timestamp = max(entries[base].timestamp, f.stat().st_mtime)

            elif name.endswith(".zh.glossary.csv"):
                base = name[:-len(".zh.glossary.csv")]
                display = _clean_name(base)
                if base not in entries:
                    entries[base] = HistoryEntry(
                        display_name=display,
                        pdf_path=None,
                        mono_pdf=None, dual_pdf=None, csv_path=None,
                        timestamp=f.stat().st_mtime,
                    )
                entries[base].csv_path = f
                entries[base].timestamp = max(entries[base].timestamp, f.stat().st_mtime)

        # 按时间降序排列
        result = sorted(entries.values(), key=lambda e: e.timestamp, reverse=True)
        return result

    def _rebuild_list(self) -> None:
        self._list.clear()

        if not self._entries:
            self._empty_label.setVisible(True)
            return

        self._empty_label.setVisible(False)
        for entry in self._entries:
            icon = "📄"
            label = entry.display_name
            item = QListWidgetItem(f"{icon}  {label}")
            item.setData(Qt.ItemDataRole.UserRole, self._list.count() - 1)  # index
            item.setToolTip(self._build_tooltip(entry))
            self._list.addItem(item)

    @staticmethod
    def _build_tooltip(entry: HistoryEntry) -> str:
        parts = [f"双栏: {entry.dual_pdf.name}" if entry.dual_pdf else ""]
        if entry.mono_pdf:
            parts.append(f"单栏: {entry.mono_pdf.name}")
        if entry.csv_path:
            parts.append(f"词汇表: {entry.csv_path.name}")
        return "\n".join(p for p in parts if p)

    # ═══════════════════════════════════════════════════════════
    # 交互
    # ═══════════════════════════════════════════════════════════

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None or idx >= len(self._entries):
            return
        entry = self._entries[idx]
        dual = str(entry.dual_pdf) if entry.dual_pdf else ""
        mono = str(entry.mono_pdf) if entry.mono_pdf else ""
        self.result_selected.emit(dual, mono, entry.display_name)


def _clean_name(base: str) -> str:
    """去掉常见的后缀，使文件名更干净"""
    for suffix in [".zh", ".en", ".ja", "-zh", "-en"]:
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    # 如果太长则截断
    if len(base) > 42:
        base = base[:39] + "..."
    return base
