"""
翻译设置面板 — 服务选择、API Key、语言配置 + 持久化 + 模型下拉
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
)

from src.core.signals import TranslationTask
from src.ui.theme import Colors, DIVIDER_STYLE

# ═══════════════════════════════════════════════════════════════
# 翻译服务元数据 + 常用模型列表
# ═══════════════════════════════════════════════════════════════

TRANSLATOR_OPTIONS: dict[str, dict] = {
    "openai": {
        "label": "OpenAI", "needs_key": True, "needs_model": True,
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3-mini", "o4-mini"],
    },
    "deepseek": {
        "label": "DeepSeek", "needs_key": True, "needs_model": True,
        "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
    },
    "deepl": {
        "label": "DeepL", "needs_key": True, "needs_model": False,
        "models": [],
    },
    "google": {
        "label": "Google", "needs_key": True, "needs_model": False,
        "models": [],
    },
    "bing": {
        "label": "Bing", "needs_key": True, "needs_model": False,
        "models": [],
    },
    "ollama": {
        "label": "Ollama（本地）", "needs_key": False, "needs_model": True,
        "models": ["llama3", "qwen2.5", "mistral", "gemma3", "deepseek-r1"],
    },
    "zhipu": {
        "label": "智谱 GLM", "needs_key": True, "needs_model": True,
        "models": ["glm-4-plus", "glm-4-flash", "glm-4-air"],
    },
    "siliconflow": {
        "label": "SiliconFlow", "needs_key": True, "needs_model": True,
        "models": ["Qwen/Qwen2.5-7B-Instruct", "deepseek-ai/DeepSeek-V3"],
    },
    "gemini": {
        "label": "Gemini", "needs_key": True, "needs_model": True,
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
    },
    "groq": {
        "label": "Groq", "needs_key": True, "needs_model": True,
        "models": ["llama-3.3-70b", "mixtral-8x7b", "gemma2-9b-it"],
    },
    "grok": {
        "label": "Grok", "needs_key": True, "needs_model": True,
        "models": ["grok-3-beta"],
    },
    "xinference": {
        "label": "Xinference", "needs_key": False, "needs_model": True,
        "models": [],
    },
    "azure": {
        "label": "Azure OpenAI", "needs_key": True, "needs_model": True,
        "models": ["gpt-4o", "gpt-4o-mini"],
    },
    "qwenmt": {
        "label": "QwenMT", "needs_key": False, "needs_model": False,
        "models": [],
    },
    "claudecode": {
        "label": "Claude Code", "needs_key": True, "needs_model": True,
        "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"],
    },
}

LANGUAGE_OPTIONS = [
    ("en", "English"), ("zh", "中文"), ("ja", "日本語"), ("ko", "한국어"),
    ("fr", "Français"), ("de", "Deutsch"), ("es", "Español"), ("ru", "Русский"),
]

SETTINGS_ORG = "FormTransPDF"
SETTINGS_APP = "FormTransPDF"


# ═══════════════════════════════════════════════════════════════
# 设置面板
# ═══════════════════════════════════════════════════════════════

class SettingsPanel(QWidget):
    """翻译设置面板 — 支持持久化和模型下拉"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")
        self._qsettings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._build_ui()
        self._restore_settings()
        self._on_translator_changed()

    # ── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # ── 翻译服务 ──
        svc_group = QGroupBox("翻译引擎")
        svc_layout = QFormLayout(svc_group)
        svc_layout.setSpacing(6)
        svc_layout.setContentsMargins(8, 14, 8, 8)

        self._translator_combo = QComboBox()
        for key, meta in TRANSLATOR_OPTIONS.items():
            self._translator_combo.addItem(meta["label"], key)
        self._translator_combo.currentIndexChanged.connect(self._on_translator_changed)
        svc_layout.addRow("服务:", self._translator_combo)

        self._api_key_input = QLineEdit()
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.setPlaceholderText("sk-...")
        svc_layout.addRow("API Key:", self._api_key_input)

        # 模型：可编辑下拉框
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._model_combo.lineEdit().setPlaceholderText("输入或选择模型…")
        svc_layout.addRow("模型:", self._model_combo)

        self._base_url_input = QLineEdit()
        self._base_url_input.setPlaceholderText("留空使用默认")
        svc_layout.addRow("Base URL:", self._base_url_input)

        self._ollama_hint = QLabel("Ollama 无需 API Key；请确保服务已启动")
        self._ollama_hint.setStyleSheet(
            f"color: {Colors.AMBER.name()}; font-size: 9pt; padding: 2px 0;"
        )
        self._ollama_hint.setWordWrap(True)
        self._ollama_hint.setVisible(False)
        svc_layout.addRow(self._ollama_hint)

        root.addWidget(svc_group)

        # ── 分隔 ──
        sep1 = QLabel()
        sep1.setObjectName("sectionDivider")
        sep1.setStyleSheet(DIVIDER_STYLE)
        root.addWidget(sep1)

        # ── 语言 ──
        lang_group = QGroupBox("语言")
        lang_layout = QFormLayout(lang_group)
        lang_layout.setSpacing(6)
        lang_layout.setContentsMargins(8, 14, 8, 8)

        self._lang_in_combo = QComboBox()
        self._lang_out_combo = QComboBox()
        for code, name in LANGUAGE_OPTIONS:
            self._lang_in_combo.addItem(name, code)
            self._lang_out_combo.addItem(name, code)
        self._lang_in_combo.setCurrentText("English")
        self._lang_out_combo.setCurrentText("中文")

        lang_layout.addRow("源语言:", self._lang_in_combo)
        lang_layout.addRow("目标语言:", self._lang_out_combo)

        # 输出模式
        self._output_mode_combo = QComboBox()
        self._output_mode_combo.addItem("双语对照（原文+译文双栏）", "dual")
        self._output_mode_combo.addItem("仅译文（纯译文单栏）", "mono")
        lang_layout.addRow("输出模式:", self._output_mode_combo)

        root.addWidget(lang_group)

        # ── 操作 ──
        sep2 = QLabel()
        sep2.setObjectName("sectionDivider")
        sep2.setStyleSheet(DIVIDER_STYLE)
        root.addWidget(sep2)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(6)

        self._select_btn = QPushButton("📂 选择")
        self._select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_btn.setToolTip("选择 PDF 文件")
        action_layout.addWidget(self._select_btn)

        self._translate_btn = QPushButton("🚀 翻译")
        self._translate_btn.setObjectName("primaryBtn")
        self._translate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._translate_btn.setEnabled(False)
        action_layout.addWidget(self._translate_btn)

        root.addLayout(action_layout)

        # ── 状态 ──
        self._status_label = QLabel("就绪 — 请载入 PDF")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

    # ═══════════════════════════════════════════════════════════
    # 持久化
    # ═══════════════════════════════════════════════════════════

    def _restore_settings(self) -> None:
        """从 QSettings 恢复上次配置"""
        qs = self._qsettings

        translator = qs.value("translator", "openai")
        idx = self._translator_combo.findData(translator)
        if idx >= 0:
            self._translator_combo.setCurrentIndex(idx)

        self._api_key_input.setText(qs.value("api_key", ""))

        model = qs.value("model", "")
        if model:
            self._model_combo.setCurrentText(model)

        self._base_url_input.setText(qs.value("base_url", ""))

        lang_in = qs.value("lang_in", "en")
        idx = self._lang_in_combo.findData(lang_in)
        if idx >= 0:
            self._lang_in_combo.setCurrentIndex(idx)

        lang_out = qs.value("lang_out", "zh")
        idx = self._lang_out_combo.findData(lang_out)
        if idx >= 0:
            self._lang_out_combo.setCurrentIndex(idx)

        output_mode = qs.value("output_mode", "dual")
        idx = self._output_mode_combo.findData(output_mode)
        if idx >= 0:
            self._output_mode_combo.setCurrentIndex(idx)

    def save_settings(self) -> None:
        """持久化当前配置"""
        qs = self._qsettings
        qs.setValue("translator", self._translator_combo.currentData())
        qs.setValue("api_key", self._api_key_input.text())
        qs.setValue("model", self._model_combo.currentText())
        qs.setValue("base_url", self._base_url_input.text())
        qs.setValue("lang_in", self._lang_in_combo.currentData())
        qs.setValue("lang_out", self._lang_out_combo.currentData())
        qs.setValue("output_mode", self._output_mode_combo.currentData())

    # ═══════════════════════════════════════════════════════════
    # 公有接口
    # ═══════════════════════════════════════════════════════════

    @property
    def select_btn(self) -> QPushButton:
        return self._select_btn

    @property
    def translate_btn(self) -> QPushButton:
        return self._translate_btn

    def set_pdf_loaded(self, path: str, loaded: bool = True) -> None:
        self._translate_btn.setEnabled(loaded)
        if loaded:
            from pathlib import Path
            self._status_label.setText(f"已加载: {Path(path).name}")
            self._status_label.setStyleSheet(f"color: {Colors.MOSS.name()}; font-size: 10pt;")
        else:
            self._status_label.setText("就绪 — 请载入 PDF")
            self._status_label.setStyleSheet(f"color: {Colors.ASH.name()}; font-size: 10pt;")

    def set_translating(self, active: bool) -> None:
        self._translate_btn.setEnabled(not active)
        self._translate_btn.setText("⏳ 翻译中…" if active else "🚀 翻译")
        self._translator_combo.setEnabled(not active)
        self._lang_in_combo.setEnabled(not active)
        self._lang_out_combo.setEnabled(not active)
        self._select_btn.setEnabled(not active)

    def set_status(self, text: str, is_error: bool = False) -> None:
        color = Colors.EMBER.name() if is_error else Colors.ASH.name()
        self._status_label.setStyleSheet(f"color: {color}; font-size: 10pt;")
        self._status_label.setText(text)

    def build_task(self, pdf_path: str) -> TranslationTask:
        from pathlib import Path
        return TranslationTask(
            input_pdf=Path(pdf_path),
            lang_in=self._lang_in_combo.currentData(),
            lang_out=self._lang_out_combo.currentData(),
            translator=self._translator_combo.currentData(),
            api_key=self._api_key_input.text().strip(),
            model=self._model_combo.currentText().strip(),
            base_url=self._base_url_input.text().strip(),
            output_mode=self._output_mode_combo.currentData(),
        )

    # ═══════════════════════════════════════════════════════════
    # 槽
    # ═══════════════════════════════════════════════════════════

    def _on_translator_changed(self) -> None:
        key = self._translator_combo.currentData()
        meta = TRANSLATOR_OPTIONS.get(key, {})

        needs_key = meta.get("needs_key", True)
        needs_model = meta.get("needs_model", True)
        is_ollama = (key == "ollama")

        self._api_key_input.setVisible(needs_key)
        self._model_combo.setVisible(needs_model)
        self._ollama_hint.setVisible(is_ollama)

        # 更新模型下拉列表
        if needs_model:
            models = meta.get("models", [])
            current_text = self._model_combo.currentText()
            self._model_combo.clear()
            self._model_combo.addItems(models)
            if current_text:
                idx = self._model_combo.findText(current_text)
                if idx >= 0:
                    self._model_combo.setCurrentIndex(idx)
                else:
                    self._model_combo.setCurrentText(current_text)

