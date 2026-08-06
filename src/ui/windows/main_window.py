"""
主窗口 — "Gilded Ink / Vellum" 双主题布局

┌──────────────────────────────────────────────────────────┐
│ ☰  FormTransPDF       [−] 适应 [+] │ 暗色主题 │ 下载译文      │
├────────┬─────────────────────────────────────────────────┤
│ 可收起 │  ┌ 原始文档 ─── 翻译结果 ──────────────────┐   │
│ 侧边栏 │  │           PDFViewer（单窗口）            │   │
│ 280px  │  └──────────────────────────────────────────┘   │
└────────┴─────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

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
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from src.core.signals import TranslationSignals
from src.core.translation.engine import TranslationEngine
from src.core.translation.records import compute_source_fingerprint
from src.ui.base.icon_factory import IconHoverFilter, accent_icon
from src.ui.base.theme import ThemePalette, theme_manager, _contrast_text
from src.ui.dialogs.quick_translate import QuickTranslateDialog
from src.ui.panels.settings import SettingsPanel, SETTINGS_APP, SETTINGS_ORG
from src.ui.pdf.viewer import PDFViewer
from src.ui.windows.history_flow import _HistoryFlowMixin
from src.ui.windows.minimap_controller import _MinimapControllerMixin
from src.ui.windows.sidebar_behavior import _SidebarBehaviorMixin
from src.ui.windows.translation_flow import _EngineLoader, _TranslationFlowMixin
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
    QMainWindow,
):
    """FormTransPDF 主窗口

    将各功能域 mixin 组合为完整主窗口：
    - _SidebarBehaviorMixin   侧边栏动画 / 缩放标签
    - _TranslationFlowMixin   翻译编排 / 即时翻译弹窗
    - _HistoryFlowMixin       历史记录回放
    - _MinimapControllerMixin 缩略图导航
    """

    SIDEBAR_WIDTH = 280
    MIN_WINDOW_W = 900
    MIN_WINDOW_H = 600
    DEFAULT_W = 1400
    DEFAULT_H = 850

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FormTransPDF — PDF 科学论文翻译")
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

        self._current_pdf: Path | None = None
        self._mono_path: Path | None = None
        self._dual_path: Path | None = None
        # 源文件指纹（重复提交复用检测；_load_pdf 时计算）
        self._current_pdf_hash: str | None = None
        self._current_pdf_bytes_hash: str | None = None
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
        self._quick_translate_dialog: QuickTranslateDialog | None = None
        self._icon_hovers: list = []  # IconHoverFilter 列表（主题切换时刷新）
        self._theme_icon_filter: IconHoverFilter | None = None

        self._build_ui()
        self._connect_signals()
        self._history.refresh()  # 启动时扫描已有记录
        self.setAcceptDrops(True)
        self._start_engine_load()

    # ═══════════════════════════════════════════════════════════
    # UI 构建
    # ═══════════════════════════════════════════════════════════

    @property
    def _tp(self) -> ThemePalette:
        return theme_manager.palette

    def _build_ui(self) -> None:
        tp = self._tp
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"background-color: {tp.canvas.name()};")

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = self._build_sidebar()
        root.addWidget(self._sidebar)

        self._sidebar_sep = QFrame()
        self._sidebar_sep.setFrameShape(QFrame.Shape.VLine)
        self._sidebar_sep.setMinimumWidth(0)  # 允许动画收缩到 0
        self._sidebar_sep.setStyleSheet(f"color: {tp.divider.name()};")
        root.addWidget(self._sidebar_sep)

        main_area = QWidget()
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._toolbar = self._build_toolbar()
        main_layout.addWidget(self._toolbar)

        self._tab_bar = self._build_tab_bar()
        main_layout.addWidget(self._tab_bar)

        self._viewer = PDFViewer()
        self._viewer.text_selected.connect(self._on_text_selected)
        self._viewer.translate_requested.connect(self._on_viewer_translate_requested)
        # 监听 viewer 尺寸变化（侧边栏动画/窗口缩放），实时跟随重定位 minimap
        self._viewer.installEventFilter(self)
        main_layout.addWidget(self._viewer, stretch=1)

        # 缩略图导航（覆盖在 PDF 查看器右上角）
        self._minimap = MinimapPanel(self._viewer) # 缩略图导航
        self._minimap.page_clicked.connect(self._on_minimap_page_clicked)
        self._minimap.viewport_dragged.connect(self._on_minimap_dragged)
        root.addWidget(main_area, stretch=1) # 主内容区域

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
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        brand = QLabel("FormTransPDF")
        brand.setObjectName("brandLabel")
        brand.setStyleSheet(f"background: transparent;")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(brand)

        sub = QLabel("科学论文翻译工坊")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color: {tp.text_secondary.name()}; font-size: 9pt; font-style: italic; padding-bottom: 4px; background: transparent;")
        layout.addWidget(sub)

        # self._drop_zone = DropZone()
        # self._drop_zone.setMinimumHeight(72)
        # layout.addWidget(self._drop_zone)

        self._settings = SettingsPanel()
        layout.addWidget(self._settings)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # 历史记录
        self._history = HistoryPanel(self._output_dir)
        layout.addWidget(self._history, stretch=1)

        return sidebar

    # ── 顶栏 ─────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        tp = self._tp
        bar = QWidget()
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

    # ── 标签栏 ───────────────────────────────────────────────

    def _build_tab_bar(self) -> QTabBar:
        bar = QTabBar()
        bar.addTab(accent_icon("document", 16), " 原始文档")
        bar.addTab(accent_icon("web", 16), " 翻译结果")
        bar.setCurrentIndex(0)
        bar.setTabEnabled(1, False)
        bar.currentChanged.connect(self._on_tab_changed)
        self._tab_bar_widget = bar
        self._apply_tab_styles()
        return bar

    def _apply_tab_styles(self) -> None:
        tp = self._tp
        if not hasattr(self, "_tab_bar_widget"):
            return
        bar = self._tab_bar_widget
        bar.setStyleSheet(
            f"QTabBar {{ background-color: {tp.background.name()}; }}"
            f"QTabBar::tab {{"
            f"  background: {tp.surface.name()}; color: {tp.text_secondary.name()};"
            f"  border: none; padding: 6px 20px; margin-right: 2px;"
            f"  border-bottom: 2px solid transparent; font-size: 10pt;"
            f"}}"
            f"QTabBar::tab:selected {{"
            f"  color: {tp.accent.name()}; background: {tp.canvas.name()};"
            f"  border-bottom: 2px solid {tp.accent.name()};"
            f"}}"
            f"QTabBar::tab:hover:!selected {{"
            f"  color: {tp.text_primary.name()}; background: {tp.surface_hover.name()};"
            f"}}"
            f"QTabBar::tab:disabled {{ color: {tp.text_disabled.name()}; }}"
        )

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
        self._apply_toolbar_styles()
        self._apply_tab_styles()

        # 更新主题按钮图标与所有主题色 SVG 图标
        self._refresh_icons()

        if self._quick_translate_dialog:
            self._quick_translate_dialog.refresh_theme()

        # 更新 PDF 容器背景
        self._viewer.refresh_theme()

    def _refresh_icons(self) -> None:
        """主题切换后按新主题重建所有 SVG 图标颜色"""
        if hasattr(self, "_tab_bar_widget"):
            bar = self._tab_bar_widget
            bar.setTabIcon(0, accent_icon("document", 16))
            bar.setTabIcon(1, accent_icon("web", 16))
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
        # 输出模式变更时（历史回放场景）切换双栏/单栏
        self._settings._output_mode_combo.currentIndexChanged.connect(
            self._on_output_mode_changed
        )

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

        self._current_pdf = pdf_path
        self._mono_path = None
        self._dual_path = None

        # ── 计算源文件指纹（内容+字节），用于「已翻译过则复用」检测 ──
        # 论文 PDF 通常很小，同步计算可接受；失败则跳过复用检测。
        self._current_pdf_hash = None
        self._current_pdf_bytes_hash = None
        try:
            self._current_pdf_hash, self._current_pdf_bytes_hash = compute_source_fingerprint(pdf_path)
        except Exception:
            logger.debug("Failed to fingerprint source pdf", exc_info=True)

        # 若该文件已翻译过，给出可复用提示（真正复用发生在点击「翻译」时）
        if self._current_pdf_hash or self._current_pdf_bytes_hash:
            reuse = self._history.find_by_hash(
                self._current_pdf_hash or "", self._current_pdf_bytes_hash or ""
            )
            if reuse is not None:
                self._settings.set_status(
                    f"已找到该文件的翻译记录（{reuse.display_name}），点击「翻译」可直接复用"
                )

        self._viewer.clear()
        try:
            self._viewer.load_pdf(str(pdf_path))
            # 生成缩略图并加载到 minimap
            self._setup_minimap()
        except Exception as exc:
            QMessageBox.critical(self, "PDF 加载失败", str(exc))
            return

        self._tab_bar.setCurrentIndex(0)
        self._tab_bar.setTabEnabled(1, False)
        self._download_btn.setEnabled(False)
        self._settings.set_pdf_loaded(path, loaded=True)
        self._update_zoom_label()

    # ═══════════════════════════════════════════════════════════
    # 标签切换
    # ═══════════════════════════════════════════════════════════

    def _on_tab_changed(self, index: int) -> None:
        if index == 0 and self._current_pdf:
            self._viewer.load_pdf(str(self._current_pdf))
            self._setup_minimap()
        elif index == 1:
            target = self._dual_path or self._mono_path
            if target and target.exists():
                self._viewer.load_pdf(str(target))
                self._setup_minimap()
        self._update_zoom_label()

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
        """监听 viewer 的 Resize，实时重定位 minimap。

        侧边栏收起/展开动画期间，viewer 宽度逐帧变化（其左上角也随布局左移），
        仅靠动画结束后的单次定位会读到未生效的宽度、导致 minimap 位置偏移；
        改为在每次 viewer resize 时即时跟随，保证动画全程贴住右上角。
        """
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

