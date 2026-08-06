"""
翻译引擎封装 — 将 pdf2zh-next 的 async generator 包装为可注入 Qt 信号的形式。

重型依赖（pdf2zh_next / babeldoc，导入约 3-4 秒）采用延迟加载：
__init__ 仅做轻量初始化，真正的模块导入在 load() 中由后台线程完成，
避免阻塞应用启动。引擎就绪前调用翻译会抛出 EngineNotReadyError。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import AsyncIterator

from ..signals import TranslationEvent, TranslationTask, TranslationSignals

logger = logging.getLogger(__name__)


class EngineNotReadyError(RuntimeError):
    """翻译引擎尚未加载完成时调用翻译所引发的异常。"""


# ═══════════════════════════════════════════════════════════════
# Nuitka 兼容：预导入所有动态加载的 translator_impl 模块
# ═══════════════════════════════════════════════════════════════
# pdf2zh_next.translator.utils 使用 importlib.import_module()
# 动态加载 translator_impl 下的引擎模块。Nuitka 静态分析无法
# 发现这些动态导入，因此我们需要在启动时显式导入它们，确保 Nuitka
# 将其纳入编译，同时运行时也可通过 sys.modules 缓存直接命中。
#
# 这些导入在正常开发环境中无副作用（只是提前加载模块）。
# ═══════════════════════════════════════════════════════════════

def _pre_import_translator_impls() -> None:
    """预导入所有翻译引擎实现模块（Nuitka 兼容）。"""
    _modules = [
        "pdf2zh_next.translator.translator_impl.anythingllm",
        "pdf2zh_next.translator.translator_impl.azure",
        "pdf2zh_next.translator.translator_impl.azureopenai",
        "pdf2zh_next.translator.translator_impl.bing",
        "pdf2zh_next.translator.translator_impl.claudecode",
        "pdf2zh_next.translator.translator_impl.clitranslator",
        "pdf2zh_next.translator.translator_impl.deepl",
        "pdf2zh_next.translator.translator_impl.dify",
        "pdf2zh_next.translator.translator_impl.google",
        "pdf2zh_next.translator.translator_impl.ollama",
        "pdf2zh_next.translator.translator_impl.openai",
        "pdf2zh_next.translator.translator_impl.qwenmt",
        "pdf2zh_next.translator.translator_impl.siliconflow",
        "pdf2zh_next.translator.translator_impl.siliconflowfree",
        "pdf2zh_next.translator.translator_impl.tencentmechinetranslation",
        "pdf2zh_next.translator.translator_impl.xinference",
    ]
    import importlib
    for mod_name in _modules:
        try:
            importlib.import_module(mod_name)
        except Exception:
            logger.debug("Pre-import of %s failed (not critical)", mod_name)


# ── 翻译引擎规格：app translator key → (pdf2zh_next 引擎类型, {设置字段: task 字段}) ──
# 字段名取自 pdf2zh_next.config.translate_engine_model 中各引擎设置类的实际字段。
# 注意：pdf2zh_next 通过 translate_engine_type 判别字段实例化对应翻译器，
# 必须为所选引擎创建对应类型的设置对象并整体替换，否则会静默回退到默认的
# SiliconFlowFree 引擎（此前的 setattr 方式就是这样失效的）。
_ENGINE_SPECS: dict[str, tuple[str, dict[str, str]]] = {
    "openai": (
        "OpenAI",
        {
            "openai_model": "model",
            "openai_api_key": "api_key",
            "openai_base_url": "base_url",
        },
    ),
    "deepseek": (
        "DeepSeek",
        {"deepseek_model": "model", "deepseek_api_key": "api_key"},
    ),
    "deepl": ("DeepL", {"deepl_auth_key": "api_key"}),
    "google": ("Google", {}),
    "bing": ("Bing", {}),
    # Ollama 使用 ollama_host（不是 *_base_url）
    "ollama": (
        "Ollama",
        {"ollama_model": "model", "ollama_host": "base_url"},
    ),
    "zhipu": (
        "Zhipu",
        {"zhipu_model": "model", "zhipu_api_key": "api_key"},
    ),
    "siliconflow": (
        "SiliconFlow",
        {
            "siliconflow_model": "model",
            "siliconflow_api_key": "api_key",
            "siliconflow_base_url": "base_url",
        },
    ),
    "gemini": (
        "Gemini",
        {"gemini_model": "model", "gemini_api_key": "api_key"},
    ),
    "groq": (
        "Groq",
        {"groq_model": "model", "groq_api_key": "api_key"},
    ),
    "grok": (
        "Grok",
        {"grok_model": "model", "grok_api_key": "api_key"},
    ),
    "xinference": (
        "Xinference",
        {"xinference_model": "model", "xinference_host": "base_url"},
    ),
    # 界面上 label 为 “Azure OpenAI”，对应 AzureOpenAI 引擎
    "azure": (
        "AzureOpenAI",
        {
            "azure_openai_model": "model",
            "azure_openai_api_key": "api_key",
            "azure_openai_base_url": "base_url",
        },
    ),
    "qwenmt": (
        "QwenMt",
        {
            "qwenmt_model": "model",
            "qwenmt_api_key": "api_key",
            "qwenmt_base_url": "base_url",
        },
    ),
    "claudecode": (
        "ClaudeCode",
        {"claude_code_model": "model"},
    ),
}


class TranslationEngine:
    """
    封装 pdf2zh-next 翻译流水线（重型依赖延迟加载）。

    pdf2zh_next / babeldoc 导入耗时约 3-4 秒，且仅在真正需要翻译时才有用。
    因此 __init__ 只做轻量初始化；真正的模块导入在 load() 中完成，
    由主窗口在后台线程调用，避免阻塞应用启动。引擎就绪前调用翻译会抛出
    EngineNotReadyError。

    usage::

        engine = TranslationEngine()
        engine.load()          # 后台线程中调用
        async for event in engine.run(task):
            signals.progress.emit(event)
    """

    def __init__(self) -> None:
        self._ready = False
        self._load_error: Exception | None = None
        self._lock = threading.Lock()
        # 延迟加载的引用（load() 中填充）
        self._config_manager = None
        self._do_translate_async_stream = None
        self._BabelDOCConfig = None
        self._engine_metadata_map = None

    # ── 加载与状态 ──────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        """引擎是否已加载完成。"""
        return self._ready

    @property
    def load_error(self) -> Exception | None:
        """最近一次加载失败的原因（未失败为 None）。"""
        return self._load_error

    def load(self) -> None:
        """导入 pdf2zh_next / babeldoc 并预加载 translator_impl。

        耗时约 3-4 秒，应由后台线程调用；重复调用幂等。
        """
        with self._lock:
            if self._ready:
                return
            try:
                from pdf2zh_next.config import ConfigManager
                from pdf2zh_next.config.translate_engine_model import (
                    TRANSLATION_ENGINE_METADATA_MAP,
                )
                from pdf2zh_next.high_level import (
                    BabelDOCConfig,
                    do_translate_async_stream,
                )
                self._config_manager = ConfigManager()
                self._do_translate_async_stream = do_translate_async_stream
                self._BabelDOCConfig = BabelDOCConfig
                self._engine_metadata_map = TRANSLATION_ENGINE_METADATA_MAP
                # Nuitka 兼容：预导入动态加载的 translator_impl 模块
                _pre_import_translator_impls()
                self._ready = True
                self._load_error = None
                logger.info("Translation engine loaded")
            except Exception as exc:
                self._load_error = exc
                logger.exception("Translation engine load failed")
                raise

    def _ensure_loaded(self) -> None:
        """确保引擎已加载；否则抛出 EngineNotReadyError。"""
        if not self._ready:
            raise EngineNotReadyError("翻译引擎尚未加载完成，请稍候再试。")

    # ------------------------------------------------------------------
    def build_settings(self, task: TranslationTask, output_dir: Path | None = None):
        """从 TranslationTask 构建 SettingsModel"""
        self._ensure_loaded()
        settings = self._config_manager.initialize_config()

        # -- 输出目录（避免散落到项目根目录）--
        if output_dir is not None:
            settings.translation.output = str(output_dir.resolve())

        # -- 语言设置 --
        settings.translation.lang_in = task.lang_in
        settings.translation.lang_out = task.lang_out

        # -- 性能优化：关闭自动术语提取，避免 LLM JSON 解析错误带来的重试开销 --
        #    自动术语提取在控制台输出中频繁报错（JSON 解析失败），
        #    每次错误都会触发 fallback 重试，拖慢整体翻译速度。
        #    如需术语表，可后期通过专门工具生成。
        settings.translation.no_auto_extract_glossary = True

        # -- 扫描件处理：检测到大量"扫描页"时自动启用 OCR workaround --
        #    babeldoc 的扫描检测会把"带整页背景图 + 文本层"的 PDF（Zotero
        #    来源的论文很常见）误判为扫描件；默认会直接抛
        #    "Scanned PDF detected." 错误终止翻译。
        #    开启该选项后，检测到大量扫描页时 babeldoc 会自动切换为
        #    OCR workaround 模式（黑字白底 + 跳过富文本翻译）继续处理，
        #    而不是报错终止。对正常 PDF 无任何影响（仅在误判时生效）。
        settings.pdf.auto_enable_ocr_workaround = True

        # -- 翻译引擎设置 --
        # 关键点：pdf2zh_next 的 translate_engine_settings 是带判别字段
        # (translate_engine_type) 的联合类型，必须为所选引擎创建**对应类型**的
        # 设置对象并整体替换。此前代码只是在默认的 SiliconFlowFreeSettings
        # 对象上 setattr（如 ollama_model），字段不存在 → 静默失败，
        # 导致无论选择什么引擎都回退到 SiliconFlowFree。
        svc = task.translator.lower()
        spec = _ENGINE_SPECS.get(svc)
        if spec is not None and self._engine_metadata_map is not None:
            engine_type, engine_fields = spec
            metadata = self._engine_metadata_map.get(engine_type)
            if metadata is not None:
                new_ts = metadata.setting_model_type()
                task_values = {
                    "model": task.model,
                    "api_key": task.api_key,
                    "base_url": task.base_url,
                }
                for field_name, task_attr in engine_fields.items():
                    value = task_values.get(task_attr)
                    if value:
                        try:
                            setattr(new_ts, field_name, value)
                        except Exception:
                            logger.debug(
                                "Setting %s on %s failed — may not exist",
                                field_name,
                                engine_type,
                            )
                settings.translate_engine_settings = new_ts
                logger.info(
                    "Translation engine: %s (model=%s, base_url=%s)",
                    engine_type,
                    task.model or "(default)",
                    task.base_url or "(default)",
                )

        return settings

    # ------------------------------------------------------------------
    async def run(
        self, task: TranslationTask, signals: TranslationSignals | None = None,
        output_dir: Path | None = None,
    ) -> AsyncIterator[TranslationEvent]:
        """
        执行翻译，逐事件 yield。

        :param task: 翻译任务参数
        :param signals: 可选的 Qt 信号集
        :param output_dir: 输出目录（默认 pdf2zh-next 使用当前 CWD）
        """
        self._ensure_loaded()
        settings = self.build_settings(task, output_dir=output_dir)

        try:
            async for raw_event in self._do_translate_async_stream(settings, task.input_pdf):
                event_type = raw_event.get("type", "")

                # ── babeldoc 新版事件：progress_start / progress_update / progress_end ──
                if event_type in ("progress_start", "progress_update", "progress_end"):
                    overall = raw_event.get("overall_progress", 0.0)
                    stage = raw_event.get("stage", "")
                    stage_current = raw_event.get("stage_current", 0)
                    stage_total = raw_event.get("stage_total", 0)

                    ev = TranslationEvent(
                        event_type="progress",
                        current=int(overall),
                        total=100,
                        message=f"翻译中… {stage} ({stage_current}/{stage_total}) — {overall:.0f}%",
                    )
                    if signals:
                        signals.progress.emit(ev)
                    yield ev

                # ── 兼容旧版 progress 事件（n / total）──────────────────────
                elif event_type == "progress":
                    ev = TranslationEvent(
                        event_type="progress",
                        current=raw_event.get("n", 0),
                        total=raw_event.get("total", 100),
                        message=f"翻译中… {raw_event.get('n', 0)}/{raw_event.get('total', 100)}",
                    )
                    if signals:
                        signals.progress.emit(ev)
                    yield ev

                elif event_type == "finish":
                    result = raw_event.get("translate_result")
                    ev = TranslationEvent(
                        event_type="finish",
                        current=raw_event.get("total", 100),
                        total=raw_event.get("total", 100),
                        message="翻译完成",
                        mono_pdf_path=(
                            Path(result.mono_pdf_path)
                            if getattr(result, "mono_pdf_path", None)
                            else None
                        ),
                        dual_pdf_path=(
                            Path(result.dual_pdf_path)
                            if getattr(result, "dual_pdf_path", None)
                            else None
                        ),
                        elapsed_seconds=getattr(result, "total_seconds", 0.0),
                    )
                    if signals:
                        signals.finished.emit(ev)
                    yield ev

                elif event_type == "error":
                    ev = TranslationEvent(
                        event_type="error",
                        message=raw_event.get("error", "未知错误"),
                        error_details=raw_event.get("details", ""),
                    )
                    if signals:
                        signals.error_occurred.emit(ev)
                    yield ev

        except Exception as exc:
            ev = TranslationEvent(
                event_type="error",
                message=str(exc),
                error_details=type(exc).__name__,
            )
            if signals:
                signals.error_occurred.emit(ev)
            yield ev
