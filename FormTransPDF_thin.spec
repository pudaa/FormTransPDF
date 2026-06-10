# -*- mode: python ; coding: utf-8 -*-
"""
FormTransPDF — PyInstaller 打包配置（优化版）

打包命令:
    pyinstaller FormTransPDF_thin.spec          # 正常打包
    pyinstaller FormTransPDF.spec --console  # 调试模式（带控制台）

策略: --onedir 模式，最大化兼容性。
      ✅ UPX 压缩（减少 30-40% 体积）
      ✅ 去除冗余翻译文件 / 测试 / C++ 头文件
      ✅ Strip + optimize=2
"""

import sys
import os
import glob
import fnmatch
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
    "tiktoken",
    "tiktoken_ext",
]

# ── 基础隐藏导入 ────────────────────────────────────────
_HIDDEN_IMPORTS = [
    "multiprocessing",
    "multiprocessing.pool",
    "multiprocessing.popen_spawn_win32",
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
# ① 辅助函数（必须在循环前定义）
# ═══════════════════════════════════════════════════════════


def _filter_pyside6_datas(datas):
    """过滤 PySide6 中不需要的数据文件。

    保留:
      - Qt 核心资源 (.qm 翻译仅保留英文/中文)
      - 必要的插件、字体、类型系统文件
    移除:
      - 60+ 种非必要翻译文件 (~20-30 MB)
      - C++ 头文件 (include/)
      - 示例代码 (examples/)
    """
    keep_qm = {"qtbase_zh_CN.qm", "qtbase_en.qm", "qt_en.qm", "qt_zh_CN.qm"}
    filtered = []
    removed_count = 0
    for src_path, dest_dir in datas:
        normalized = src_path.replace("\\", "/")
        # 跳过 C++ 头文件
        if "/include/" in normalized:
            removed_count += 1
            continue
        # 跳过示例
        if "/examples/" in normalized:
            removed_count += 1
            continue
        # 翻译文件：只保留英文和中文
        if normalized.endswith(".qm"):
            fname = os.path.basename(src_path)
            if fname not in keep_qm:
                removed_count += 1
                continue
        filtered.append((src_path, dest_dir))
    if removed_count:
        print(f"  [OK] PySide6: removed {removed_count} redundant data files")
    return filtered


# ═══════════════════════════════════════════════════════════
# ② collect-all
# ═══════════════════════════════════════════════════════════

_all_hidden = list(_HIDDEN_IMPORTS)
_all_datas = list(_DATAS)
_all_binaries = []

for pkg in _COLLECT_ALL_PACKAGES:
    try:
        from PyInstaller.utils.hooks import collect_all as _collect_all
        datas, binaries, hiddenimports = _collect_all(pkg)
        if pkg == "PySide6":
            datas = _filter_pyside6_datas(datas)
        _all_datas.extend(datas)
        _all_binaries.extend(binaries)
        _all_hidden.extend(hiddenimports)
        print(f"  [OK] collect-all: {pkg} "
              f"({len(datas)} data, {len(binaries)} bin, "
              f"{len(hiddenimports)} imports)")
    except Exception as exc:
        print(f"  [WARN] collect-all failed for {pkg}: {exc}")


# ── 手动收集 delvewheel .libs 下的 DLL ────────────────────
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
# ② 排除列表（在现有基础上追加）
# ═══════════════════════════════════════════════════════════

_EXCLUDES = [
    "PyQt6",
    # 构建工具（运行时不需要）
    "setuptools",
    "setuptools._vendor",
    "distutils",
    "pip",
    "wheel",
    "pkg_resources",
    # PySide6 子模块
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtSensors",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtPositioning",
    "PySide6.QtRemoteObjects",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtTextToSpeech",
    "PySide6.QtAxContainer",
    "PySide6.QtConcurrent",
    "PySide6.QtStateMachine",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtHelp",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtXml",
    "PySide6.QtDBus",
    "PySide6.scripts",
]

# ═══════════════════════════════════════════════════════════
# ③ Analysis
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
    excludes=_EXCLUDES,
    noarchive=False,
    optimize=2,  # ← 移除 docstring，减小体积 + 加速导入
)

# ═══════════════════════════════════════════════════════════
# ④ PYZ / EXE / COLLECT
#
# 注意：
#   strip=True 在 Windows 上无效（strip 是 Unix 工具，会输出 WARNING）
#   upx=True  在现代 Python 3.11 DLL 上收益极小 —— 几乎所有 Qt/
#             onnxruntime/scipy DLL 都有 CFG 保护，PyInstaller 自动跳过。
#             但仍会压缩少量 .pyd 和无 CFG 的 DLL。
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
    strip=False,      # Windows 上 strip 不存在，无需开启
    upx=True,          # 仍开启（压缩少数无 CFG 的 .pyd）
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
    strip=False,       # Windows 上 strip 不存在
    upx=True,           # 仍开启
    upx_exclude=[],
    name="FormTransPDF",
)