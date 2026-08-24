"""
主窗口 — "Gilded Ink / Vellum" 双主题布局

┌──────────────────────────────────────────────────────────┐
│ ☰  FormTransPDF  [−] 适应 [+] ... 下载译文  [─][□][✕]   │ ← 标题栏
├────────┬─────────────────────────────────────────────────┤
│ 可收起 │  [文档标签页 ...]          [原文] [译文]          │
│ 侧边栏 │  ┌──────────────────────────────────────────┐   │
│ 280px  │  │           PDFViewer（多标签页）            │   │
│        │  └──────────────────────────────────────────┘   │
└────────┴─────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

try:
    from PySide6 import shiboken6  # pip 安装的 PySide6 通常在此
except ImportError:  # conda 安装的 PySide6 把 shiboken6 作为顶层包
    import shiboken6
from PySide6.QtCore import QEvent, Qt, QSettings, QVariantAnimation
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.signals import TranslationSignals
from src.core.translation.engine import TranslationEngine
from src.core.translation.records import (
    compute_source_fingerprint,
    load_rough_sidecar,
    rough_cache_path,
    rough_translations_from_sidecar,
    save_rough_sidecar,
)
from src.ui.base.icon_factory import IconHoverFilter, accent_icon, svg_icon
from src.ui.base.theme import ThemePalette, theme_manager, _contrast_text
from src.ui.dialogs.quick_translate import QuickTranslateDialog
from src.ui.panels.settings import SettingsPanel, SETTINGS_APP, SETTINGS_ORG
from src.ui.pdf.cover import COVER_TRANSLATED, COVER_TRANSPARENT
from src.ui.pdf.viewer import PDFViewer
from src.ui.windows.history_flow import _HistoryFlowMixin
from src.ui.windows.minimap_controller import _MinimapControllerMixin
from src.ui.windows.sidebar_behavior import _SidebarBehaviorMixin
from src.ui.windows.translation_flow import _EngineLoader, _TranslationFlowMixin
from src.ui.windows.window_chrome import _WindowChromeMixin, TitleBar
from src.ui.widgets.document_tab_bar import DocumentTab, DocumentTabBar, _adjust_index
from src.ui.widgets.drop_zone import DropZone
from src.ui.widgets.history_panel import HistoryPanel
from src.ui.widgets.minimap import MinimapPanel
from src.ui.widgets.switch import Switch

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 输出目录（打包兼容）
# ═══════════════════════════════════════════════════════════════

def _is_frozen() -> bool:
    """检测当前是否为打包（冻结）模式。

    兼容 PyInstaller (sys.frozen) 和 Nuitka (sys.__compiled__)。
    """
    if getattr(sys, "frozen", False):
        return True
    if hasattr(sys, "__compiled__"):
        return True
    # 兜底：如果 exe 与 src/ 目录同级，说明是打包模式
    try:
        exe_dir = Path(sys.executable).parent
        if (exe_dir / "src").is_dir():
            # 可执行文件旁边有 src/，大概率是打包后的目录
            return True
        # 如果 exe 在 build-nuitka/main.dist/ 下，也是打包模式
        if "build-nuitka" in exe_dir.parts or "main.dist" in exe_dir.parts:
            return True
    except Exception:
        pass
    return False


def _get_output_dir() -> Path:
    """获取翻译输出目录。

    - 开发模式：项目根目录下的 output/
    - 打包模式（PyInstaller/Nuitka）：用户主目录下的 FormTransPDF/output/
    """
    if _is_frozen():
        base = Path.home() / "FormTransPDF" / "output"
    else:
        # src/ui/windows/main_window.py → 上四级即项目根目录
        base = Path(__file__).resolve().parents[3] / "output"
    base.mkdir(parents=True, exist_ok=True)
    return base


# ═══════════════════════════════════════════════════════════════
# 主窗口
# ═══════════════════════════════════════════════════════════════

class MainWindow(
    _TranslationFlowMixin,
    _HistoryFlowMixin,
    _MinimapControllerMixin,
    _SidebarBehaviorMixin,
    _WindowChromeMixin,
    QMainWindow,
):
    """FormTransPDF 主窗口

    将各功能域 mixin 组合为完整主窗口：
    - _SidebarBehaviorMixin   侧边栏动画 / 缩放标签
    - _TranslationFlowMixin   翻译编排 / 即时翻译弹窗
    - _HistoryFlowMixin       历史记录回放
    - _MinimapControllerMixin 缩略图导航
    - _WindowChromeMixin      无边框窗口装饰（标题栏/缩放/圆角）
    """

    SIDEBAR_WIDTH = 280
    MIN_WINDOW_W = 900
    MIN_WINDOW_H = 600
    DEFAULT_W = 1400
    DEFAULT_H = 850

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FormTransPDF — PDF 论文翻译")
        self.setMinimumSize(self.MIN_WINDOW_W, self.MIN_WINDOW_H)
        self.resize(self.DEFAULT_W, self.DEFAULT_H)

        self._output_dir = _get_output_dir()

        # 划词自动弹出即时翻译开关（工具栏可切换，持久化保存）
        self._app_settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._auto_popup_quick = bool(
            self._app_settings.value("quick_translate_auto_popup", True, type=bool)
        )

        # 窗口图标已在 app.py 中通过 QApplication.setWindowIcon 统一设置，
        # Windows 任务栏图标需要 QApplication 级别的图标，此处不再重复设置。

        self._doc_tabs: list[DocumentTab] = []
        self._active_doc_index = -1
        # viewer 高度缓存（minimap 跟随重定位时避免重复重算面板）
        self._last_viewer_h: int | None = None
        self._sidebar_visible = True
        self._sidebar_anim: QVariantAnimation | None = None

        self._signals = TranslationSignals()
        self._engine = TranslationEngine()
        self._pending_translate = False  # 引擎就绪后自动续跑
        self._engine_thread: threading.Thread | None = None
        self._engine_worker: _EngineLoader | None = None
        self._minimap: MinimapPanel | None = None  # 在 _build_ui 中创建
        self._minimap_synced = False  # 滚动条信号是否已连接
        # 跨 viewer 实例的文档会话共享池（布局 mono↔dual 重建时迁移，避免重新提取/翻译）
        self._rough_sessions: dict[str, dict] = {}
        self._quick_translate_dialog: QuickTranslateDialog | None = None
        self._icon_hovers: list = []  # IconHoverFilter 列表（主题切换时刷新）
        self._theme_icon_filter: IconHoverFilter | None = None

        self._build_ui()
        self._setup_window_chrome()
        self._connect_signals()
        self._history.refresh()  # 启动时扫描已有记录
        self.setAcceptDrops(True)
        self._start_engine_load()

    def closeEvent(self, event) -> None:
        """窗口关闭前清理：断开 PDF 查看器的视口跟踪信号并卸载文档。

        避免窗口销毁过程中 QPdfView 触发 resize/滚动等信号时，
        _on_viewport_changed 访问到已被 Qt 删除的 QPdfDocument，
        抛出 RuntimeError: Internal C++ object already deleted。
        """
        if hasattr(self, "_viewer"):
            self._viewer.shutdown()
        super().closeEvent(event)

    # ═══════════════════════════════════════════════════════════
    # UI 构建
    # ═══════════════════════════════════════════════════════════

    @property
    def _tp(self) -> ThemePalette:
        return theme_manager.palette

    def _create_viewer(self):
        """按「输出模式」配置创建查看器：双语对照→双栏（DualRoughViewer），纯译文→单栏（PDFViewer）。

        输出模式是单一配置项，同时决定 BabelDoc 精确翻译的呈现形式与
        粗糙翻译的查看器布局 —— 用户无需在两种翻译模式下重复选择。
        """
        try:
            mode = str(self._settings.output_mode_combo.currentData() or "dual")
        except Exception:
            mode = str(self._app_settings.value("output_mode", "dual") or "dual")
        if mode == "dual":
            from src.ui.pdf.dual_viewer import DualRoughViewer
            return DualRoughViewer()
        return PDFViewer()

    # ═══════════════════════════════════════════════════════════
    # 文档标签页状态（属性路由到活动标签，兼容既有代码读写）
    # ═══════════════════════════════════════════════════════════

    def _active_doc_tab(self) -> DocumentTab | None:
        """返回活动文档标签；无标签时返回 None。"""
        if 0 <= self._active_doc_index < len(self._doc_tabs):
            return self._doc_tabs[self._active_doc_index]
        return None

    @property
    def _current_pdf(self) -> Path | None:
        tab = self._active_doc_tab()
        return tab.source_pdf if tab else None

    @_current_pdf.setter
    def _current_pdf(self, value: Path | None) -> None:
        tab = self._active_doc_tab()
        if tab:
            tab.source_pdf = value

    @property
    def _mono_path(self) -> Path | None:
        tab = self._active_doc_tab()
        return tab.mono_pdf if tab else None

    @_mono_path.setter
    def _mono_path(self, value: Path | None) -> None:
        tab = self._active_doc_tab()
        if tab:
            tab.mono_pdf = value

    @property
    def _dual_path(self) -> Path | None:
        tab = self._active_doc_tab()
        return tab.dual_pdf if tab else None

    @_dual_path.setter
    def _dual_path(self, value: Path | None) -> None:
        tab = self._active_doc_tab()
        if tab:
            tab.dual_pdf = value

    @property
    def _current_pdf_hash(self) -> str | None:
        tab = self._active_doc_tab()
        return tab.source_hash if tab else None

    @_current_pdf_hash.setter
    def _current_pdf_hash(self, value: str | None) -> None:
        tab = self._active_doc_tab()
        if tab:
            tab.source_hash = value or ""

    @property
    def _current_pdf_bytes_hash(self) -> str | None:
        tab = self._active_doc_tab()
        return tab.source_bytes_hash if tab else None

    @_current_pdf_bytes_hash.setter
    def _current_pdf_bytes_hash(self, value: str | None) -> None:
        tab = self._active_doc_tab()
        if tab:
            tab.source_bytes_hash = value or ""

    def _build_ui(self) -> None:
        tp = self._tp
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"background-color: {tp.canvas.name()};")

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏（全宽，兼作工具栏：品牌/操作 + 窗口控制）──
        self._toolbar = self._build_toolbar()
        root.addWidget(self._toolbar)

        # ── 内容行：侧边栏 + 主区域（内部布局不变）──────────
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self._sidebar = self._build_sidebar()
        content_layout.addWidget(self._sidebar)

        self._sidebar_sep = QFrame()
        self._sidebar_sep.setFrameShape(QFrame.Shape.VLine)
        self._sidebar_sep.setMinimumWidth(0)  # 允许动画收缩到 0
        self._sidebar_sep.setStyleSheet(f"color: {tp.divider.name()};")
        content_layout.addWidget(self._sidebar_sep)

        main_area = QWidget()
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._tab_row = self._build_tab_row()
        main_layout.addWidget(self._tab_row)

        self._viewer = self._create_viewer()
        self._viewer.text_selected.connect(self._on_text_selected)
        self._viewer.translate_requested.connect(self._on_viewer_translate_requested)
        # 监听 viewer 尺寸变化（侧边栏动画/窗口缩放），实时跟随重定位 minimap
        self._viewer.installEventFilter(self)
        main_layout.addWidget(self._viewer, stretch=1)
        self._main_layout = main_layout  # 供粗糙布局切换时重建 viewer

        # 缩略图导航（覆盖在 PDF 查看器右上角）
        self._create_minimap()
        content_layout.addWidget(main_area, stretch=1) # 主内容区域

        root.addWidget(content, stretch=1)

    def _create_minimap(self) -> None:
        """为当前 viewer 创建缩略图导航面板（viewer 重建后必须重新创建）。"""
        self._minimap = MinimapPanel(self._viewer)  # 缩略图导航
        self._minimap.page_clicked.connect(self._on_minimap_page_clicked)
        self._minimap.viewport_dragged.connect(self._on_minimap_dragged)

    def _destroy_minimap(self) -> None:
        """销毁当前 minimap（viewer 重建前调用，避免残留指向已删对象的引用）。"""
        mm = self._minimap
        was_synced = self._minimap_synced
        self._minimap = None
        self._minimap_synced = False
        # 断开旧 viewer scrollbar 的视口同步连接：重建窗口期其 valueChanged
        # 仍会触发 _update_minimap_viewport（minimap 已置 None → 已防御，但断开更干净）
        if was_synced:
            try:
                if shiboken6.isValid(self._viewer):
                    bar = self._viewer.verticalScrollBar()
                    if shiboken6.isValid(bar):
                        bar.valueChanged.disconnect(self._update_minimap_viewport)
            except (RuntimeError, TypeError, AttributeError):
                pass
        if mm is None or not shiboken6.isValid(mm):
            return
        try:
            mm.page_clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            mm.viewport_dragged.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            mm.deleteLater()
        except RuntimeError:
            pass  # C++ 对象已随旧 viewer 销毁

    # ── 侧边栏 ───────────────────────────────────────────────

    def _build_sidebar(self) -> QWidget:
        tp = self._tp
        sidebar = QWidget()
        # 不使用 setFixedWidth —— 它会同时锁定 min/max 宽度，后续动画无法收缩
        sidebar.setMinimumWidth(0)
        sidebar.setMaximumWidth(self.SIDEBAR_WIDTH)
        sidebar.resize(self.SIDEBAR_WIDTH, sidebar.height())
        sidebar.setObjectName("sidebar")
        sidebar.setStyleSheet(f"QWidget#sidebar {{ background-color: {tp.background.name()}; }}")

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        brand = QLabel("FormTransPDF")
        brand.setObjectName("brandLabel")
        brand.setStyleSheet(f"background: transparent;")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(brand)

        # self._drop_zone = DropZone()
        # self._drop_zone.setMinimumHeight(72)
        # layout.addWidget(self._drop_zone)

        self._settings = SettingsPanel()
        layout.addWidget(self._settings)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # 设置区与历史区之间的分隔线（theme 切换时由 _refresh_theme 更新颜色）
        self._sidebar_divider = QFrame()
        self._sidebar_divider.setObjectName("sidebarDivider")
        self._sidebar_divider.setFrameShape(QFrame.Shape.HLine)
        self._sidebar_divider.setStyleSheet(
            f"QFrame#sidebarDivider {{ background: {tp.divider.name()};"
            "border: none; min-height: 1px; max-height: 1px; }"
        )
        layout.addWidget(self._sidebar_divider)

        # 历史记录
        self._history = HistoryPanel(self._output_dir)
        layout.addWidget(self._history, stretch=1)

        return sidebar

    # ── 顶栏 ─────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        tp = self._tp
        bar = TitleBar(self)  # 标题栏：支持拖拽移动 / 双击最大化还原
        bar.setFixedHeight(40)
        self._toolbar_widget = bar  # for theme refresh

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)

        # 收起按钮
        self._toggle_btn = self._make_icon_btn("☰", "收起 / 展开侧边栏", width=38)
        self._toggle_btn.clicked.connect(self._toggle_sidebar)
        layout.addWidget(self._toggle_btn)

        brand = QLabel("FormTransPDF")
        brand.setStyleSheet(
            "font-family: 'Cormorant Garamond', 'Microsoft YaHei', serif;"
            f"font-size: 16pt; font-weight: 600; color: {tp.accent.name()};"
            "background: transparent; padding: 0 8px;"
        )
        layout.addWidget(brand)
        layout.addStretch()

        # 缩放
        self._zoom_label = QLabel("适应")
        self._zoom_label.setFixedWidth(44)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setStyleSheet(
            f"color: {tp.text_secondary.name()}; font-size: 10pt; background: transparent;"
        ) # 前景色根据明暗主题
        layout.addWidget(self._zoom_label)

        self._zoom_btns: list[QPushButton] = []
        for text, tip, slot in [
            ("−", "缩小 (Ctrl+滚轮)", lambda: self._viewer.zoom_out() or self._update_zoom_label()),
            ("+", "放大 (Ctrl+滚轮)", lambda: self._viewer.zoom_in() or self._update_zoom_label()),
        ]:
            btn = self._make_icon_btn(text, tip)
            btn.clicked.connect(slot)
            self._zoom_btns.append(btn)
            layout.addWidget(btn)

        # 重置缩放：SVG 图标（reload）
        reset_btn, _ = self._make_tool_icon_btn("reload", "重置缩放", width=32)
        reset_btn.clicked.connect(lambda: self._viewer.zoom_reset() or self._update_zoom_label())
        self._zoom_btns.append(reset_btn)
        layout.addWidget(reset_btn)

        # 缩略图切换
        self._minimap_btn, _ = self._make_tool_icon_btn("thumbnail", "切换缩略图导航", width=38)
        self._minimap_btn.clicked.connect(lambda: self._minimap and self._minimap.toggle()) # 点击时切换缩略图导航
        layout.addWidget(self._minimap_btn)

        self._translate_quick_btn, _ = self._make_tool_icon_btn("translate", "即时翻译选中文本", width=38)
        self._translate_quick_btn.clicked.connect(self._open_quick_translate)
        layout.addWidget(self._translate_quick_btn)

        # 划词自动弹出即时翻译开关（Switch 组件；关闭后划词仅高亮不弹窗）
        self._auto_translate_switch = Switch()
        self._auto_translate_switch.setToolTip(
            "划词时自动弹出即时翻译（开）" if self._auto_popup_quick else "划词时自动弹出即时翻译（关）"
        )
        self._auto_translate_switch.setChecked(self._auto_popup_quick)
        self._auto_translate_switch.toggled.connect(self._on_auto_translate_toggled)
        layout.addWidget(self._auto_translate_switch)

        # 分隔
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {tp.divider.name()}; background: transparent;")
        layout.addWidget(sep)

        # 主题切换（暗色主题显示 sun，亮色主题显示 moon）
        self._theme_btn, self._theme_icon_filter = self._make_tool_icon_btn(
            "sun" if theme_manager.is_dark else "moon", "切换亮色/暗色主题", width=40
        )
        self._theme_btn.clicked.connect(self._on_toggle_theme)
        layout.addWidget(self._theme_btn)

        # 下载
        self._download_btn = QPushButton(" 下载译文")
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.setEnabled(False)
        self._download_btn.setToolTip("将翻译结果保存到指定位置")
        _dl_hover = IconHoverFilter(self._download_btn, "download", size=16)
        self._icon_hovers.append(_dl_hover)
        self._download_btn.clicked.connect(self._on_download)
        layout.addWidget(self._download_btn)

        # 窗口控制按钮（最小化 / 最大化·还原 / 关闭）
        layout.addWidget(self._build_window_controls())

        self._apply_toolbar_styles()
        return bar

    def _make_icon_btn(self, text: str, tooltip: str, width: int = 32) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(width, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        return btn

    def _make_tool_icon_btn(self, icon_name: str, tooltip: str, width: int = 32):
        """创建带主题色 SVG 图标的工具栏按钮（hover 自动反色切换）。

        返回 (按钮, IconHoverFilter)。
        """
        btn = QPushButton()
        btn.setFixedSize(width, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        hover = IconHoverFilter(btn, icon_name, size=18)
        self._icon_hovers.append(hover)
        return btn, hover

    def _apply_toolbar_styles(self) -> None:
        """应用/刷新顶栏样式（主题切换时调用）"""
        tp = self._tp
        bar_style = (
            f"background-color: {tp.background.name()};"
            f"border-bottom: 1px solid {tp.divider.name()};"
        )
        if hasattr(self, "_toolbar_widget"):
            self._toolbar_widget.setStyleSheet(bar_style)

        icon_btn_style = (
            f"QPushButton {{"
            f"  background-color: {tp.surface.name()};"
            f"  color: {tp.accent.name()};"
            f"  border: 1px solid {tp.divider.name()};"
            f"  border-radius: 4px; font-size: 14pt;"
            f"  padding: 0;"                     # ← 去掉默认内边距
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {tp.accent.name()};"
            f"  color: {_contrast_text(tp.accent).name()};"
            f"  border-color: {tp.accent.name()};"
            f"}}"
            f"QPushButton:pressed {{"
            f"  background-color: {tp.accent_press.name()};"
            f"  color: {_contrast_text(tp.accent_press).name()};"
            f"}}"
        )
        for btn in [self._toggle_btn, self._theme_btn, self._minimap_btn, self._translate_quick_btn]:
            btn.setStyleSheet(icon_btn_style)
        if hasattr(self, "_zoom_btns"):
            for btn in self._zoom_btns:
                btn.setStyleSheet(icon_btn_style)

        dl_style = (
            f"QPushButton {{ background-color: {tp.accent_muted.name()};"
            f"color: {tp.accent.name()};"
            f"border: 1px solid {tp.accent.name()}; border-radius: 4px;"
            f"padding: 4px 12px; font-size: 10pt; font-weight: 500; }}"
            f"QPushButton:hover {{ background-color: {tp.accent.name()};"
            f"color: {_contrast_text(tp.accent).name()}; }}"
            f"QPushButton:disabled {{ background-color: transparent;"
            f"color: {tp.text_disabled.name()};"
            f"border-color: {tp.text_disabled.name()}; }}"
        )
        self._download_btn.setStyleSheet(dl_style)

        self._zoom_label.setStyleSheet(
            f"color: {tp.text_secondary.name()}; font-size: 10pt; background: transparent;"
        )

        # 窗口控制按钮（SVG 图标 / hover 背景）随主题刷新
        if hasattr(self, "_win_controls"):
            self._win_controls.refresh_theme()

    # ── 标签行（文档标签页 + 原文/译文切换，单行合并）────────

    def _build_tab_row(self) -> QWidget:
        row = QWidget()
        row.setFixedHeight(DocumentTabBar.BAR_HEIGHT + 4)
        self._tab_row_widget = row

        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 2, 8, 0)
        layout.setSpacing(6)

        # 多文档标签页（浏览器式，可关闭）
        self._doc_tab_bar = DocumentTabBar()
        self._doc_tab_bar.tab_activated.connect(self._on_doc_tab_activated)
        self._doc_tab_bar.tab_close_requested.connect(self._on_doc_tab_close_requested)
        self._doc_tab_bar.tabs_reordered.connect(self._on_doc_tabs_reordered)
        layout.addWidget(self._doc_tab_bar, stretch=1)

        # 原文/译文切换（作用于当前文档标签页，各标签独立记忆）
        self._view_source_btn = self._make_view_toggle("原文", self._on_view_source)
        self._view_result_btn = self._make_view_toggle("译文", self._on_view_result)
        self._view_source_btn.setToolTip("查看原始文档")
        self._view_result_btn.setToolTip("查看翻译结果")
        layout.addWidget(self._view_source_btn)
        layout.addWidget(self._view_result_btn)

        # 粗糙翻译：单按钮三态（原文 → 点击=开始翻译/复用内存 → 译文；再点=原文）
        self._rough_btn = QPushButton(" 粗糙翻译")
        self._rough_btn.setFixedHeight(24)
        self._rough_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rough_btn.setCheckable(True)
        self._rough_btn.setEnabled(False)
        self._rough_btn.setToolTip(
            "粗糙翻译：点击切换到译文；再点切回原文"
        )
        self._rough_btn.toggled.connect(self._on_rough_toggled)
        layout.addWidget(self._rough_btn)

        self._sync_view_toggle_ui()
        return row

    def _make_view_toggle(self, text: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(24)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setCheckable(True)
        btn.clicked.connect(slot)
        return btn

    def _on_view_source(self) -> None:
        self._set_active_view("source")

    def _on_view_result(self) -> None:
        self._set_active_view("result")

    def _set_active_view(self, view: str) -> None:
        """设置当前文档标签页的视图（原文/译文）并加载。"""
        tab = self._active_doc_tab()
        if not tab:
            return
        tab.view = view
        self._apply_doc_view(tab)

    def _result_target(self, tab: DocumentTab) -> Path | None:
        """按输出模式返回应展示的译文文件（dual 优先 / mono）。"""
        mode = "dual"
        try:
            if tab.source_pdf:
                mode = self._settings.build_task(str(tab.source_pdf)).output_mode
        except Exception:
            pass
        if mode == "mono":
            return tab.mono_pdf or tab.dual_pdf
        return tab.dual_pdf or tab.mono_pdf

    def _apply_doc_view(self, tab: DocumentTab) -> None:
        """按标签页的视图状态把对应文档加载到 viewer。"""
        if tab.view == "result":
            target = self._result_target(tab)
        else:
            target = tab.source_pdf if tab.has_source else None
        if target and target.exists():
            self._viewer.load_pdf(str(target))
        else:
            self._viewer.clear()
        self._setup_minimap()
        self._update_zoom_label()
        self._sync_view_toggle_ui()
        self._sync_download_btn()
        self._sync_rough_ui()

    def _sync_view_toggle_ui(self) -> None:
        """刷新原文/译文按钮的可用性与选中态。"""
        tab = self._active_doc_tab()
        has_source = bool(tab and tab.has_source and tab.source_pdf and tab.source_pdf.exists())
        result = self._result_target(tab) if tab else None
        has_result = bool(result and result.exists())
        self._view_source_btn.setEnabled(has_source)
        self._view_result_btn.setEnabled(has_result)
        view = tab.view if tab else "source"
        self._view_source_btn.setChecked(view == "source")
        self._view_result_btn.setChecked(view == "result")
        # 关键：选中态变化后必须立即重刷样式，否则按钮视觉不随状态更新（无保持态）
        self._apply_tab_row_styles()

    def _sync_download_btn(self) -> None:
        tab = self._active_doc_tab()
        target = self._result_target(tab) if tab else None
        has_babel = bool(target and target.exists())
        has_rough = bool(self._viewer.has_rough_translations())
        self._download_btn.setEnabled(has_babel or has_rough)

    def _apply_tab_row_styles(self) -> None:
        tp = self._tp
        if not hasattr(self, "_tab_row_widget"):
            return
        self._tab_row_widget.setStyleSheet(f"background-color: {tp.background.name()};")
        if not hasattr(self, "_view_source_btn"):
            return
        for btn, icon_name in (
            (self._view_source_btn, "document"),
            (self._view_result_btn, "web"),
            (self._rough_btn, "translate"),
        ):
            if btn.isChecked():
                icon_color = _contrast_text(tp.accent)
                style = (
                    f"QPushButton {{ background-color: {tp.accent.name()};"
                    f"color: {icon_color.name()};"
                    f"border: 1px solid {tp.accent.name()}; border-radius: 4px;"
                    f"font-size: 9pt; padding: 0 10px; }}"
                )
            else:
                icon_color = tp.accent if btn.isEnabled() else tp.text_disabled
                style = (
                    f"QPushButton {{ background-color: transparent;"
                    f"color: {tp.text_secondary.name()};"
                    f"border: 1px solid {tp.divider.name()}; border-radius: 4px;"
                    f"font-size: 9pt; padding: 0 10px; }}"
                    f"QPushButton:hover {{ background-color: {tp.surface_hover.name()};"
                    f"color: {tp.text_primary.name()}; }}"
                    f"QPushButton:disabled {{ color: {tp.text_disabled.name()};"
                    f"border-color: {tp.divider.name()}; }}"
                )
            btn.setIcon(svg_icon(icon_name, icon_color, 14))
            btn.setStyleSheet(style)

    # ═══════════════════════════════════════════════════════════
    # 文档标签页管理
    # ═══════════════════════════════════════════════════════════

    def _on_doc_tab_activated(self, index: int) -> None:
        if index == self._active_doc_index:
            return
        self._activate_doc_tab(index)

    def _on_doc_tab_close_requested(self, index: int) -> None:
        self._close_doc_tab(index)

    def _on_doc_tabs_reordered(self, from_index: int, to_index: int) -> None:
        """标签拖拽重排：同步文档数据列表与活动索引。"""
        if from_index == to_index:
            return
        if not (0 <= from_index < len(self._doc_tabs)) or not (0 <= to_index < len(self._doc_tabs)):
            return
        tab = self._doc_tabs.pop(from_index)
        self._doc_tabs.insert(to_index, tab)
        self._active_doc_index = _adjust_index(self._active_doc_index, from_index, to_index)

    def _activate_doc_tab(self, index: int) -> None:
        """激活指定文档标签页：切换状态并把其视图加载到 viewer。"""
        if not (0 <= index < len(self._doc_tabs)):
            return
        self._active_doc_index = index
        self._doc_tab_bar.set_active(index)
        tab = self._doc_tabs[index]
        self._apply_doc_view(tab)
        self._settings.set_pdf_loaded(
            str(tab.source_pdf) if tab.source_pdf else "",
            loaded=tab.has_source,  # 历史结果标签（无源文件）不可翻译
        )
        self._settings.set_status(f"文档: {tab.title}")
        self._sync_rough_ui()
        self._maybe_hint_resume_rough()

    def _maybe_hint_resume_rough(self) -> None:
        """切回存在部分粗译的文档时提示一键续译（不自动消耗 API）。"""
        try:
            tab = self._active_doc_tab()
            if not tab or not tab.has_source:
                return
            viewer = self._viewer
            if viewer.rough_is_running() or not viewer.has_rough_segments():
                return
            done_n, total_n = viewer.rough_progress_counts()
            if 0 < done_n < total_n:
                self._settings.set_status(
                    f"点击继续翻译（已有 {done_n}/{total_n} 段）"
                )
        except Exception:
            logger.debug("rough resume hint failed", exc_info=True)

    def _close_doc_tab(self, index: int) -> None:
        """关闭文档标签页；关闭最后一个后显示空状态。"""
        if not (0 <= index < len(self._doc_tabs)):
            return
        self._doc_tabs.pop(index)
        self._doc_tab_bar.remove_tab(index)

        if not self._doc_tabs:
            self._active_doc_index = -1
            self._doc_tab_bar.set_active(-1)
            self._show_empty_state()
            return

        # 相邻激活：关闭活动标签时优先右侧、其次左侧
        active = self._active_doc_index
        if active == index:
            active = index if index < len(self._doc_tabs) else len(self._doc_tabs) - 1
        elif active > index:
            active -= 1
        self._active_doc_index = active
        self._doc_tab_bar.set_active(active)
        tab = self._doc_tabs[active]
        self._apply_doc_view(tab)
        self._settings.set_pdf_loaded(
            str(tab.source_pdf) if tab.source_pdf else "",
            loaded=tab.has_source,
        )

    def _show_empty_state(self) -> None:
        """无打开文档：显示空状态占位。"""
        self._viewer.clear()
        self._download_btn.setEnabled(False)
        self._view_source_btn.setEnabled(False)
        self._view_result_btn.setEnabled(False)
        self._settings.set_pdf_loaded("", loaded=False)
        self._settings.set_status("就绪 — 请载入 PDF")
        self._sync_rough_ui()

    # ═══════════════════════════════════════════════════════════
    # 粗糙翻译（单按钮三态：原文 → 译文(开始/复用) → 原文）
    # ═══════════════════════════════════════════════════════════

    def _on_rough_toggled(self, checked: bool) -> None:
        """粗糙翻译按钮切换。

        - checked（切到译文）：首次点击 → 启动翻译；已有内存译文 → 直接复用呈现（不重译）；
        - unchecked（切回原文）：若后台仍在翻译则**真正停止**（已完成段保留，
          再次点击可续译）；仅切视图不停译的操作不存在 —— 运行中按钮即「停止」。
        """
        viewer = self._viewer
        tab = self._active_doc_tab()

        if not checked:
            if viewer.rough_is_running():
                viewer.cancel_rough_translation()
                done_n, total_n = viewer.rough_progress_counts()
                self._settings.set_status(
                    f"已停止粗糙翻译（已有 {done_n}/{total_n} 段）— 再次点击可续译剩余段"
                )
            viewer.set_cover_mode(COVER_TRANSPARENT)
            self._sync_rough_ui()
            return

        if not tab or not (tab.has_source and tab.source_pdf):
            self._settings.set_status("请先加载 PDF 文件", is_error=True)
            self._rough_btn.setChecked(False)
            return
        if not viewer.text_layer_done:
            self._settings.set_status("文本层提取中，请稍候再点「粗糙翻译」…")
            self._rough_btn.setChecked(False)
            return
        # (b) 防护：粗糙翻译永远基于「原文」文本层 —— 若正在查看译文视图，先切回原文
        if tab.view != "source":
            self._set_active_view("source")
            self._settings.set_status("粗糙翻译基于原文执行 — 已切换回原文视图")

        if viewer.rough_is_running():
            # 翻译仍在后台进行：直接呈现译文（不打断任务，未译段继续累积）
            viewer.set_cover_mode(COVER_TRANSLATED)
            self._settings.set_status("译文呈现中…（后台翻译仍在进行）")
        else:
            # 未在运行（含「翻译被布局切换暂停」的情况）：
            # start_rough_translation 内部自动分派 ——
            #   全部已译 → 复用内存译文（不重新翻译）；
            #   部分已译 → 续传剩余未译段（暂停后点按钮即恢复翻译）；
            #   无译文   → 全量启动。
            profile = self._settings.translation_profile()
            ok = viewer.start_rough_translation(
                profile,
                str(profile.get("lang_in") or "en"),
                str(profile.get("lang_out") or "zh"),
            )
            if not ok:
                self._settings.set_status(
                    "没有可翻译的文本层（文档可能为扫描件）", is_error=True
                )
                self._rough_btn.setChecked(False)
        self._sync_rough_ui()

    def _rebuild_viewer(self) -> None:
        """用新布局的 viewer 替换当前 viewer（断开信号/卸载/重建/重载当前文档）。

        minimap 是旧 viewer 的子对象，会随旧 viewer 一并销毁 —— 必须
        先置空引用再重建，否则残留的 self._minimap 指向已删除的 C++ 对象，
        resize/事件过滤路径访问它即抛 RuntimeError（全屏/布局切换崩溃根因）。
        """
        self._destroy_minimap()

        old = self._viewer
        # 迁移旧 viewer 的文档会话缓存到共享池（shutdown 会清空其内部缓存；
        # 翻译结果随会话保留，新 viewer 复用后不再重新提取/翻译已完成段落）
        try:
            for k, v in old.export_sessions().items():
                self._rough_sessions[k] = v
        except Exception:
            logger.debug("Failed to export sessions", exc_info=True)
        # 会话池上限：超出按插入序淘汰最旧（销毁 QPdfDocument 归还句柄），
        # 防止长期使用中池无界增长；被淘汰文档重开时由粗译 sidecar 兜底恢复译文
        while len(self._rough_sessions) > 8:
            key = next(iter(self._rough_sessions))
            sess = self._rough_sessions.pop(key)
            doc = sess.get("doc")
            if doc is not None:
                try:
                    if shiboken6.isValid(doc):
                        shiboken6.delete(doc)
                except Exception:
                    logger.debug("Evict pooled session failed: %s", key, exc_info=True)
        for sig in (
            old.text_selected,
            old.translate_requested,
            old.rough_status,
            old.rough_ready,
            old.rough_progress,
            old.rough_stats,
            old.text_layer_ready,
        ):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        try:
            old.removeEventFilter(self)
        except Exception:
            pass
        old.shutdown()

        self._viewer = self._create_viewer()
        # 注入共享会话池：同一 PDF 的 doc/spans/segments/译文直接复用
        self._viewer.inject_sessions(self._rough_sessions)
        self._viewer.text_selected.connect(self._on_text_selected)
        self._viewer.translate_requested.connect(self._on_viewer_translate_requested)
        self._viewer.rough_status.connect(self._on_rough_status)
        self._viewer.rough_ready.connect(self._on_rough_ready)
        self._viewer.rough_progress.connect(self._on_rough_progress)
        self._viewer.rough_stats.connect(self._on_rough_stats)
        self._viewer.text_layer_ready.connect(self._on_text_layer_ready)
        self._viewer.installEventFilter(self)
        self._main_layout.replaceWidget(old, self._viewer)
        old.deleteLater()

        # 新 viewer 必须配套新 minimap（旧 minimap 已随旧 viewer 销毁）
        self._create_minimap()

        # 重载当前文档视图
        tab = self._active_doc_tab()
        if tab is not None:
            self._apply_doc_view(tab)
        else:
            self._show_empty_state()

    def _on_rough_status(self, message: str) -> None:
        self._settings.set_status(message)

    def _on_rough_progress(self, done: int, total: int) -> None:
        """粗糙翻译进度：驱动侧边栏进度条（done 含成功 + 最终失败段）。"""
        if total <= 0:
            self._progress.hide()
            return
        self._progress.setRange(0, total)
        self._progress.setValue(min(done, total))
        self._progress.setVisible(True)

    def _on_rough_stats(self, ok: int, failed: int) -> None:
        """粗糙翻译结束统计：收起进度条、给出成败明细并落盘缓存。

        失败段没有写入译文缓存 → 再次点击「粗译」会自动只补译这些段（续传）。
        """
        self._progress.hide()
        if failed > 0:
            self._settings.set_status(
                f"粗糙翻译完成：{ok} 段成功，{failed} 段失败 — 再次点击「粗译」可重试失败段",
                is_error=True,
            )
        else:
            self._settings.set_status(f"粗糙翻译完成：{ok} 段全部成功")
        self._save_rough_sidecar()

    # ── 粗译持久化（output/rough_cache/<指纹>.rough.json）────

    def _save_rough_sidecar(self) -> None:
        """把当前文档的粗译结果写入 sidecar（失败静默，不影响主流程）。"""
        try:
            tab = self._active_doc_tab()
            if not tab or not tab.source_pdf:
                return
            result = self._viewer.collect_rough_result()
            if not result:
                return
            profile = self._settings.translation_profile()
            path = rough_cache_path(
                self._output_dir, tab.source_hash or "", tab.source_bytes_hash or ""
            )
            save_rough_sidecar(
                path,
                source_hash=tab.source_hash or "",
                bytes_hash=tab.source_bytes_hash or "",
                lang_in=str(profile.get("lang_in", "en")),
                lang_out=str(profile.get("lang_out", "zh")),
                translator=str(profile.get("translator", "")),
                model=str(profile.get("model", "")),
                pages=result,
                source_name=tab.source_pdf.name,
                source_path=str(tab.source_pdf),
            )
            logger.info("粗译已缓存: %s", path.name)
            self._history.refresh()  # 历史面板立即出现「粗译」记录
        except Exception:
            logger.debug("保存粗译缓存失败", exc_info=True)

    def _load_rough_sidecar(self) -> int:
        """文本层就绪后尝试命中历史粗译；返回采纳的段数。"""
        try:
            tab = self._active_doc_tab()
            if not tab or not tab.source_pdf:
                return 0
            if not (tab.source_hash or tab.source_bytes_hash):
                return 0
            path = rough_cache_path(
                self._output_dir, tab.source_hash or "", tab.source_bytes_hash or ""
            )
            data = load_rough_sidecar(path)
            if not data:
                return 0
            translations = rough_translations_from_sidecar(data)
            if not translations:
                return 0
            applied = self._viewer.apply_rough_translations(translations)
            if applied:
                logger.info("命中历史粗译: %d 段", applied)
            return applied
        except Exception:
            logger.debug("载入粗译缓存失败", exc_info=True)
            return 0

    def _on_history_rough_selected(self, source_path: str) -> None:
        """点击历史「粗译」记录：重新打开源文件，译文按指纹自动命中。"""
        p = Path(source_path) if source_path else None
        if not p or not p.exists():
            QMessageBox.information(
                self, "粗译记录",
                "源文件不存在或已被移动，无法打开。\n"
                "把同名 PDF 重新拖入即可 — 译文按内容指纹自动命中缓存。",
            )
            return
        self._load_pdf(str(p))

    def _on_rough_ready(self) -> None:
        self._sync_rough_ui()

    def _on_text_layer_ready(self) -> None:
        """文本层提取完成：同步按钮态、尝试命中历史粗译并提示。"""
        self._sync_rough_ui()
        applied = self._load_rough_sidecar()
        if applied > 0:
            self._settings.set_status(
                f"文本层就绪 — 已载入历史粗译 {applied} 段，点击「粗译」查看"
            )
        else:
            self._settings.set_status("文本层就绪 — 可以开始粗糙翻译")

    def _sync_rough_ui(self) -> None:
        """按 viewer 当前状态同步粗糙翻译按钮（标签行）。"""
        try:
            viewer = self._viewer
            has_doc = self._current_pdf is not None or viewer.document is not None
            has_layer = viewer.has_rough_segments()
            translated = viewer.cover_mode == COVER_TRANSLATED
            running = viewer.rough_is_running()

            self._rough_btn.setEnabled(has_doc and has_layer)
            if self._rough_btn.isChecked() != translated:
                self._rough_btn.blockSignals(True)
                self._rough_btn.setChecked(translated)
                self._rough_btn.blockSignals(False)
            # 运行中按钮即「停止」：文案与提示随状态切换
            if running:
                self._rough_btn.setText("停止粗译")
                self._rough_btn.setToolTip(
                    "正在后台翻译 — 点击停止（已完成段保留，可续译）"
                )
            else:
                self._rough_btn.setText("粗译译文" if translated else "粗译原文")
                self._rough_btn.setToolTip(
                    "粗糙翻译：点击切换到译文；再点切回原文"
                )
            # 翻译不在后台进行时收起进度条（切标签/取消/完成后均会走到这里）
            if not running:
                self._progress.hide()
        except Exception:
            import traceback
            traceback.print_exc()

    # ═══════════════════════════════════════════════════════════
    # 主题切换
    # ═══════════════════════════════════════════════════════════

    def _on_toggle_theme(self) -> None:
        app = QApplication.instance()
        toggle = getattr(app, "toggle_theme", None)
        if toggle is not None:
            toggle()
            self._refresh_theme()

    def _refresh_theme(self) -> None:
        """刷新所有内联样式"""
        tp = self._tp
        self.centralWidget().setStyleSheet(f"background-color: {tp.canvas.name()};")
        self._sidebar.setStyleSheet(f"QWidget#sidebar {{ background-color: {tp.background.name()}; }}")
        self._sidebar_sep.setStyleSheet(f"color: {tp.divider.name()};")
        if hasattr(self, "_sidebar_divider"):
            self._sidebar_divider.setStyleSheet(
                f"QFrame#sidebarDivider {{ background: {tp.divider.name()};"
                "border: none; min-height: 1px; max-height: 1px; }"
            )
        self._apply_toolbar_styles()
        self._apply_tab_row_styles()

        # 更新主题按钮图标与所有主题色 SVG 图标
        self._refresh_icons()

        if self._quick_translate_dialog:
            self._quick_translate_dialog.refresh_theme()

        # 更新 PDF 容器背景
        self._viewer.refresh_theme()

    def _refresh_icons(self) -> None:
        """主题切换后按新主题重建所有 SVG 图标颜色"""
        if hasattr(self, "_doc_tab_bar"):
            self._doc_tab_bar.refresh_theme()
        if self._theme_icon_filter is not None:
            self._theme_icon_filter.set_icon_name("sun" if theme_manager.is_dark else "moon")
        for hover in self._icon_hovers:
            hover.refresh_theme()
        if hasattr(self, "_auto_translate_switch"):
            self._auto_translate_switch.refresh_theme()
        if hasattr(self, "_settings"):
            self._settings.refresh_theme()
        if hasattr(self, "_history"):
            self._history.refresh_theme()

    # ═══════════════════════════════════════════════════════════
    # 信号
    # ═══════════════════════════════════════════════════════════

    def _connect_signals(self) -> None:
        # self._drop_zone.pdf_dropped.connect(self._on_pdf_received)
        self._settings.select_btn.clicked.connect(self._on_select_file)
        self._settings.translate_btn.clicked.connect(self._on_translate)
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_finished)
        self._signals.error_occurred.connect(self._on_error)
        self._history.result_selected.connect(self._on_history_selected)
        # 历史中的「粗译」记录：重新打开源文件（文本层就绪后自动命中缓存）
        self._history.rough_selected.connect(self._on_history_rough_selected)
        # 历史删除前释放 viewer 句柄 + 关闭引用被删文件的标签页（Windows 文件锁）
        self._history.about_to_delete_files.connect(self._on_history_delete_files)
        # 输出模式变更（单/双栏）：重建 viewer —— BabelDoc 结果与粗糙翻译布局统一生效
        self._settings.output_mode_combo.currentIndexChanged.connect(
            self._on_output_mode_changed
        )
        # ── 粗糙翻译 ──
        self._viewer.rough_status.connect(self._on_rough_status)
        self._viewer.rough_ready.connect(self._on_rough_ready)
        self._viewer.rough_progress.connect(self._on_rough_progress)
        self._viewer.rough_stats.connect(self._on_rough_stats)
        self._viewer.text_layer_ready.connect(self._on_text_layer_ready)

    # ═══════════════════════════════════════════════════════════
    # PDF 加载
    # ═══════════════════════════════════════════════════════════

    def _on_pdf_received(self, path: str) -> None:
        self._load_pdf(path)

    def _on_select_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF 文件", "", "PDF 文件 (*.pdf)")
        if path:
            self._load_pdf(path)

    def _load_pdf(self, path: str) -> None:
        pdf_path = Path(path)
        if not pdf_path.exists():
            QMessageBox.warning(self, "文件不存在", f"找不到文件:\n{path}")
            return

        # 去重：同一文件已作为标签打开 → 切换到已有标签页
        for i, tab in enumerate(self._doc_tabs):
            if tab.source_pdf and tab.source_pdf.resolve() == pdf_path.resolve():
                self._activate_doc_tab(i)
                return

        # ── 计算源文件指纹（内容+字节），用于「已翻译过则复用」检测 ──
        # 论文 PDF 通常很小，同步计算可接受；失败则跳过复用检测。
        content_hash: str | None = None
        bytes_hash: str | None = None
        try:
            content_hash, bytes_hash = compute_source_fingerprint(pdf_path)
        except Exception:
            logger.debug("Failed to fingerprint source pdf", exc_info=True)

        # ── 新建文档标签页并激活（_activate_doc_tab 内部加载到 viewer）──
        tab = DocumentTab(
            title=pdf_path.stem,
            source_pdf=pdf_path,
            view="source",
            has_source=True,
            source_hash=content_hash or "",
            source_bytes_hash=bytes_hash or "",
        )
        self._doc_tabs.append(tab)
        self._doc_tab_bar.add_tab(tab.title)
        try:
            self._activate_doc_tab(len(self._doc_tabs) - 1)
        except Exception as exc:
            QMessageBox.critical(self, "PDF 加载失败", str(exc))
            return

        self._download_btn.setEnabled(False)

        # 若该文件已翻译过，给出可复用提示（真正复用发生在点击「翻译」时）
        if content_hash or bytes_hash:
            reuse = self._history.find_by_hash(content_hash or "", bytes_hash or "")
            if reuse is not None:
                self._settings.set_status(
                    f"已找到该文件的翻译记录（{reuse.display_name}），点击「翻译」可直接复用"
                )

    # ═══════════════════════════════════════════════════════════
    # 拖拽
    # ═══════════════════════════════════════════════════════════

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is None:
            return
        if any(Path(u.toLocalFile()).suffix.lower() == ".pdf" for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".pdf" and path.exists():
                self._load_pdf(str(path.resolve()))
                event.acceptProposedAction()
                return

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._minimap and self._minimap.isVisible():
            self._position_minimap()

    # ═══════════════════════════════════════════════════════════
    # 事件过滤器（viewer 尺寸变化 → minimap 跟随）
    # ═══════════════════════════════════════════════════════════

    def eventFilter(self, watched, event) -> bool:
        """窗口事件分发：无边框边缘缩放 + viewer 尺寸变化 → minimap 跟随。

        边缘缩放：无边框窗口没有原生缩放边框，通过应用级事件过滤器在
        窗口边缘检测鼠标按下/拖动实现跨平台缩放。
        """
        # 无边框窗口边缘缩放（跨平台）；防御性捕获——应用级过滤器会收到
        # 各类事件（含 QWindow 等原生对象），单个异常不得拖垮整个应用
        try:
            if self._chrome_handle_event(watched, event):
                return True
        except Exception:
            logger.debug("Window chrome event error", exc_info=True)
        # 监听 viewer 的 Resize，实时重定位 minimap（侧边栏动画期间 viewer 逐帧变宽）
        if watched is self._viewer and event.type() == QEvent.Type.Resize:
            self._on_viewer_resized()
        return super().eventFilter(watched, event)

    def _on_viewer_resized(self) -> None:
        """viewer 尺寸变化时重定位 minimap（高度变化才重算面板几何）。"""
        if self._minimap is None or not self._minimap.isVisible():
            return
        h = self._viewer.height()
        if self._last_viewer_h != h:
            self._last_viewer_h = h
            self._minimap.refresh_geometry()  # 面板高度随 viewer 高度封顶
        x = self._viewer.width() - self._minimap.width() - 8
        y = 8
        self._minimap.move(x, y)
        self._minimap.raise_()

