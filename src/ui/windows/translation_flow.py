"""
翻译流程 — 引擎后台加载、翻译编排、即时翻译弹窗管理。

以 mixin 形式注入 MainWindow（windows/main_window.py），
依赖 self._settings / self._viewer / self._tab_bar / self._progress /
self._engine / self._signals 等由 MainWindow.__init__ 初始化的状态。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox

from src.core.signals import TranslationEvent, TranslationTask
from src.core.translation.engine import EngineNotReadyError, TranslationEngine
from src.ui.dialogs.quick_translate import QuickTranslateDialog

logger = logging.getLogger(__name__)


class _EngineLoader(QObject):
    """在后台线程中加载翻译引擎，完成后通过信号通知主线程。"""

    loaded = Signal()
    failed = Signal(str)

    def __init__(self, engine: TranslationEngine) -> None:
        super().__init__()
        self._engine = engine

    def run(self) -> None:
        try:
            self._engine.load()
        except Exception as exc:
            logger.exception("Translation engine load failed")
            self._safe_emit(self.failed, str(exc))
        else:
            self._safe_emit(self.loaded)

    def _safe_emit(self, signal, *args) -> None:
        """主窗口可能已销毁（用户提前关闭应用），此时静默忽略信号。"""
        try:
            signal.emit(*args)
        except RuntimeError:
            pass


class _TranslationFlowMixin:
    """翻译编排：引擎就绪门控、进度/完成/错误处理、下载、即时翻译。"""

    def _on_translate(self) -> None:
        if not self._current_pdf:
            QMessageBox.information(self, "提示", "请先选择 PDF 文件")
            return

        # 先持久化设置：即使引擎未就绪/加载失败，用户的选择也必须保存
        self._settings.save_settings()

        if not self._engine.is_ready:
            if self._engine.load_error:
                self._settings.set_status(
                    f"翻译引擎不可用：{self._engine.load_error}", is_error=True
                )
                return
            # 引擎仍在后台加载：挂起请求，就绪后自动开始
            self._pending_translate = True
            self._settings.set_status("翻译引擎加载中，就绪后自动开始…")
            return

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

    def _start_engine_load(self) -> None:
        """后台线程加载翻译引擎（重型 pdf2zh_next/babeldoc 导入），不阻塞启动。"""
        worker = _EngineLoader(self._engine)
        worker.loaded.connect(self._on_engine_loaded)
        worker.failed.connect(self._on_engine_load_failed)
        self._engine_worker = worker
        self._engine_thread = threading.Thread(
            target=worker.run, name="engine-loader", daemon=True
        )
        self._settings.set_status("正在后台加载翻译引擎…")
        self._engine_thread.start()

    def _on_engine_loaded(self) -> None:
        """翻译引擎加载完成：更新状态，若有挂起请求则自动开始翻译。"""
        logger.info("Translation engine ready")
        self._settings.set_status("翻译引擎就绪 — 可以开始翻译")
        if self._pending_translate:
            self._pending_translate = False
            self._on_translate()

    def _on_engine_load_failed(self, message: str) -> None:
        """翻译引擎加载失败：提示用户，翻译功能不可用。"""
        logger.error("Translation engine failed to load: %s", message)
        self._settings.set_status(f"翻译引擎加载失败：{message}", is_error=True)

    async def _run_translate(self, task: TranslationTask) -> None:
        try:
            async for event in self._engine.run(task, self._signals, output_dir=self._output_dir):
                pass
        except EngineNotReadyError as exc:
            self._settings.set_status(str(exc), is_error=True)
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

    def _on_auto_translate_toggled(self, checked: bool) -> None:
        """划词自动弹出即时翻译开关（持久化保存 + 更新提示）"""
        self._auto_popup_quick = checked
        self._app_settings.setValue("quick_translate_auto_popup", checked)
        self._app_settings.sync()
        self._auto_translate_switch.setToolTip(
            "划词时自动弹出即时翻译（开）" if checked else "划词时自动弹出即时翻译（关）"
        )

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
        if not self._auto_popup_quick:
            # 用户关闭了划词自动弹出（仅用于阅读/高亮，不打扰浏览）
            return
        self._open_quick_translate()
        if self._quick_translate_dialog:
            self._quick_translate_dialog.set_profile(self._settings.translation_profile())
            self._quick_translate_dialog.set_source_text(text, auto_translate=True)

    def _on_viewer_translate_requested(self, text: str) -> None:
        """浮动工具栏「翻译」→ 打开即时翻译并自动翻译（不受划词自动弹出开关影响）"""
        if not text.strip():
            return
        self._open_quick_translate()
        if self._quick_translate_dialog:
            self._quick_translate_dialog.set_profile(self._settings.translation_profile())
            self._quick_translate_dialog.set_source_text(text, auto_translate=True)

    def _on_finished(self, event: TranslationEvent) -> None:
        self._progress.setValue(self._progress.maximum())
        self._settings.set_status(f"翻译完成 — 耗时 {event.elapsed_seconds:.1f}s")

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
        self._settings.set_status(f"{event.message}", is_error=True)
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
