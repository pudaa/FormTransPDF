#!/usr/bin/env python3
"""
FormTransPDF — PDF 论文翻译查看器

基于 pdf2zh-next（BabelDOC）和 PySide6 构建。
"Gilded Ink" 美学 — 左右分栏 + 金脊线设计。

usage:
    python -m src.main
    # 或
    python src/main.py
"""

from __future__ import annotations

import logging
import multiprocessing
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _is_frozen() -> bool:
    """检测打包模式（PyInstaller / Nuitka）"""
    return getattr(sys, "frozen", False) or hasattr(sys, "__compiled__")


def _log_dir() -> Path:
    """日志目录：开发=项目根；打包=用户目录（始终可写，与 output 同目录策略）"""
    if _is_frozen():
        base = Path.home() / "FormTransPDF"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return _PROJECT_ROOT


def setup_logging() -> None:
    """配置日志：开发=控制台；打包=控制台 + 日志文件（无控制台时仍可诊断）"""
    handlers: list = [logging.StreamHandler()]
    if _is_frozen():
        try:
            handlers.append(
                logging.FileHandler(
                    _log_dir() / "FormTransPDF.log", encoding="utf-8"
                )
            )
        except OSError:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def install_excepthook() -> None:
    """未捕获异常：记录日志；打包窗口程序（无控制台）时弹窗提示，避免静默崩溃。"""

    def _hook(exc_type, exc_value, exc_tb) -> None:
        logging.critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        if exc_type is not KeyboardInterrupt:
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox
                if QApplication.instance() is not None:
                    QMessageBox.critical(
                        None,
                        "程序错误",
                        f"发生未预期的错误：\n\n{exc_value}\n\n详情已写入日志文件。",
                    )
            except Exception:
                pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def main() -> int:
    """应用入口"""
    setup_logging()
    install_excepthook()
    logger = logging.getLogger(__name__)
    logger.info("FormTransPDF starting …")

    from src.app import FormTransPDFApp

    app = FormTransPDFApp(sys.argv)
    return app.run()


if __name__ == "__main__":
    # Windows + PyInstaller 下 multiprocessing 用 spawn 模式，
    # 子进程会 re-launch EXE 并传入 --multiprocessing-fork。
    # freeze_support() 必须在任何其他代码之前调用，确保子进程正确初始化。
    multiprocessing.freeze_support()
    sys.exit(main())
