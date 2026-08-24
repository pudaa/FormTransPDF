"""pytest 全局配置：项目路径 + Qt 离屏平台 + 共享 QApplication。"""

import os
import sys
from pathlib import Path

# 项目根目录加入 sys.path（tests/ 的上一级）
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 无显示环境下使用离屏平台（CI / 无头终端）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """会话级 QApplication（TextOverlay 等控件测试依赖）。"""
    app = QApplication.instance() or QApplication([])
    yield app
