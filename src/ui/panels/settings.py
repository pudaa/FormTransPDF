"""
翻译设置面板 — 服务选择、API Key、语言配置 + 持久化 + 模型下拉
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSettings, QThread, Signal
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
from src.ui.base.icon_factory import accent_icon, svg_icon
from src.ui.base.theme import Colors, DIVIDER_STYLE, _contrast_text, theme_manager

# ═══════════════════════════════════════════════════════════════
# 翻译服务元数据 + 常用模型列表
# ═══════════════════════════════════════════════════════════════

TRANSLATOR_OPTIONS: dict[str, dict] = {
    "openai": {
        "label": "OpenAI", "needs_key": True, "needs_model": True,
        # 2026-08: GPT-5.6 家族 GA；luna 最便宜，适合翻译场景
        "models": ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6", "gpt-5.5",
                   "gpt-5.4-mini", "gpt-5.2", "gpt-5-mini", "gpt-4.1",
                   "gpt-4o", "gpt-4o-mini"],
    },
    "deepseek": {
        "label": "DeepSeek", "needs_key": True, "needs_model": True,
        # 2026-07-24 起 deepseek-chat / deepseek-reasoner 已退役，仅剩 V4 两档
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
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
        # 2026-08 主流本地模型（name:tag 格式，可按需 ollama pull）
        "models": ["qwen3:8b", "qwen3:14b", "llama3.3:70b", "gemma3:27b",
                   "gpt-oss:20b", "qwen2.5:7b", "deepseek-r1:8b"],
    },
    "zhipu": {
        "label": "智谱 GLM", "needs_key": True, "needs_model": True,
        # 2026-06 GLM-5.2 开源上线；glm-4-flash 仍为免费档，翻译性价比高
        "models": ["glm-5.2", "glm-5.1", "glm-5", "glm-4.7", "glm-4.6",
                   "glm-4-flash", "glm-4-air", "glm-4-plus"],
    },
    "siliconflow": {
        "label": "SiliconFlow", "needs_key": True, "needs_model": True,
        "models": ["Qwen/Qwen2.5-7B-Instruct", "deepseek-ai/DeepSeek-V3",
                   "Qwen/Qwen3-8B", "Qwen/Qwen3-14B"],
    },
    "gemini": {
        "label": "Gemini", "needs_key": True, "needs_model": True,
        # 2026-08: 3.6 flash 为最新稳定版；flash-lite 最便宜
        "models": ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite",
                   "gemini-3.1-pro-preview", "gemini-3-flash-preview",
                   "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
    },
    "groq": {
        "label": "Groq", "needs_key": True, "needs_model": True,
        "models": ["llama-3.3-70b-versatile", "llama-4-scout-17b-16e-instruct",
                   "qwen/qwen3-32b", "openai/gpt-oss-120b", "llama-3.1-8b-instant"],
    },
    "grok": {
        "label": "Grok", "needs_key": True, "needs_model": True,
        # 2026-07 grok-4.5 上线；grok-3 已退役
        "models": ["grok-4.5", "grok-4.3"],
    },
    "xinference": {
        "label": "Xinference", "needs_key": False, "needs_model": True,
        "models": [],
    },
    "azure": {
        "label": "Azure OpenAI", "needs_key": True, "needs_model": True,
        "models": ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.4", "gpt-4o", "gpt-4o-mini"],
    },
    "qwenmt": {
        "label": "QwenMT", "needs_key": False, "needs_model": False,
        "models": [],
    },
    "claudecode": {
        "label": "Claude Code", "needs_key": True, "needs_model": True,
        # 2026-06-15 起 claude-*-20250514 已退役，迁移到 Sonnet 5 / Opus 4.8
        "models": ["claude-sonnet-5", "claude-opus-4-8", "claude-opus-4-7",
                   "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    },
}

LANGUAGE_OPTIONS = [
    ("en", "English"), ("zh", "中文"), ("ja", "日本語"), ("ko", "한국어"),
    ("fr", "Français"), ("de", "Deutsch"), ("es", "Español"), ("ru", "Русский"),
]

# ═══════════════════════════════════════════════════════════════
# 模型列表刷新（后台拉取真实可用模型）
# ═══════════════════════════════════════════════════════════════

# 支持"刷新模型列表"的服务：translator key -> (默认 base_url, 是否 OpenAI 兼容)
_REFRESHABLE: dict[str, tuple[str | None, bool]] = {
    "openai": ("https://api.openai.com/v1", True),
    "deepseek": ("https://api.deepseek.com/v1", True),
    "siliconflow": ("https://api.siliconflow.cn/v1", True),
    "groq": ("https://api.groq.com/openai/v1", True),
    "grok": ("https://api.x.ai/v1", True),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", True),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta", False),
    "ollama": ("http://localhost:11434", False),
}

MODEL_CACHE_KEY_PREFIX = "model_cache_"


class ModelFetcher(QThread):
    """后台线程：拉取指定翻译服务的可用模型 ID 列表。

    - OpenAI 兼容服务：GET {base}/v1/models → data[].id
    - Ollama：GET {base}/api/tags → models[].name
    - Gemini：GET {base}/models?key=... → models[].name（去掉 models/ 前缀）

    失败不抛异常，通过 finished_err 信号回传错误信息。
    """

    finished_ok = Signal(str, list)  # (translator_key, model_ids)
    finished_err = Signal(str, str)  # (translator_key, error_message)

    def __init__(
        self, translator: str, api_key: str, base_url: str, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._api_key = api_key
        self._base_url = base_url

    def run(self) -> None:
        try:
            ids = self._fetch()
            if ids:
                self.finished_ok.emit(self._translator, ids)
            else:
                self.finished_err.emit(self._translator, "服务端未返回任何模型")
        except Exception as exc:  # noqa: BLE001 — 后台线程错误统一走信号
            self.finished_err.emit(self._translator, str(exc))

    def _fetch(self) -> list[str]:
        import json
        import urllib.request

        key = self._translator

        # ── Ollama：/api/tags ──
        if key == "ollama":
            base = (self._base_url or "http://localhost:11434").rstrip("/")
            req = urllib.request.Request(base + "/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            return [m.get("name", "") for m in payload.get("models", []) if m.get("name")]

        # ── Gemini：{base}/models?key=... ──
        if key == "gemini":
            base = (
                self._base_url
                or "https://generativelanguage.googleapis.com/v1beta"
            ).rstrip("/")
            sep = "&" if "?" in base else "?"
            url = f"{base}/models{sep}key={self._api_key or ''}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            return [
                m.get("name", "").removeprefix("models/")
                for m in payload.get("models", [])
                if m.get("name")
            ]

        # ── OpenAI 兼容：{base}/v1/models ──
        default_base = _REFRESHABLE.get(key, (None, True))[0]
        base = (self._base_url or default_base or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("缺少 Base URL，无法获取模型列表")
        if base.endswith("/v1") or base.endswith("/v4"):  # OpenAI / 智谱
            url = base + "/models"
        else:
            url = base + "/v1/models"
        headers = {"Authorization": f"Bearer {self._api_key or ''}"}
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return [m.get("id", "") for m in payload.get("data", []) if m.get("id")]

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
        self._loading_settings = True  # 恢复期间抑制自动保存
        self._build_ui()
        self._restore_settings()
        self._loading_settings = False
        self._on_translator_changed()

    # ── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 4, 10, 6)
        root.setSpacing(6)

        # ── 翻译服务 ──
        svc_group = QGroupBox("翻译引擎")
        svc_layout = QFormLayout(svc_group)
        svc_layout.setSpacing(6)
        svc_layout.setContentsMargins(8, 4, 8, 6)

        self._translator_combo = QComboBox()
        for key, meta in TRANSLATOR_OPTIONS.items():
            self._translator_combo.addItem(meta["label"], key)
        self._translator_combo.currentIndexChanged.connect(self._on_translator_changed)
        self._translator_combo.currentIndexChanged.connect(self._auto_save)
        svc_layout.addRow("服务:", self._translator_combo)

        self._api_key_input = QLineEdit()
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.setPlaceholderText("sk-...")
        self._api_key_input.textChanged.connect(self._auto_save)
        svc_layout.addRow("API Key:", self._api_key_input)

        # 模型：可编辑下拉框 + 刷新按钮
        model_row = QWidget()
        model_row_layout = QHBoxLayout(model_row)
        model_row_layout.setContentsMargins(0, 0, 0, 0)
        model_row_layout.setSpacing(4)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._model_combo.lineEdit().setPlaceholderText("输入或选择模型…")
        self._model_combo.currentTextChanged.connect(self._auto_save)
        model_row_layout.addWidget(self._model_combo, 1)

        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.setFixedWidth(52)
        self._refresh_btn.setToolTip("从服务端拉取最新可用模型列表（需已填写 API Key）")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._on_refresh_models)
        model_row_layout.addWidget(self._refresh_btn)

        svc_layout.addRow("模型:", model_row)

        self._base_url_input = QLineEdit()
        self._base_url_input.setPlaceholderText("留空使用默认")
        self._base_url_input.textChanged.connect(self._auto_save)
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
        lang_layout.setContentsMargins(8, 4, 8, 6)

        self._lang_in_combo = QComboBox()
        self._lang_out_combo = QComboBox()
        for code, name in LANGUAGE_OPTIONS:
            self._lang_in_combo.addItem(name, code)
            self._lang_out_combo.addItem(name, code)
        self._lang_in_combo.setCurrentText("English")
        self._lang_out_combo.setCurrentText("中文")
        self._lang_in_combo.currentIndexChanged.connect(self._auto_save)
        self._lang_out_combo.currentIndexChanged.connect(self._auto_save)

        lang_layout.addRow("源语言:", self._lang_in_combo)
        lang_layout.addRow("目标语言:", self._lang_out_combo)

        # 输出模式（同时决定 BabelDoc 精确翻译的呈现形式与粗糙翻译的查看器布局）
        self._output_mode_combo = QComboBox()
        self._output_mode_combo.addItem("双语对照（原文+译文双栏）", "dual")
        self._output_mode_combo.addItem("纯译文（译文单栏）", "mono")
        self._output_mode_combo.currentIndexChanged.connect(self._auto_save)
        lang_layout.addRow("输出模式:", self._output_mode_combo)

        root.addWidget(lang_group)

        # ── 操作 ──
        sep2 = QLabel()
        sep2.setObjectName("sectionDivider")
        sep2.setStyleSheet(DIVIDER_STYLE)
        root.addWidget(sep2)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(6)

        self._select_btn = QPushButton(" 选择")
        self._select_btn.setIcon(accent_icon("file", 16))
        self._select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_btn.setToolTip("选择 PDF 文件")
        action_layout.addWidget(self._select_btn)

        self._translate_btn = QPushButton(" 翻译")
        self._translate_btn.setObjectName("primaryBtn")
        self._translate_btn.setIcon(
            svg_icon("translate", _contrast_text(theme_manager.palette.accent), 16)
        )
        self._translate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._translate_btn.setEnabled(False)
        action_layout.addWidget(self._translate_btn)

        root.addLayout(action_layout)

        # ── 状态 ──
        self._status_label = QLabel("就绪 — 请载入 PDF")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setStyleSheet(f"background: transparent;")
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

        output_mode = qs.value("output_mode", None)
        if output_mode is None:
            # 兼容旧版本：粗糙翻译布局曾独立存储，现统一归并到「输出模式」
            output_mode = qs.value("rough_layout", "dual")
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
        qs.sync()  # 立即落盘，防止异常退出丢失设置

    def _auto_save(self, *_args) -> None:
        """设置变更时自动持久化（恢复期间不触发）。"""
        if getattr(self, "_loading_settings", False):
            return
        self.save_settings()

    # ═══════════════════════════════════════════════════════════
    # 公有接口
    # ═══════════════════════════════════════════════════════════

    @property
    def select_btn(self) -> QPushButton:
        return self._select_btn

    @property
    def translate_btn(self) -> QPushButton:
        return self._translate_btn

    @property
    def output_mode_combo(self) -> QComboBox:
        return self._output_mode_combo

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
        self._translate_btn.setText("翻译中…" if active else "翻译")
        self._translator_combo.setEnabled(not active)
        self._lang_in_combo.setEnabled(not active)
        self._lang_out_combo.setEnabled(not active)
        self._select_btn.setEnabled(not active)
        self._refresh_btn.setEnabled(not active)

    def set_status(self, text: str, is_error: bool = False) -> None:
        color = Colors.EMBER.name() if is_error else Colors.ASH.name()
        self._status_label.setStyleSheet(f"color: {color}; font-size: 10pt;")
        self._status_label.setText(text)

    def refresh_theme(self) -> None:
        """主题切换后刷新操作按钮图标颜色"""
        tp = theme_manager.palette
        self._select_btn.setIcon(accent_icon("file", 16))
        self._translate_btn.setIcon(
            svg_icon("translate", _contrast_text(tp.accent), 16)
        )

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

    def translation_profile(self) -> dict[str, str]:
        """导出当前翻译配置，供即时翻译窗口复用。"""
        return {
            "translator": str(self._translator_combo.currentData() or "openai"),
            "api_key": self._api_key_input.text().strip(),
            "model": self._model_combo.currentText().strip(),
            "base_url": self._base_url_input.text().strip(),
            "lang_in": str(self._lang_in_combo.currentData() or "en"),
            "lang_out": str(self._lang_out_combo.currentData() or "zh"),
        }

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

        # 刷新按钮：仅对支持模型列表接口的服务显示
        self._refresh_btn.setVisible(needs_model and key in _REFRESHABLE)

        # 更新模型下拉列表（内置建议 + 历史缓存合并，去重）
        if needs_model:
            models = list(meta.get("models", []))
            for m in self._load_model_cache(key):
                if m not in models:
                    models.append(m)
            current_text = self._model_combo.currentText()
            self._model_combo.clear()
            self._model_combo.addItems(models)
            if current_text:
                idx = self._model_combo.findText(current_text)
                if idx >= 0:
                    self._model_combo.setCurrentIndex(idx)
                else:
                    self._model_combo.setCurrentText(current_text)

    # ═══════════════════════════════════════════════════════════
    # 模型列表刷新（后台拉取）
    # ═══════════════════════════════════════════════════════════

    def _on_refresh_models(self) -> None:
        key = self._translator_combo.currentData()
        if key not in _REFRESHABLE or not self._model_combo.isVisible():
            return
        api_key = self._api_key_input.text().strip()
        if key != "ollama" and not api_key:
            self.set_status("请先填写 API Key，再刷新模型列表", is_error=True)
            return
        base_url = self._base_url_input.text().strip()
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("获取中…")
        self.set_status(f"正在获取 {key} 可用模型…")
        self._fetcher = ModelFetcher(key, api_key, base_url, self)
        self._fetcher.finished_ok.connect(self._on_refresh_done)
        self._fetcher.finished_err.connect(self._on_refresh_failed)
        self._fetcher.finished.connect(self._fetcher.deleteLater)
        self._fetcher.start()

    def _on_refresh_done(self, key: str, ids: list[str]) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("刷新")
        # 内置建议优先，拉取结果去重追加，并持久化缓存
        meta = TRANSLATOR_OPTIONS.get(key, {})
        merged = list(meta.get("models", []))
        for m in ids:
            if m not in merged:
                merged.append(m)
        self._save_model_cache(key, merged)
        # 目标服务正是当前服务时，实时刷新下拉
        if key == self._translator_combo.currentData() and self._model_combo.isVisible():
            current_text = self._model_combo.currentText()
            self._model_combo.clear()
            self._model_combo.addItems(merged)
            if current_text:
                idx = self._model_combo.findText(current_text)
                if idx >= 0:
                    self._model_combo.setCurrentIndex(idx)
                else:
                    self._model_combo.setCurrentText(current_text)
        self.set_status(f"模型列表已更新（共 {len(ids)} 个服务端模型）")

    def _on_refresh_failed(self, key: str, err: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("刷新")
        self.set_status(f"获取模型列表失败：{err}（继续使用内置列表）", is_error=True)

    def _load_model_cache(self, key: str) -> list[str]:
        """读取 QSettings 中缓存的模型列表（解析失败返回空）。"""
        import json
        raw = self._qsettings.value(MODEL_CACHE_KEY_PREFIX + key, "")
        if not raw:
            return []
        try:
            data = json.loads(str(raw))
            return [str(x) for x in data] if isinstance(data, list) else []
        except Exception:  # noqa: BLE001 — 缓存损坏时静默忽略
            return []

    def _save_model_cache(self, key: str, models: list[str]) -> None:
        import json
        self._qsettings.setValue(
            MODEL_CACHE_KEY_PREFIX + key, json.dumps(models, ensure_ascii=False)
        )

