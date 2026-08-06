"""即时翻译窗口。"""

from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QFontMetrics, QMouseEvent, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QGraphicsDropShadowEffect,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.translation.text import (
    TextTranslationError,
    TextTranslationProfile,
    normalize_translation_profile,
    translate_text,
)
from src.ui.base.icon_factory import svg_icon
from src.ui.base.theme import ThemePalette, theme_manager, _contrast_text

LANGUAGE_OPTIONS = [
    ("en", "English"),
    ("zh", "中文"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("ru", "Русский"),
]


class QuickTranslateDialog(QDialog):
    """即时短文本翻译对话框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(480, 400)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._profile = TextTranslationProfile(translator="openai")
        self._active_task: asyncio.Task | None = None
        self._last_autotrigger_text = ""
        self._drag_offset = QPoint()
        self._dragging = False
        self._drag_start_global_y = 0
        self._drag_close_enabled = True
        self._drag_close_threshold = 200

        self._build_ui()
        self.refresh_theme()
        self._sync_from_profile()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        self._card = QFrame()
        self._card.setObjectName("qtCard")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setContentsMargins(14, 12, 14, 12)
        self._card_layout.setSpacing(10)
        root.addWidget(self._card)

        header = QHBoxLayout()
        header.setSpacing(8)

        self._drag_handle = QFrame()
        self._drag_handle.setObjectName("qtDragHandle")
        self._drag_handle.installEventFilter(self)
        drag_layout = QHBoxLayout(self._drag_handle)
        drag_layout.setContentsMargins(0, 0, 0, 0)
        drag_layout.setSpacing(8)

        title = QLabel("即时翻译")
        title.setObjectName("qtTitle")
        drag_layout.addWidget(title)

        self._service_label = QLabel()
        self._service_label.setObjectName("qtService")
        self._service_label.setMaximumWidth(300)
        drag_layout.addWidget(self._service_label, stretch=1)

        header.addWidget(self._drag_handle, stretch=1)

        self._close_btn = QPushButton("")
        self._close_btn.setObjectName("qtCloseBtn")
        self._close_btn.setFixedSize(32, 32)
        self._close_btn.setIconSize(QSize(18, 18))
        self._close_btn.setIcon(
            svg_icon("close", theme_manager.palette.text_secondary, 18)
        )
        self._close_btn.setToolTip("关闭")
        self._close_btn.clicked.connect(self.close)
        header.addWidget(self._close_btn)

        self._card_layout.addLayout(header)

        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)

        self._translator_label = QLabel("服务")
        self._translator_label.setObjectName("qtPill")
        profile_row.addWidget(self._translator_label)

        self._source_combo = QComboBox()
        self._target_combo = QComboBox()
        for code, name in LANGUAGE_OPTIONS:
            self._source_combo.addItem(name, code)
            self._target_combo.addItem(name, code)
        self._source_combo.currentIndexChanged.connect(self._update_service_summary)
        self._target_combo.currentIndexChanged.connect(self._update_service_summary)
        profile_row.addWidget(QLabel("源"))
        profile_row.addWidget(self._source_combo, stretch=1)
        profile_row.addWidget(QLabel("目标"))
        profile_row.addWidget(self._target_combo, stretch=1)
        self._card_layout.addLayout(profile_row)

        # ── 原文 / 译文：字段标签与文本框组合为带边框整体，中间用分隔线划分 ──
        self._source_edit = QPlainTextEdit()
        self._source_edit.setPlaceholderText("在这里输入或粘贴需要即时翻译的文本")
        self._source_edit.textChanged.connect(self._update_translate_hint)
        self._source_edit.setFixedHeight(140)
        self._card_layout.addWidget(self._make_field("原文", self._source_edit))

        self._divider = QWidget()
        self._divider.setObjectName("qtDivider")
        self._divider.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._divider.setFixedHeight(1)
        self._card_layout.addWidget(self._divider)

        self._target_edit = QPlainTextEdit()
        self._target_edit.setReadOnly(True)
        self._target_edit.setPlaceholderText("译文会显示在这里")
        self._target_edit.setFixedHeight(140)
        self._card_layout.addWidget(self._make_field("译文", self._target_edit))

        footer = QHBoxLayout()
        footer.setSpacing(8)

        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("qtStatus")
        self._status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        footer.addWidget(self._status_label)
        footer.addStretch()

        self._clear_btn = QPushButton("清空")
        self._clear_btn.clicked.connect(self._clear_all)
        footer.addWidget(self._clear_btn)

        self._translate_btn = QPushButton("翻译")
        self._translate_btn.setObjectName("primaryBtn")
        self._translate_btn.clicked.connect(self._start_translate)
        footer.addWidget(self._translate_btn)
        self._card_layout.addLayout(footer)

    def _make_field(self, text: str, edit: QPlainTextEdit) -> QFrame:
        """字段容器：小标签 + 文本框 组合为一个带边框的整体。"""
        frame = QFrame()
        frame.setObjectName("qtField")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(4)
        label = QLabel(text)
        label.setObjectName("qtFieldLabel")
        lay.addWidget(label)
        lay.addWidget(edit)
        return frame

    def set_profile(self, profile: dict[str, str] | TextTranslationProfile) -> None:
        if isinstance(profile, TextTranslationProfile):
            self._profile = normalize_translation_profile(profile)
        else:
            self._profile = normalize_translation_profile({
                "translator": str(profile.get("translator", "openai") or "openai"),
                "api_key": str(profile.get("api_key", "") or ""),
                "model": str(profile.get("model", "") or ""),
                "base_url": str(profile.get("base_url", "") or ""),
                "lang_in": str(profile.get("lang_in", "en") or "en"),
                "lang_out": str(profile.get("lang_out", "zh") or "zh"),
            })
        self._sync_from_profile()

    def set_source_text(self, text: str, auto_translate: bool = True) -> None:
        self._source_edit.blockSignals(True)
        self._source_edit.setPlainText(text)
        self._source_edit.blockSignals(False)
        if text.strip():
            self._source_edit.setFocus()
            self._source_edit.moveCursor(QTextCursor.MoveOperation.End)
            if auto_translate and text != self._last_autotrigger_text:
                self._last_autotrigger_text = text
                self._position_bottom_right()
                self.show()
                self.raise_()
                self.activateWindow()
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    loop.create_task(self.translate_now())
        self._update_service_summary()

    def _position_bottom_right(self) -> None:
        """将浮窗定位到父窗口（或屏幕）的右下角。"""
        parent_win = self.parent() if self.parent() else None
        if parent_win and parent_win.isVisible():
            parent_rect = parent_win.geometry()
            target_x = parent_rect.right() - self.width() - 20
            target_y = parent_rect.bottom() - self.height() - 20
        else:
            screen = QApplication.primaryScreen()
            if screen:
                screen_rect = screen.availableGeometry()
                target_x = screen_rect.right() - self.width() - 20
                target_y = screen_rect.bottom() - self.height() - 20
            else:
                return
        self.move(target_x, target_y)

    def _start_translate(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.translate_now())

    def refresh_theme(self) -> None:
        tp = theme_manager.palette
        self.setStyleSheet(
            f"QDialog {{ background-color: transparent; }}"
            f"QFrame#qtCard {{ background-color: {tp.background.name()}; border: 1px solid {tp.divider.name()}; border-radius: 14px; }}"
            f"QFrame#qtDragHandle {{ background-color: transparent; }}"
            f"QLabel#qtTitle {{ color: {tp.accent.name()}; font-size: 15pt; font-weight: 700; }}"
            f"QLabel#qtFieldLabel {{ color: {tp.accent.name()}; font-size: 9pt; font-weight: 700; background: transparent; }}"
            f"QFrame#qtField {{"
            f"  background-color: {tp.surface.name()};"
            f"  border: 1px solid {tp.divider.name()};"
            f"  border-radius: 10px;"
            f"}}"
            f"QFrame#qtField QPlainTextEdit {{ background-color: transparent; border: none; }}"
            f"QWidget#qtDivider {{ background-color: {tp.divider.name()}; }}"
            f"QLabel#qtPill {{ color: {_contrast_text(tp.accent).name()}; background-color: {tp.accent.name()};"
            f" border-radius: 10px; padding: 4px 10px; font-size: 9pt; }}"
            f"QLabel#qtService {{ color: {tp.text_secondary.name()}; font-size: 9pt; }}"
            f"QLabel#qtStatus {{ color: {tp.text_secondary.name()}; font-size: 9pt; }}"
            f"QComboBox, QPlainTextEdit {{"
            f"  background-color: {tp.surface.name()};"
            f"  color: {tp.text_primary.name()};"
            f"  border: 1px solid {tp.divider.name()};"
            f"  border-radius: 10px;"
            f"}}"
            f"QPlainTextEdit {{ selection-background-color: {tp.accent_muted.name()}; }}"
            f"QComboBox::drop-down {{ border: none; width: 24px; }}"
            f"QComboBox QAbstractItemView {{"
            f"  background-color: {tp.surface.name()};"
            f"  color: {tp.text_primary.name()};"
            f"  selection-background-color: {tp.accent.name()};"
            f"  selection-color: {_contrast_text(tp.accent).name()};"
            f"}}"
            f"QPushButton {{"
            f"  background-color: {tp.surface.name()};"
            f"  color: {tp.accent.name()};"
            f"  border: 1px solid {tp.divider.name()};"
            f"  border-radius: 9px;"
            f"  padding: 6px 12px;"
            f"}}"
            f"QPushButton:hover {{ background-color: {tp.surface_hover.name()}; }}"
            f"QPushButton#primaryBtn {{"
            f"  background-color: {tp.accent.name()};"
            f"  color: {_contrast_text(tp.accent).name()};"
            f"  border-color: {tp.accent.name()};"
            f"}}"
            f"QPushButton#primaryBtn:hover {{ background-color: {tp.accent_hover.name()}; }}"
            f"QPushButton#qtCloseBtn {{ color: {tp.text_secondary.name()}; background-color: transparent; border: none; font-size: 14pt; }}"
            f"QPushButton#qtCloseBtn:hover {{ background-color: {tp.accent_muted.name()}; color: {tp.accent.name()}; }}"
        )
        self._source_edit.setTabChangesFocus(False)
        self._target_edit.setTabChangesFocus(False)
        self._source_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._target_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._close_btn.setIcon(svg_icon("close", tp.text_secondary, 18))
        self._update_service_summary()
        self._apply_button_state(False)

    async def translate_now(self) -> None:
        text = self._source_edit.toPlainText().strip()
        if not text:
            self._set_status("请先输入或选中需要翻译的文本", is_error=False)
            return

        if self._active_task and not self._active_task.done():
            return

        self._apply_button_state(True)
        self._set_status("翻译中…", is_error=False)
        source_lang = str(self._source_combo.currentData() or "en")
        target_lang = str(self._target_combo.currentData() or "zh")

        async def _job() -> None:
            try:
                translated = await translate_text(
                    text,
                    self._profile,
                    source_lang,
                    target_lang,
                )
            except TextTranslationError as exc:
                self._show_translation_error(str(exc))
            except Exception as exc:  # pragma: no cover - UI guardrail
                self._show_translation_error(f"翻译失败：{exc}")
            else:
                self._target_edit.setPlainText(translated)
                self._set_target_error_style(False)
                self._set_status("翻译完成", is_error=False)
            finally:
                self._apply_button_state(False)

        self._active_task = asyncio.create_task(_job())
        await self._active_task

    def _language_label(self, code: Any) -> str:
        code_str = str(code or "")
        for option_code, name in LANGUAGE_OPTIONS:
            if option_code == code_str:
                return name
        return code_str or "未知语言"

    def _sync_from_profile(self) -> None:
        self._set_combo_value(self._source_combo, self._profile.lang_in)
        self._set_combo_value(self._target_combo, self._profile.lang_out)
        self._translator_label.setText(self._profile.translator.upper())
        model_text = self._profile.model or "默认模型"
        base_text = self._profile.base_url or "默认地址"
        self._set_service_text(f"{model_text} · {base_text}")
        self._update_service_summary()

    def _set_service_text(self, text: str) -> None:
        """设置服务摘要文本，超出宽度时右侧省略，完整文本放入 tooltip。"""
        max_w = self._service_label.width() if self._service_label.width() > 40 else 320
        elided = QFontMetrics(self._service_label.font()).elidedText(
            text, Qt.TextElideMode.ElideRight, max_w
        )
        self._service_label.setText(elided)
        self._service_label.setToolTip(text)

    def _update_service_summary(self) -> None:
        source = self._language_label(self._source_combo.currentData())
        target = self._language_label(self._target_combo.currentData())
        self._set_service_text(
            f"{self._profile.translator.upper()} · {source} → {target} · {self._profile.base_url or '默认接口'}"
        )

    def _update_translate_hint(self) -> None:
        text = self._source_edit.toPlainText().strip()
        if text:
            self._status_label.setText(f"已载入 {len(text)} 个字符")
        else:
            self._status_label.setText("就绪")

    def _set_combo_value(self, combo: QComboBox, code: str) -> None:
        idx = combo.findData(code)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _apply_button_state(self, busy: bool) -> None:
        self._translate_btn.setEnabled(not busy)
        self._clear_btn.setEnabled(not busy)
        self._source_combo.setEnabled(not busy)
        self._target_combo.setEnabled(not busy)
        self._source_edit.setReadOnly(busy)
        self._translate_btn.setText("翻译中…" if busy else "翻译")

    def _set_status(self, text: str, is_error: bool) -> None:
        tp = theme_manager.palette
        color = tp.error.name() if is_error else tp.text_secondary.name()
        self._status_label.setStyleSheet(f"color: {color}; font-size: 9.5pt;")
        # 限制状态文本像素宽度：否则其 sizeHint 会撑大布局最小宽度，
        # 导致弹窗重新打开时被拉宽（长宽比翻转）。完整文本放入 tooltip。
        max_w = 260
        elided = self._status_label.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max_w
        )
        self._status_label.setText(elided)
        # 文本被截断时悬停可查看完整内容（完整信息已展示在译文区）
        self._status_label.setToolTip(text if elided != text else "")

    def _set_target_error_style(self, error: bool) -> None:
        """译文框颜色：错误时用主题错误色，正常时恢复默认。"""
        if error:
            self._target_edit.setStyleSheet(
                f"color: {theme_manager.palette.error.name()};"
            )
        else:
            self._target_edit.setStyleSheet("")

    def _show_translation_error(self, message: str) -> None:
        """在译文区完整呈现错误信息（不撑大弹窗），状态栏显示简短摘要。"""
        self._set_target_error_style(True)
        self._target_edit.setPlainText(message)
        summary = message if len(message) <= 72 else message[:72].rstrip() + "…"
        self._set_status(summary, is_error=True)

    def _clear_all(self) -> None:
        self._source_edit.clear()
        self._target_edit.clear()
        self._set_target_error_style(False)
        self._set_status("已清空", is_error=False)

    def eventFilter(self, obj, event):
        if obj is getattr(self, "_drag_handle", None):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._dragging = True
                self._drag_start_global_y = event.globalPosition().toPoint().y()
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.Type.MouseMove and self._dragging:
                new_pos = event.globalPosition().toPoint() - self._drag_offset
                self.move(new_pos)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._dragging = False
                if self._drag_close_enabled:
                    dy = event.globalPosition().toPoint().y() - self._drag_start_global_y
                    if dy > self._drag_close_threshold:
                        self.close()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
        super().closeEvent(event)
