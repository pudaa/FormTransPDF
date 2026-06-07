# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置 — FormTransPDF

使用方式:
    pip install pyinstaller
    pyinstaller FormTransPDF.spec

输出:
    dist/FormTransPDF/FormTransPDF.exe
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent

a = Analysis(
    [str(_root / "src" / "main.py")],
    pathex=[str(_root)],
    binaries=[],
    datas=[
        # 如有图标等资源，在此添加
        # (str(_root / "src" / "resources" / "icon.ico"), "resources"),
    ],
    hiddenimports=[
        "pdf2zh_next",
        "pdf2zh_next.config",
        "pdf2zh_next.config.model",
        "pdf2zh_next.high_level",
        "fitz",  # PyMuPDF
        "qasync",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FormTransPDF",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_root / "src" / "resources" / "icons" / "app.ico") if (_root / "src" / "resources" / "icons" / "app.ico").exists() else None,
)
