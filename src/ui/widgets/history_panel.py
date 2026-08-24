"""
翻译历史记录组件 — 扫描 output/ 目录展示已完成翻译，支持单个/批量删除
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.translation.records import (
    ROUGH_SIDECAR_SUFFIX,
    SIDECAR_SUFFIX,
    load_rough_sidecar,
    read_source_info,
)
from src.ui.base.icon_factory import svg_icon, svg_pixmap
from src.ui.base.theme import Colors, ThemeMode, _contrast_text, theme_manager

logger = logging.getLogger(__name__)


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
    # ── 粗译缓存记录（无 BabelDOC 结果 PDF，仅 .rough.json）──
    is_rough: bool = False
    rough_cache: Path | None = None
    source_hash: str = ""       # 源文件内容指纹（sidecar 提供；空=旧记录）
    source_bytes_hash: str = ""  # 源文件字节指纹（sidecar 提供）
    source_name: str = ""       # 原始源文件名（sidecar 提供）
    source_path: str = ""       # 原文件绝对路径（sidecar 提供；历史打开原文用）
    sidecar: Path | None = None  # 源文件指纹 sidecar（删除时一并清理）


# ═══════════════════════════════════════════════════════════════
# 可勾选列表（选择模式下：checkbox 区域由 Qt 处理，其余区域点击切换勾选）
# ═══════════════════════════════════════════════════════════════

class _CheckableList(QListWidget):
    """整行点击可切换勾选的 QListWidget。

    选择模式下：点击可勾选 item 的任意区域（含 checkbox 指示器）都手动切换
    checkState 一次；不调用基类 mousePressEvent 处理，因此 Qt 不会在其
    release 阶段的 editorEvent 中再次切换，避免双重翻转。状态统一由
    itemChanged 信号驱动外部按钮更新。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._select_mode = False

    def set_select_mode(self, active: bool) -> None:
        self._select_mode = active

    def mousePressEvent(self, event) -> None:
        if not self._select_mode:
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint()
        item = self.itemAt(pos)
        if item is not None and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            # 任意区域点击均手动切换一次（不调用 super，杜绝 Qt 自动切换的双重翻转）
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            self.setCurrentItem(item)
            return
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════
# 历史记录列表组件
# ═══════════════════════════════════════════════════════════════

