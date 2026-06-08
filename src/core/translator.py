"""
翻译引擎封装 — 将 pdf2zh-next 的 async generator 包装为可注入 Qt 信号的形式
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator

from pdf2zh_next.config import ConfigManager
from pdf2zh_next.config.model import SettingsModel
from pdf2zh_next.high_level import do_translate_async_stream, BabelDOCConfig

from .signals import TranslationEvent, TranslationTask, TranslationSignals

logger = logging.getLogger(__name__)


class TranslationEngine:
    """
    封装 pdf2zh-next 翻译流水线。

    usage::

        engine = TranslationEngine()
        async for event in engine.run(task):
            signals.progress.emit(event)
    """

    def __init__(self) -> None:
        self._config_manager = ConfigManager()

    # ------------------------------------------------------------------
    def build_settings(self, task: TranslationTask, output_dir: Path | None = None) -> SettingsModel:
        """从 TranslationTask 构建 SettingsModel"""
        settings = self._config_manager.initialize_config()

        # -- 输出目录（避免散落到项目根目录）--
        if output_dir is not None:
            settings.translation.output = str(output_dir.resolve())

        # -- 语言设置 --
        settings.translation.lang_in = task.lang_in
        settings.translation.lang_out = task.lang_out

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
        settings = self.build_settings(task, output_dir=output_dir)

        try:
            async for raw_event in do_translate_async_stream(settings, task.input_pdf):
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
