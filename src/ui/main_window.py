"""
主窗口 — "Gilded Ink / Vellum" 双主题布局

┌──────────────────────────────────────────────────────────┐
│ ☰  FormTransPDF       [−] 适应 [+] │ 暗色主题 │ ⬇ 下载译文   │
├────────┬─────────────────────────────────────────────────┤
│ 可收起 │  ┌ 原始文档 ─── 翻译结果 ──────────────────┐   │
│ 侧边栏 │  │           PDFViewer（单窗口）            │   │
│ 280px  │  └──────────────────────────────────────────┘   │
└────────┴─────────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QEasingCurve, QVariantAnimation
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

from src.core.signals import TranslationEvent, TranslationTask, TranslationSignals
from src.core.translator import TranslationEngine
from src.ui.quick_translate_dialog import QuickTranslateDialog
from src.ui.pdf_viewer import PDFViewer
from src.ui.settings_panel import SettingsPanel
from src.ui.theme import ThemeManager, ThemePalette, theme_manager, _contrast_text
from src.ui.widgets.drop_zone import DropZone
from src.ui.widgets.history_panel import HistoryPanel
from src.ui.widgets.minimap import MinimapPanel, generate_thumbnails

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
        base = Path(__file__).resolve().parent.parent.parent / "output"
    base.mkdir(parents=True, exist_ok=True)
    return base


# ═══════════════════════════════════════════════════════════════
# 主窗口
# ═══════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    """FormTransPDF 主窗口"""

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

        # 窗口图标已在 app.py 中通过 QApplication.setWindowIcon 统一设置，
        # Windows 任务栏图标需要 QApplication 级别的图标，此处不再重复设置。

        self._current_pdf: Path | None = None
        self._mono_path: Path | None = None
        self._dual_path: Path | None = None
        self._sidebar_visible = True
        self._sidebar_anim: QVariantAnimation | None = None

        self._signals = TranslationSignals()
        self._engine = TranslationEngine()
        self._minimap: MinimapPanel | None = None  # 在 _build_ui 中创建
        self._minimap_synced = False  # 滚动条信号是否已连接
        self._quick_translate_dialog: QuickTranslateDialog | None = None

        self._build_ui()
        self._connect_signals()
        self._history.refresh()  # 启动时扫描已有记录
        self.setAcceptDrops(True)

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
        layout.setSpacing(6)

        brand = QLabel("FormTransPDF")
        brand.setObjectName("brandLabel")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(brand)

        sub = QLabel("科学论文翻译工坊")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color: {tp.text_secondary.name()}; font-size: 9pt; font-style: italic;")
        layout.addWidget(sub)

        self._drop_zone = DropZone()
        self._drop_zone.setMinimumHeight(72)
        layout.addWidget(self._drop_zone)

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
            ("↺", "重置缩放", lambda: self._viewer.zoom_reset() or self._update_zoom_label()),
        ]:
            btn = self._make_icon_btn(text, tip)
            btn.clicked.connect(slot)
            self._zoom_btns.append(btn)
            layout.addWidget(btn)

        # 缩略图切换
        self._minimap_btn = self._make_icon_btn("▦", "切换缩略图导航", width=38)
        self._minimap_btn.clicked.connect(lambda: self._minimap and self._minimap.toggle()) # 点击时切换缩略图导航
        layout.addWidget(self._minimap_btn)

        self._translate_quick_btn = self._make_icon_btn("译", "即时翻译选中文本", width=38)
        self._translate_quick_btn.clicked.connect(self._open_quick_translate)
        layout.addWidget(self._translate_quick_btn)

        # 分隔
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {tp.divider.name()}; background: transparent;")
        layout.addWidget(sep)

        # 主题切换
        self._theme_btn = self._make_icon_btn("☀" if theme_manager.is_dark else "🌙", "切换亮色/暗色主题", width=40)
        self._theme_btn.clicked.connect(self._on_toggle_theme)
        layout.addWidget(self._theme_btn)

        # 下载
        self._download_btn = QPushButton("⬇ 下载译文")
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.setEnabled(False)
        self._download_btn.setToolTip("将翻译结果保存到指定位置")
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
        bar.addTab("📄 原始文档")
        bar.addTab("🌐 翻译结果")
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
        if hasattr(app, "toggle_theme"):
            app.toggle_theme()
            self._refresh_theme()

    def _refresh_theme(self) -> None:
        """刷新所有内联样式"""
        tp = self._tp
        self.centralWidget().setStyleSheet(f"background-color: {tp.canvas.name()};")
        self._sidebar.setStyleSheet(f"QWidget#sidebar {{ background-color: {tp.background.name()}; }}")
        self._sidebar_sep.setStyleSheet(f"color: {tp.divider.name()};")
        self._apply_toolbar_styles()
        self._apply_tab_styles()

        # 更新主题按钮图标
        self._theme_btn.setText("☀" if theme_manager.is_dark else "🌙")

        if self._quick_translate_dialog:
            self._quick_translate_dialog.refresh_theme()

        # 更新 PDF 容器背景
        self._viewer.refresh_theme()

    # ═══════════════════════════════════════════════════════════
    # 侧边栏 / 缩放
    # ═══════════════════════════════════════════════════════════

    def _toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        target_w = self.SIDEBAR_WIDTH if self._sidebar_visible else 0
        start_w = self._sidebar.width()

        # 确保展开前 widget 可见
        if self._sidebar_visible:
            self._sidebar.setVisible(True)
            self._sidebar_sep.setVisible(True)

        # 如果已在目标宽度，跳过
        if start_w == target_w:
            if not self._sidebar_visible:
                self._on_sidebar_collapsed()
            return

        # ── 使用 QVariantAnimation 驱动 setFixedWidth，
        #    直接强制宽度，不受布局缓存 / 子控件 min-size 干扰 ──
        self._sidebar_anim = QVariantAnimation()
        self._sidebar_anim.setDuration(260)
        self._sidebar_anim.setStartValue(start_w)
        self._sidebar_anim.setEndValue(target_w)
        self._sidebar_anim.setEasingCurve(
            QEasingCurve.Type.OutCubic if self._sidebar_visible
            else QEasingCurve.Type.InCubic
        )

        def _drive(value: float) -> None:
            w = int(value)
            self._sidebar.setFixedWidth(w)
            self._sidebar_sep.setFixedWidth(2 if w > 10 else 0)

        self._sidebar_anim.valueChanged.connect(_drive)

        if not self._sidebar_visible:
            self._sidebar_anim.finished.connect(self._on_sidebar_collapsed)
        else:
            self._sidebar_anim.finished.connect(self._on_sidebar_expanded)

        self._sidebar_anim.finished.connect(self._on_sidebar_anim_finished)
        self._sidebar_anim.start()

    def _on_sidebar_collapsed(self) -> None:
        """收起动画结束后隐藏侧边栏和分隔线"""
        self._sidebar.setVisible(False)
        self._sidebar_sep.setVisible(False)

    def _on_sidebar_expanded(self) -> None:
        """展开动画结束后恢复约束（为下次收起动画做准备）"""
        # 从 setFixedWidth 的锁死状态恢复为可动画的 min/max 模式
        self._sidebar.setMinimumWidth(0)
        self._sidebar.setMaximumWidth(self.SIDEBAR_WIDTH)
        self._sidebar_sep.setMinimumWidth(0)
        self._sidebar_sep.setMaximumWidth(2)

    def _on_sidebar_anim_finished(self) -> None:
        """动画结束后重定位 minimap（布局已稳定）"""
        if self._minimap and self._minimap.isVisible():
            self._position_minimap()

    def _update_zoom_label(self) -> None:
        if self._viewer.is_fit_width:
            self._zoom_label.setText("适应")
        else:
            self._zoom_label.setText(f"{int(self._viewer.scale * 100)}%")

    # ═══════════════════════════════════════════════════════════
    # 信号
    # ═══════════════════════════════════════════════════════════

    def _connect_signals(self) -> None:
        self._drop_zone.pdf_dropped.connect(self._on_pdf_received)
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
    # 翻译
    # ═══════════════════════════════════════════════════════════

    def _on_translate(self) -> None:
        if not self._current_pdf:
            QMessageBox.information(self, "提示", "请先选择 PDF 文件")
            return

        self._settings.save_settings()
        task = self._settings.build_task(str(self._current_pdf))

        if task.api_key == "" and task.translator not in ("ollama", "xinference", "qwenmt"):
            reply = QMessageBox.question(
                self, "缺少 API Key",
                f"翻译服务「{task.translator}」需要 API Key。\n\n"
                "是否继续？（可能使用环境变量中的 Key）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._settings.set_translating(True)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        asyncio.ensure_future(self._run_translate(task))

    async def _run_translate(self, task: TranslationTask) -> None:
        try:
            async for event in self._engine.run(task, self._signals, output_dir=self._output_dir):
                pass
        except Exception as exc:
            logger.exception("Translation failed")
            QMessageBox.critical(self, "翻译异常", str(exc))
        finally:
            self._settings.set_translating(False)
            self._progress.setVisible(False)

    def _on_progress(self, event: TranslationEvent) -> None:
        self._progress.setMaximum(event.total)
        self._progress.setValue(event.current)
        self._settings.set_status(event.message)

    def _open_quick_translate(self) -> None:
        if self._quick_translate_dialog is None:
            self._quick_translate_dialog = QuickTranslateDialog(self)
        self._quick_translate_dialog.set_profile(self._settings.translation_profile())
        self._quick_translate_dialog.refresh_theme()
        self._quick_translate_dialog._position_bottom_right()
        self._quick_translate_dialog.show()
        self._quick_translate_dialog.raise_()
        self._quick_translate_dialog.activateWindow()

    def _on_text_selected(self, text: str) -> None:
        if not text.strip():
            return
        self._open_quick_translate()
        if self._quick_translate_dialog:
            self._quick_translate_dialog.set_profile(self._settings.translation_profile())
            self._quick_translate_dialog.set_source_text(text, auto_translate=True)

    def _on_finished(self, event: TranslationEvent) -> None:
        self._progress.setValue(self._progress.maximum())
        self._settings.set_status(f"✅ 翻译完成 — 耗时 {event.elapsed_seconds:.1f}s")

        self._dual_path = event.dual_pdf_path
        self._mono_path = event.mono_pdf_path

        # 根据用户选择决定展示哪个
        task = self._settings.build_task(str(self._current_pdf))
        if task.output_mode == "mono":
            target = self._mono_path
        else:
            target = self._dual_path or self._mono_path

        if target and target.exists():
            self._tab_bar.setTabEnabled(1, True)
            self._tab_bar.setCurrentIndex(1)
            self._viewer.load_pdf(str(target))
            self._download_btn.setEnabled(True)
            self._update_zoom_label()
            # 刷新历史记录
            self._history.refresh()
        else:
            QMessageBox.warning(self, "结果缺失", "翻译流程已完成，但未生成输出文件。")

    def _on_error(self, event: TranslationEvent) -> None:
        self._settings.set_status(f"❌ {event.message}", is_error=True)
        QMessageBox.critical(self, "翻译错误", f"{event.message}\n\n{event.error_details}")

    def _on_download(self) -> None:
        target = self._dual_path or self._mono_path
        if not target or not target.exists():
            QMessageBox.information(self, "提示", "没有可下载的翻译结果")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "保存翻译结果", target.name, "PDF 文件 (*.pdf)")
        if dest:
            try:
                shutil.copy2(str(target), str(dest))
                self._settings.set_status(f"已保存: {Path(dest).name}")
            except Exception as exc:
                QMessageBox.critical(self, "保存失败", str(exc))

    # ═══════════════════════════════════════════════════════════
    # 历史记录
    # ═══════════════════════════════════════════════════════════

    def _on_history_selected(self, dual_path: str, mono_path: str, name: str) -> None:
        """点击历史记录中的翻译"""
        target = dual_path or mono_path
        if not target:
            return
        path = Path(target)
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", f"历史文件已失效:\n{path}")
            return
        self._dual_path = Path(dual_path) if dual_path else None
        self._mono_path = Path(mono_path) if mono_path else None

        self._viewer.load_pdf(target)
        self._setup_minimap()
        self._tab_bar.setCurrentIndex(0)
        self._tab_bar.setTabEnabled(1, True)
        self._download_btn.setEnabled(bool(target))
        self._settings.set_pdf_loaded(name, loaded=False)
        self._settings.set_status(f"📜 历史: {name}")

    def _on_output_mode_changed(self) -> None:
        """输出模式下拉框变更时，若正在查看历史记录则切换双栏/单栏"""
        # 仅在有历史双栏+单栏文件时生效
        if not (self._dual_path and self._mono_path):
            return
        mode = self._settings._output_mode_combo.currentData()
        if mode == "mono" and self._mono_path.exists():
            self._viewer.load_pdf(str(self._mono_path))
            self._setup_minimap()
        elif mode == "dual" and self._dual_path.exists():
            self._viewer.load_pdf(str(self._dual_path))
            self._setup_minimap()

    # ═══════════════════════════════════════════════════════════
    # 缩略图导航
    # ═══════════════════════════════════════════════════════════

    def _setup_minimap(self) -> None:
        """为当前 PDF 生成缩略图并加载到 minimap（默认隐藏，通过按钮唤起）"""
        doc = self._viewer.document
        from PySide6.QtPdf import QPdfDocument
        if doc is None or doc.status() != QPdfDocument.Status.Ready:
            return
        try:
            thumbs = generate_thumbnails(doc, self._viewer.page_count)
            self._minimap.load_document(self._viewer.page_count, thumbs)
            self._position_minimap()
            # 监听滚动条变化（仅首次连接，避免重复）
            if not self._minimap_synced:
                self._viewer.verticalScrollBar().valueChanged.connect(
                    self._update_minimap_viewport
                )
                self._minimap_synced = True
        except Exception:
            logger.debug("Failed to generate thumbnails", exc_info=True)

    def _position_minimap(self) -> None:
        """将 minimap 定位到 viewer 右上角"""
        x = self._viewer.width() - self._minimap.width() - 8
        y = 8
        self._minimap.move(x, y)
        self._minimap.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._minimap.isVisible():
            self._position_minimap()

    def _update_minimap_viewport(self) -> None:
        """根据当前滚动位置更新 minimap 的视口指示器"""
        vbar = self._viewer.verticalScrollBar()
        if vbar.maximum() <= 0:
            return
        ratio_start = vbar.value() / vbar.maximum()
        viewport_h = self._viewer.viewport().height()
        # QPdfView 内容总高度 = scrollbar_max + viewport_height
        total_h = vbar.maximum() + viewport_h
        ratio_end = min((vbar.value() + viewport_h) / max(total_h, 1), 1.0)
        self._minimap.set_visible_range(ratio_start, ratio_end)

    def _on_minimap_page_clicked(self, page_number: int) -> None:
        """点击 minimap 缩略图 → 跳转到对应页面"""
        self._viewer.goto_page(page_number)

    def _on_minimap_dragged(self, ratio: float) -> None:
        """拖拽 minimap 视口指示器 → 实时滚动 PDF"""
        vbar = self._viewer.verticalScrollBar()
        if vbar and vbar.maximum() > 0:
            vbar.setValue(int(ratio * vbar.maximum()))

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