class HistoryPanel(QWidget):
    """扫描 output/ 目录，以列表展示历史翻译记录，支持单个/批量删除"""

    result_selected = Signal(str, str, str, str)  # dual_path, mono_path, display_name, source_path
    rough_selected = Signal(str)          # 粗译记录被点击 → 参数为源文件路径
    about_to_delete_files = Signal(list)  # 确认删除后、执行删除前发出将被删除的文件列表（供释放句柄）

    def __init__(self, output_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._output_dir = output_dir
        self._entries: list[HistoryEntry] = []
        self._select_mode = False      # 多选删除模式
        self._last_selected_name = ""  # 主题刷新后恢复选中项

        self.setObjectName("historyPanel")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(4)

        # ── 标题行：历史记录 + 选择 / 删除 ──
        header = QHBoxLayout()
        header.setSpacing(4)

        self._header_title = QLabel("历史记录")
        self._header_title.setStyleSheet(
            f"color: {Colors.ASH.name()}; font-size: 9pt; font-weight: 600;"
            "background: transparent; padding-left: 2px;"
        )
        header.addWidget(self._header_title)
        header.addStretch()

        self._select_btn = QPushButton("选择")
        self._select_btn.setToolTip("进入多选模式，可批量删除记录")
        self._select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_btn.clicked.connect(self._toggle_select_mode)
        header.addWidget(self._select_btn)

        self._delete_btn = QPushButton("删除")
        self._delete_btn.setToolTip("删除当前选中的历史记录")
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        header.addWidget(self._delete_btn)

        layout.addLayout(header)

        # ── 列表 ──
        self._list = _CheckableList()
        self._list.setAlternatingRowColors(False)
        self._list.setSpacing(2)
        # 关闭焦点策略：根除选中项上的键盘焦点框（focus rect）——
        # 历史列表以鼠标操作为主，不需要焦点框；否则列表获得焦点时
        # Qt delegate 会在选中行绘制 1px 黑框，转移焦点后消失。
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemChanged.connect(self._on_item_state_changed)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.setStyleSheet(self._list_stylesheet())
        layout.addWidget(self._list, stretch=1)

        # 空状态提示
        self._empty_label = QLabel("暂无翻译记录")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color: {Colors.CHAR.name()};"
            "font-size: 9pt; font-style: italic; padding: 8px;"
        )
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label)

        self._apply_button_styles()

    def _list_stylesheet(self) -> str:
        """按当前主题生成列表 QSS（主题切换时由 refresh_theme 重建）

        - :selected 与 :selected:!active 均显式声明，避免非激活窗口时
          Qt 回退到系统默认选中样式（黑色边框来源）
        - 选择模式 checkbox 指示器随主题着色；勾选态用主题对比色对勾
        """
        tp = theme_manager.palette
        contrast = _contrast_text(tp.accent).name()
        check_png = self._check_indicator_path()
        return (
            f"QListWidget {{ background: transparent;"
            f"border: 1px solid {tp.divider.name()};"
            f"border-radius: 4px; padding: 2px; }}"
            f"QListWidget::item {{ padding: 5px 8px; border-radius: 3px; font-size: 9pt; }}"
            f"QListWidget::item:hover {{ background: {tp.accent_muted.name()}; }}"
            f"QListWidget::item:selected, QListWidget::item:selected:!active,"
            f"QListWidget::item:selected:focus, QListWidget::item:focus {{"
            f"  background: {tp.accent.name()};"
            f"  color: {contrast};"
            f"  border: none; outline: none; }}"
            # ── 选择模式 checkbox 指示器（明暗主题联动）──
            f"QListWidget::indicator {{ width: 14px; height: 14px;"
            f"border: 1px solid {tp.text_secondary.name()}; border-radius: 3px;"
            f"background: transparent; margin-right: 4px; }}"
            f"QListWidget::indicator:hover {{ border-color: {tp.accent.name()}; }}"
            f"QListWidget::indicator:checked {{ background: {tp.accent.name()};"
            f"border-color: {tp.accent.name()};"
            f"image: url(\"{check_png}\"); }}"
        )

    @staticmethod
    def _check_indicator_path() -> str:
        """渲染当前主题的勾选对勾 PNG（缓存到系统临时目录），返回正斜杠路径。

        对勾颜色 = 强调色对比色（暗色主题金底→深色对勾；亮色主题青铜底→白色对勾）。
        """
        tp = theme_manager.palette
        mode = "dark" if tp.mode == ThemeMode.DARK else "light"
        cache_dir = Path(tempfile.gettempdir()) / "formtranspdf_theme"
        cache_dir.mkdir(parents=True, exist_ok=True)
        png = cache_dir / f"check_{mode}.png"
        if not png.exists():
            svg_pixmap("check", _contrast_text(tp.accent), 14).save(str(png), "PNG")
        return str(png).replace("\\", "/")

    def _apply_button_styles(self) -> None:
        """标题行小按钮样式（随主题刷新）"""
        tp = theme_manager.palette
        style = (
            f"QPushButton {{ background: transparent; color: {tp.text_secondary.name()};"
            f"border: 1px solid {tp.divider.name()}; border-radius: 3px;"
            f"padding: 2px 8px; font-size: 8pt; }}"
            f"QPushButton:hover {{ background: {tp.accent_muted.name()};"
            f"color: {tp.text_primary.name()}; }}"
            f"QPushButton:disabled {{ color: {tp.text_disabled.name()};"
            f"border-color: {tp.divider.name()}; }}"
        )
        for btn in (self._select_btn, self._delete_btn):
            btn.setStyleSheet(style)

    # ═══════════════════════════════════════════════════════════
    # 扫描与刷新
    # ═══════════════════════════════════════════════════════════

    def refresh(self) -> None:
        """重新扫描 output 目录"""
        self._entries = self._scan_output_dir()
        self._last_selected_name = ""  # 扫描后不恢复旧选中
        self._rebuild_list()
        self._update_delete_btn()

    def refresh_theme(self) -> None:
        """主题切换后：重建 QSS / 图标 / 按钮样式，并尽量保持选中项"""
        # 记住当前选中项（重建后恢复）
        item = self._list.currentItem()
        idx = self._item_index(item)
        if idx is not None and 0 <= idx < len(self._entries):
            self._last_selected_name = self._entries[idx].display_name

        self._header_title.setStyleSheet(
            f"color: {Colors.ASH.name()}; font-size: 9pt; font-weight: 600;"
            "background: transparent; padding-left: 2px;"
        )
        self._empty_label.setStyleSheet(
            f"color: {Colors.CHAR.name()};"
            "font-size: 9pt; font-style: italic; padding: 8px;"
        )
        self._list.setStyleSheet(self._list_stylesheet())
        self._apply_button_styles()
        self._rebuild_list()
        self._update_delete_btn()

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

            elif name.endswith(SIDECAR_SUFFIX):
                # 源文件指纹 sidecar：提供源文件内容/字节指纹（用于重复提交复用检测）
                base = name[:-len(SIDECAR_SUFFIX)]
                display = _clean_name(base)
                if base not in entries:
                    entries[base] = HistoryEntry(
                        display_name=display,
                        pdf_path=None,
                        mono_pdf=None, dual_pdf=None, csv_path=None,
                        timestamp=f.stat().st_mtime,
                    )
                entries[base].sidecar = f
                info = read_source_info(f)
                if info:
                    entries[base].source_hash = info.source_hash
                    entries[base].source_bytes_hash = info.source_bytes_hash
                    entries[base].source_name = info.source_name
                    entries[base].source_path = info.source_path
                    if info.timestamp:
                        entries[base].timestamp = max(entries[base].timestamp, info.timestamp)

        # 按时间降序排列（含粗译缓存记录）
        rough_entries = self._scan_rough_cache()
        result = sorted(
            list(entries.values()) + rough_entries,
            key=lambda e: e.timestamp, reverse=True,
        )
        return result

    def _scan_rough_cache(self) -> list[HistoryEntry]:
        """扫描 output/rough_cache/*.rough.json，生成「粗译」历史记录。

        粗译没有结果 PDF —— 记录代表一份可复用的译文缓存：点击后重新打开
        源文件，文本层就绪时按内容指纹自动命中缓存免翻译呈现。
        """
        results: list[HistoryEntry] = []
        rough_dir = self._output_dir / "rough_cache"
        if not rough_dir.exists():
            return results
        for f in sorted(rough_dir.glob(f"*{ROUGH_SIDECAR_SUFFIX}")):
            data = load_rough_sidecar(f)
            if not data:
                continue
            src_name = str(data.get("source_name") or "")
            display = f"粗译 · {src_name}" if src_name else "粗译 · 未知名文档"
            try:
                ts = float(data.get("timestamp") or 0.0)
            except (TypeError, ValueError):
                ts = 0.0
            results.append(HistoryEntry(
                display_name=display,
                pdf_path=None, mono_pdf=None, dual_pdf=None, csv_path=None,
                timestamp=ts or f.stat().st_mtime,
                source_hash=str(data.get("source_hash") or ""),
                source_bytes_hash=str(data.get("source_bytes_hash") or ""),
                source_name=src_name,
                source_path=str(data.get("source_path") or ""),
                is_rough=True,
                rough_cache=f,
            ))
        return results

    def find_by_hash(self, source_hash: str, source_bytes_hash: str = "") -> HistoryEntry | None:
        """按源文件指纹查找已有翻译记录（优先 mono+dual 齐全的）。

        内容指纹或字节指纹任一命中即可；两者都为空时返回 None。
        """
        if not source_hash and not source_bytes_hash:
            return None

        def _matches(e: HistoryEntry) -> bool:
            if e.is_rough:
                return False  # 粗译缓存不是 BabelDOC 结果，不参与复用门控
            if source_bytes_hash and e.source_bytes_hash == source_bytes_hash:
                return True
            if source_hash and e.source_hash == source_hash:
                return True
            return False

        fallback: HistoryEntry | None = None
        for e in self._entries:
            if not _matches(e):
                continue
            if e.dual_pdf and e.mono_pdf:
                return e
            if fallback is None:
                fallback = e
        return fallback

    def _rebuild_list(self) -> None:
        self._list.clear()

        if not self._entries:
            self._empty_label.setVisible(True)
            return

        self._empty_label.setVisible(False)
        tp = theme_manager.palette
        for i, entry in enumerate(self._entries):
            icon_name = "translate" if entry.is_rough else "document"
            item = QListWidgetItem(
                svg_icon(icon_name, tp.accent, 14), f"  {entry.display_name}"
            )
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setToolTip(self._build_tooltip(entry))
            # 注意：QListWidgetItem 默认即含 ItemIsUserCheckable，浏览模式必须显式移除
            if self._select_mode:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self._list.addItem(item)

        # 恢复选中（主题刷新等场景），使用后清空
        if self._last_selected_name:
            for i, entry in enumerate(self._entries):
                if entry.display_name == self._last_selected_name:
                    self._list.setCurrentRow(i)
                    break
        self._last_selected_name = ""

    @staticmethod
    def _build_tooltip(entry: HistoryEntry) -> str:
        if entry.is_rough:
            parts = [f"粗译译文缓存: {entry.rough_cache.name if entry.rough_cache else ''}"]
            parts.append(f"源文件: {entry.source_name or '未知'}")
            if entry.source_path:
                parts.append("点击重新打开源文件（自动命中缓存免翻译）")
            else:
                parts.append("源文件路径未记录 — 请重新拖入同名 PDF 以命中缓存")
            return "\n".join(p for p in parts if p)
        parts = [f"双栏: {entry.dual_pdf.name}" if entry.dual_pdf else ""]
        if entry.mono_pdf:
            parts.append(f"单栏: {entry.mono_pdf.name}")
        if entry.csv_path:
            parts.append(f"词汇表: {entry.csv_path.name}")
        if entry.source_name:
            parts.append(f"源文件: {entry.source_name}")
        return "\n".join(p for p in parts if p)

    # ═══════════════════════════════════════════════════════════
    # 交互
    # ═══════════════════════════════════════════════════════════

    def _item_index(self, item: QListWidgetItem | None) -> int | None:
        if item is None:
            return None
        idx = item.data(Qt.ItemDataRole.UserRole)
        return int(idx) if idx is not None else None

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if self._select_mode:
            # 选择模式：勾选切换由 _CheckableList.mousePressEvent / Qt 处理
            # （itemChanged 已驱动按钮更新），这里仅兜底同步
            self._update_delete_btn()
            return

        idx = self._item_index(item)
        if idx is None or idx >= len(self._entries):
            return
        entry = self._entries[idx]
        if entry.is_rough:
            # 粗译条目：直接回开源文件（自动命中缓存免翻译）
            self.rough_selected.emit(entry.source_path)
            return
        self._open_entry(entry)

    def _on_item_state_changed(self, item: QListWidgetItem) -> None:
        """item checkState 变化（Qt 自动切换或整行点击切换）→ 同步删除按钮"""
        if self._select_mode and item.data(Qt.ItemDataRole.UserRole) is not None:
            self._update_delete_btn()

    def _open_entry(self, entry: HistoryEntry) -> None:
        dual = str(entry.dual_pdf) if entry.dual_pdf else ""
        mono = str(entry.mono_pdf) if entry.mono_pdf else ""
        self.result_selected.emit(dual, mono, entry.display_name, entry.source_path)

    def _on_context_menu(self, pos) -> None:
        """右键菜单：单条删除（任何模式下可用）"""
        item = self._list.itemAt(pos)
        if item is None:
            return
        idx = self._item_index(item)
        if idx is None or idx >= len(self._entries):
            return
        entry = self._entries[idx]
        self._list.setCurrentItem(item)

        menu = QMenu(self)
        act_open = menu.addAction("打开")
        menu.addSeparator()
        act_delete = menu.addAction("删除此记录")
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is act_open:
            self._open_entry(entry)
        elif chosen is act_delete:
            self._confirm_and_delete([entry])

    # ═══════════════════════════════════════════════════════════
    # 选择模式（批量删除）
    # ═══════════════════════════════════════════════════════════

    def _toggle_select_mode(self) -> None:
        self._select_mode = not self._select_mode
        self._list.set_select_mode(self._select_mode)
        self._select_btn.setText("完成" if self._select_mode else "选择")
        self._select_btn.setToolTip(
            "退出多选模式" if self._select_mode else "进入多选模式，可批量删除记录"
        )
        self._list.clearSelection()
        self._rebuild_list()  # 重建：checkbox 出现/消失
        self._update_delete_btn()

    def _checked_entries(self) -> list[HistoryEntry]:
        result = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                idx = self._item_index(item)
                if idx is not None and 0 <= idx < len(self._entries):
                    result.append(self._entries[idx])
        return result

    def _update_delete_btn(self) -> None:
        if self._select_mode:
            n = len(self._checked_entries())
            self._delete_btn.setEnabled(n > 0)
            self._delete_btn.setText(f"删除({n})" if n else "删除")
            self._delete_btn.setToolTip("删除勾选的记录")
        else:
            # 浏览模式：点击 item 即打开历史，删除走右键菜单或「选择」模式，避免误删
            self._delete_btn.setEnabled(False)
            self._delete_btn.setText("删除")
            self._delete_btn.setToolTip("点击「选择」进入多选模式后删除，或右键单条删除")

    def _on_delete_clicked(self) -> None:
        if self._select_mode:
            entries = self._checked_entries()
            if entries and self._confirm_and_delete(entries):
                self._toggle_select_mode()  # 删除完成后退出选择模式
        else:
            idx = self._item_index(self._list.currentItem())
            if idx is not None and 0 <= idx < len(self._entries):
                self._confirm_and_delete([self._entries[idx]])

    def _confirm_and_delete(self, entries: list[HistoryEntry]) -> bool:
        """删除一组历史记录对应的所有文件（带确认）；返回是否已执行删除。"""
        files = [
            p for e in entries for p in self._entry_files(e)
            if p is not None and p.exists()
        ]
        if not files:
            self.refresh()
            return True

        shown = "、".join(e.display_name for e in entries[:3])
        if len(entries) > 3:
            shown += f" 等 {len(entries)} 条"
        reply = QMessageBox.question(
            self,
            "删除历史记录",
            f"将删除 {len(entries)} 条记录的 {len(files)} 个文件：\n{shown}\n\n"
            "此操作不可恢复，确定删除？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False

        # 执行删除前先通知外部释放文件句柄（viewer 会话缓存持有 QPdfDocument，
        # Windows 下会锁定文件导致删除失败），信号同步直连，返回时已释放完毕。
        self.about_to_delete_files.emit(files)

        failed = 0
        for p in files:
            try:
                p.unlink(missing_ok=True)
            except OSError as exc:
                failed += 1
                logger.warning("Failed to delete %s: %s", p, exc)
        self.refresh()
        if failed:
            QMessageBox.warning(
                self, "部分删除失败",
                f"{failed} 个文件删除失败（可能被占用），其余已删除。",
            )
        return True

    @staticmethod
    def _entry_files(e: HistoryEntry) -> list[Path | None]:
        """一条记录对应的全部文件（dual / mono / csv / sidecar / 粗译缓存）"""
        return [e.dual_pdf, e.mono_pdf, e.csv_path, e.sidecar, e.rough_cache]

    def _on_selection_changed(self) -> None:
        """选中变化：刷新选中项图标；浏览模式下同步删除按钮可用性"""
        self._sync_item_icons()
        if not self._select_mode:
            self._update_delete_btn()

    def _sync_item_icons(self) -> None:
        """选中项图标切换为对比色，其余保持主题强调色（保证选中态可视）"""
        tp = theme_manager.palette
        sel = self._list.currentItem()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is sel:
                item.setIcon(svg_icon("document", _contrast_text(tp.accent), 14))
            else:
                item.setIcon(svg_icon("document", tp.accent, 14))


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
