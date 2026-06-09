# -*- mode: python ; coding: utf-8 -*-
"""
FormTransPDF — PyInstaller 打包配置

打包命令:
    pyinstaller FormTransPDF.spec
    # 打包为单文件
    # → dist/FormTransPDF/FormTransPDF.exe 
    # 打包带有命令行用于调试
    pyinstaller FormTransPDF.spec --console

策略: --onedir 模式，最大化兼容性，不压缩体积。
      通过 collect-all 确保所有子模块、数据文件、插件均被包含。
"""

import sys
import os
import glob
from pathlib import Path

# ── 项目根目录 ──────────────────────────────────────────
_PROJECT_ROOT = Path(SPECPATH).resolve()

# ── 需要完整收集的包 ─────────────────────────────────────
_COLLECT_ALL_PACKAGES = [
    "pdf2zh_next",
    "babeldoc",
    "PySide6",
    "bitstring",
    "hyperscan",
    "tiktoken",         # pkgutil.iter_modules 动态发现 tiktoken_ext 命名空间插件
    "tiktoken_ext",     # tiktoken 的编码插件命名空间包（独立于 tiktoken）
]

# ── 基础隐藏导入 ────────────────────────────────────────
_HIDDEN_IMPORTS = [
    # multiprocessing（pdf2zh-next 子进程翻译）
    "multiprocessing",
    "multiprocessing.pool",
    "multiprocessing.popen_spawn_win32",
    # PySide6 QtPdf（QtPdf 插件）
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
]

# ── 应用自身数据文件 ────────────────────────────────────
_DATAS = [
    (
        str(_PROJECT_ROOT / "src" / "resources" / "icons" / "app.ico"),
        "src/resources/icons",
    ),
]

# ═══════════════════════════════════════════════════════════
# ① 先执行 collect-all，将所有 hidden import / data 收集齐全
#    （必须在 Analysis() 之前，否则 a.pure 已冻结，新模块不进入 PYZ）
# ═══════════════════════════════════════════════════════════

_all_hidden = list(_HIDDEN_IMPORTS)
_all_datas = list(_DATAS)
_all_binaries = []

for pkg in _COLLECT_ALL_PACKAGES:
    try:
        from PyInstaller.utils.hooks import collect_all as _collect_all
        datas, binaries, hiddenimports = _collect_all(pkg)
        _all_datas.extend(datas)
        _all_binaries.extend(binaries)
        _all_hidden.extend(hiddenimports)
        print(f"  [OK] collect-all: {pkg} "
              f"({len(datas)} data, {len(binaries)} bin, "
              f"{len(hiddenimports)} imports)")
    except Exception as exc:
        print(f"  [WARN] collect-all failed for {pkg}: {exc}")

# ── 手动收集 delvewheel .libs 下的 DLL ────────────────────
#    PyInstaller 对 numpy/pandas/scipy 有内置 hook，但 hyperscan 没有。
#    用 sys.prefix 而非 site.getsitepackages()（后者在 exec() 中不可靠）。
_site_packages = os.path.join(sys.prefix, 'Lib', 'site-packages')

for _libs_name in os.listdir(_site_packages):
    if not _libs_name.endswith('.libs'):
        continue
    _libs_path = os.path.join(_site_packages, _libs_name)
    if not os.path.isdir(_libs_path):
        continue
    for _dll in glob.glob(os.path.join(_libs_path, '*.dll')):
        _all_binaries.append((_dll, _libs_name))
        print(f"  [OK] libs DLL: {os.path.basename(_dll)} -> {_libs_name}/")

# 去重
_all_hidden = list(set(_all_hidden))

# ═══════════════════════════════════════════════════════════
# ② Analysis（此时 hiddenimports 已完整）
# ═══════════════════════════════════════════════════════════

a = Analysis(
    [str(_PROJECT_ROOT / "src" / "main.py")],
    pathex=[str(_PROJECT_ROOT)],
    binaries=_all_binaries,
    datas=_all_datas,
    hiddenimports=_all_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# ═══════════════════════════════════════════════════════════
# ③ PYZ / EXE / COLLECT
# ═══════════════════════════════════════════════════════════

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
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_PROJECT_ROOT / "src" / "resources" / "icons" / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FormTransPDF",
)
