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

from .signals import TranslationEvent, TranslationTask, TranslationSignals

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
                from pdf2zh_next.high_level import (
                    BabelDOCConfig,
                    do_translate_async_stream,
                )
                self._config_manager = ConfigManager()
                self._do_translate_async_stream = do_translate_async_stream
                self._BabelDOCConfig = BabelDOCConfig
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

        # -- 翻译引擎设置 --
        ts = settings.translate_engine_settings
        svc = task.translator.lower()

        # 将服务名映射到设置字段前缀
        field_map: dict[str, str] = {
            "openai": "openai",
            "deepseek": "deepseek",
            "deepl": "deepl",
            "google": "google",
            "bing": "bing",
            "ollama": "ollama",
            "zhipu": "zhipu",
            "siliconflow": "siliconflow",
            "gemini": "gemini",
            "groq": "groq",
            "grok": "grok",
            "xinference": "xinference",
            "azure": "azure",
            "tencent": "tencent",
            "anythingllm": "anythingllm",
            "dify": "dify",
            "qwenmt": "qwenmt",
            "claudecode": "claudecode",
        }

        prefix = field_map.get(svc, "openai")

        # 动态设置属性（仅当值非空）
        if task.api_key:
            try:
                setattr(ts, f"{prefix}_api_key", task.api_key)
            except Exception:
                logger.debug("Setting %s_api_key failed — may not exist", prefix)

        if task.model:
            try:
                setattr(ts, f"{prefix}_model", task.model)
            except Exception:
                logger.debug("Setting %s_model failed — may not exist", prefix)

        if task.base_url:
            try:
                setattr(ts, f"{prefix}_base_url", task.base_url)
            except Exception:
                logger.debug("Setting %s_base_url failed — may not exist", prefix)

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
