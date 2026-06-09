#!/usr/bin/env python3
"""
FormTransPDF — PDF 科学论文翻译查看器

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


def setup_logging() -> None:
    """配置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    """应用入口"""
    setup_logging()
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
