"""
异步翻译信号 — 桥接 pdf2zh-next 的 async generator 与 Qt 信号槽
"""

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal


@dataclass
class TranslationEvent:
    """翻译过程中产生的事件"""
    event_type: str  # "progress" | "finish" | "error"
    current: int = 0
    total: int = 0
    message: str = ""
    mono_pdf_path: Path | None = None
    dual_pdf_path: Path | None = None
    elapsed_seconds: float = 0.0
    error_details: str = ""


@dataclass
class TranslationTask:
    """一次翻译任务的完整参数"""
    input_pdf: Path
    lang_in: str = "en"
    lang_out: str = "zh"
    translator: str = "openai"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    base_url: str = ""
    output_mode: str = "dual"  # "dual" = 双语对照, "mono" = 仅译文


class TranslationSignals(QObject):
    """
    翻译过程的 Qt 信号集合。

    usage::

        signals = TranslationSignals()
        signals.progress.connect(my_slot)
        signals.finished.connect(my_slot)
        signals.error_occurred.connect(my_slot)
    """

    progress = Signal(TranslationEvent)
    finished = Signal(TranslationEvent)
    error_occurred = Signal(TranslationEvent)
