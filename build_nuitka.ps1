<#
.SYNOPSIS
    FormTransPDF — Nuitka 打包脚本
.DESCRIPTION
    使用 Nuitka 将 FormTransPDF 编译为独立的 Windows 可执行程序。

    前置条件（首次运行前执行一次）:
        conda install -c conda-forge gcc
        pip install nuitka

    使用方法:
        .\build_nuitka.ps1                # 正常打包
        .\build_nuitka.ps1 -Console       # 打包带控制台窗口（调试用）
        .\build_nuitka.ps1 -OneFile       # 打包为单文件（启动稍慢）
        .\build_nuitka.ps1 -Quick         # 快速构建（跳过 LTO，用于测试）
        .\build_nuitka.ps1 -Clean         # 先清理再构建

    输出:
        build-nuitka/main.dist/FormTransPDF.exe  （standalone 模式）
        build-nuitka/FormTransPDF.exe             （onefile 模式）
#>

param(
    [switch]$Console,
    [switch]$OneFile,
    [switch]$Quick,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = Get-Location }
Set-Location $ProjectRoot

# ── 版本信息 ──────────────────────────────────────────────
$AppName = "FormTransPDF"
$IconFile = "src/resources/icons/app.ico"
$EntryPoint = "src/main.py"
$OutputDir = "build-nuitka"
# 从 src/__init__.py 读取版本号（单一来源），供 --product-version 与
# onefile 缓存路径 {VERSION} 使用
$AppVersion = (Select-String -Path "src/__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value

# ── 环境自检 ──────────────────────────────────────────────
# 项目依赖（含 nuitka）安装在 conda 环境 "formtranspdf" 中。
# 若在错误环境（如 base）运行，只会报晦涩的 "No module named nuitka"，
# 这里提前检测并给出明确指引。
$PyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PyExe) {
    Write-Host "!!! 未找到 python，请先激活项目环境: conda activate formtranspdf" -ForegroundColor Red
    exit 1
}
# 临时关闭 $ErrorActionPreference="Stop"，避免外部命令写 stderr（如 Nuitka 导入时的
# 提示信息）被当作终止性错误抛出
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$null = & python -c "import nuitka" 2>$null
$nuitkaOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEAP
if (-not $nuitkaOk) {
    Write-Host "!!! 当前 Python 环境未安装 Nuitka: $PyExe" -ForegroundColor Red
    Write-Host "    请先激活项目环境再打包:" -ForegroundColor Yellow
    Write-Host "        conda activate formtranspdf" -ForegroundColor Cyan
    exit 1
}
Write-Host "环境检查通过: $PyExe" -ForegroundColor DarkGray

# ── 编译器检测 ────────────────────────────────────────────
# Nuitka 在 Windows 上默认会下载自己的 MinGW64 编译器。若下载被卡住（网络问题），
# 进程会挂起等待交互确认（--windows-console-mode=disable 会吞掉提示）。
# 这里优先使用 conda 环境里已有的 gcc（conda install -c conda-forge gcc），
# 通过设置 CC 环境变量让 Nuitka 直接使用它，避免下载。
$GccExe = Get-ChildItem -Path "$PyExe\..\Library\bin\gcc.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $GccExe) {
    # 回退：在 PATH 中查找 gcc
    $GccExe = Get-Command gcc -ErrorAction SilentlyContinue | Select-Object -First 1
}
if ($GccExe) {
    $env:CC = $GccExe.FullName
    Write-Host "使用编译器: $env:CC" -ForegroundColor DarkGray
} else {
    Write-Host "!!! 未找到 gcc 编译器，Nuitka 将尝试自动下载 MinGW64（可能较慢）" -ForegroundColor Yellow
}

# ── 清理 ──────────────────────────────────────────────────
if ($Clean) {
    Write-Host "=== 清理之前的构建产物 ===" -ForegroundColor Cyan
    if (Test-Path "$OutputDir/main.dist") { Remove-Item -Recurse -Force "$OutputDir/main.dist" }
    if (Test-Path "$OutputDir/main.build") { Remove-Item -Recurse -Force "$OutputDir/main.build" }
    if (Test-Path "$OutputDir/FormTransPDF.exe") { Remove-Item -Force "$OutputDir/FormTransPDF.exe" }
    Write-Host "  清理完成" -ForegroundColor Green
}

# ── 基础 Nuitka 参数 ──────────────────────────────────────
$NuitkaArgs = @(
    "--standalone"                          # 独立目录模式（启动最快）
    "--enable-plugin=pyside6"               # PySide6 Qt 插件支持
    "--enable-plugin=multiprocessing"       # multiprocessing 支持
    "--windows-icon-from-ico=$IconFile"     # 应用程序图标
    "--product-version=$AppVersion"         # 产品版本（供 {VERSION} 占位符展开）
    "--assume-yes-for-downloads"            # 自动同意下载缺失的外部工具（如 MinGW64），避免交互提示被吞导致挂起
    "--include-data-dir=src/resources=resources"  # 资源文件（数据根=resources，与 app.py/_get_data_path 一致）
    "--follow-import-to=src"                # 跟踪项目自身模块
    "--follow-import-to=pdf2zh_next"        # 翻译引擎
    "--follow-import-to=babeldoc"           # BabelDOC 引擎
    "--follow-import-to=bitstring"          # 二进制解析
    "--follow-import-to=tiktoken"           # tokenizer
    "--follow-import-to=tiktoken_ext"       # tiktoken 编码插件
    "--follow-import-to=hyperscan"          # 高性能正则
    "--follow-import-to=qasync"             # 异步桥接
    #
    # ── 显式包含动态导入的 translator_impl 模块 ──
    # pdf2zh_next 使用 importlib.import_module() 动态加载这些模块，
    # Nuitka 静态分析无法发现，必须用 --include-module 强制纳入编译。
    #
    "--include-module=pdf2zh_next.translator.translator_impl.anythingllm"
    "--include-module=pdf2zh_next.translator.translator_impl.azure"
    "--include-module=pdf2zh_next.translator.translator_impl.azureopenai"
    "--include-module=pdf2zh_next.translator.translator_impl.bing"
    "--include-module=pdf2zh_next.translator.translator_impl.claudecode"
    "--include-module=pdf2zh_next.translator.translator_impl.clitranslator"
    "--include-module=pdf2zh_next.translator.translator_impl.deepl"
    "--include-module=pdf2zh_next.translator.translator_impl.dify"
    "--include-module=pdf2zh_next.translator.translator_impl.google"
    "--include-module=pdf2zh_next.translator.translator_impl.ollama"
    "--include-module=pdf2zh_next.translator.translator_impl.openai"
    "--include-module=pdf2zh_next.translator.translator_impl.qwenmt"
    "--include-module=pdf2zh_next.translator.translator_impl.siliconflow"
    "--include-module=pdf2zh_next.translator.translator_impl.siliconflowfree"
    "--include-module=pdf2zh_next.translator.translator_impl.tencentmechinetranslation"
    "--include-module=pdf2zh_next.translator.translator_impl.xinference"
    #
    #
    # ── pymupdf：以字节码模式包含 ──
    # _mupdf.pyd / mupdfcpp64.dll 等 C 扩展由 Nuitka 原样复制；
    # 但 mupdf.py 是 SWIG 生成的胶水代码（2.3MB Python → 107MB C），
    # 用 -O3 编译它 cc1 需要 10GB+ 内存必然 OOM，且胶水代码编译成 C 收益极小。
    # bytecode 模式让 Python 包装层以 .pyc 运行，核心性能不受影响。
    "--include-package=pymupdf"
    "--noinclude-custom-mode=pymupdf:bytecode"
    "--no-deployment-flag=excluded-module-usage"  # 其余被排除模块（如 PyQt6）运行时被探测时不报错
    "--nofollow-import-to=PyQt6"            # 排除竞争对手
    "--nofollow-import-to=PySide6.QtQml"    # 不需要的 Qt 模块
    "--nofollow-import-to=PySide6.QtQuick"
    "--nofollow-import-to=PySide6.QtQuickWidgets"
    # 注意：QtSvg 必须保留（icon_factory 用 QSvgRenderer 渲染 SVG 图标）
    "--nofollow-import-to=PySide6.QtCharts"
    "--nofollow-import-to=PySide6.QtDataVisualization"
    "--nofollow-import-to=PySide6.QtSensors"
    "--nofollow-import-to=PySide6.QtMultimedia"
    "--nofollow-import-to=PySide6.QtMultimediaWidgets"
    "--nofollow-import-to=PySide6.QtWebEngineCore"
    "--nofollow-import-to=PySide6.QtWebEngineWidgets"
    "--nofollow-import-to=PySide6.QtWebChannel"
    "--nofollow-import-to=PySide6.QtPositioning"
    "--nofollow-import-to=PySide6.QtRemoteObjects"
    "--nofollow-import-to=PySide6.QtSerialPort"
    "--nofollow-import-to=PySide6.QtSerialBus"
    "--nofollow-import-to=PySide6.QtTextToSpeech"
    "--nofollow-import-to=PySide6.QtAxContainer"
    "--nofollow-import-to=PySide6.QtConcurrent"
    "--nofollow-import-to=PySide6.QtStateMachine"
    "--nofollow-import-to=PySide6.Qt3DCore"
    "--nofollow-import-to=PySide6.Qt3DRender"
    "--nofollow-import-to=PySide6.Qt3DInput"
    "--nofollow-import-to=PySide6.Qt3DAnimation"
    "--nofollow-import-to=PySide6.Qt3DExtras"
    "--nofollow-import-to=PySide6.QtBluetooth"
    "--nofollow-import-to=PySide6.QtNfc"
    "--nofollow-import-to=PySide6.QtHelp"
    "--nofollow-import-to=PySide6.QtSql"
    "--nofollow-import-to=PySide6.QtTest"
    "--nofollow-import-to=PySide6.QtDesigner"
    "--nofollow-import-to=PySide6.QtUiTools"
    "--nofollow-import-to=PySide6.QtXml"
    "--nofollow-import-to=PySide6.QtDBus"
    "--nofollow-import-to=PySide6.scripts"
    "--nofollow-import-to=setuptools"       # 构建工具（运行时不需要）
    "--nofollow-import-to=distutils"
    "--nofollow-import-to=pip"
    "--nofollow-import-to=wheel"
    "--nofollow-import-to=pkg_resources"
    "--no-prefer-source-code"               # 使用预编译 .pyd，避免重编译 C 扩展
    "--output-dir=$OutputDir"
    "--output-filename=$AppName.exe"         # 指定输出文件名
    "--jobs=1"                              # 串行编译（最保险，避免内存不足）
    "--low-memory"                          # 降低内存使用（pymupdf 的 mupdf 模块 C 文件 107MB，cc1 编译需 4-6GB）
    $EntryPoint
)

# ── 模式相关参数 ──────────────────────────────────────────
if (-not $Console) {
    $NuitkaArgs += "--windows-console-mode=disable"  # 隐藏控制台窗口
}

if ($OneFile) {
    # ── 单文件模式（启动时解压，稍慢，但只有一个文件）──
    $NuitkaArgs = $NuitkaArgs.Where({ $_ -ne "--standalone" })
    $NuitkaArgs += "--onefile"
    # 解压目录缓存到用户缓存区：仅首次启动付出解压代价（数百 MB）。
    # {VERSION} 由 --product-version 提供，发布新版本时缓存路径自动变化，
    # 避免旧缓存与新载荷不匹配。注意：Nuitka 4.1.3 无 {CACHE_VERSION} 变量，
    # 使用它会报 "Found unknown variable name"。
    $NuitkaArgs += '--onefile-tempdir-spec={CACHE_DIR}/FormTransPDF/{VERSION}'
    Write-Host "=== 模式: 单文件 (onefile，解压缓存已启用) ===" -ForegroundColor Yellow
} else {
    Write-Host "=== 模式: 独立目录 (standalone) ===" -ForegroundColor Green
}

if ($Quick) {
    # ── 快速模式（无 LTO，适用于测试）──
    $NuitkaArgs += "--lto=no"
    Write-Host "  快速模式: LTO 已禁用" -ForegroundColor Yellow
} else {
    $NuitkaArgs += "--lto=yes"              # 链接时优化（更小更快）
}

# ── 显示完整命令 ──────────────────────────────────────────
Write-Host "`n=== Nuitka 打包命令 ===" -ForegroundColor Cyan
Write-Host ("python -m nuitka " + ($NuitkaArgs -join " ")) -ForegroundColor Gray

# ── 定时 ──────────────────────────────────────────────────
$sw = [System.Diagnostics.Stopwatch]::StartNew()

# ── 执行 ──────────────────────────────────────────────────
Write-Host "`n=== 开始构建... （首次编译可能需要 15-30 分钟）===" -ForegroundColor Cyan
Write-Host "  提示：若遇到内存不足错误，可减少 --jobs 参数（改为 4 或 6）" -ForegroundColor Yellow
# 临时关闭 $ErrorActionPreference="Stop"：Nuitka 会向 stderr 写进度信息
# （如 "Nuitka-Options: ..."），在 Stop 模式下会被当作 NativeCommandError 抛出，
# 导致脚本中断且 Nuitka 输出丢失。
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
python -m nuitka @NuitkaArgs
$nuitkaExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP

if ($nuitkaExit -ne 0) {
    Write-Host "`n!!! 构建失败 (exit code: $nuitkaExit) !!!" -ForegroundColor Red
    exit $nuitkaExit
}

$sw.Stop()

# （旧后处理“手动复制 pymupdf 到 main.dist”已移除 —— 现由 --include-package=pymupdf 统一处理）

# ── 完成 ──────────────────────────────────────────────────
Write-Host "`n=== 构建成功！耗时: $($sw.Elapsed.TotalMinutes.ToString('0.0')) 分钟 ===" -ForegroundColor Green

if ($OneFile) {
    $outputExe = "$OutputDir/$AppName.exe"
    if (Test-Path $outputExe) {
        $size = (Get-Item $outputExe).Length / 1MB
        Write-Host "输出: $outputExe" -ForegroundColor Green
        Write-Host "大小: $('{0:N1}' -f $size) MB" -ForegroundColor Green
    }
} else {
    $outputDir = "$OutputDir/main.dist"
    if (Test-Path $outputDir) {
        $size = (Get-ChildItem -Recurse $outputDir | Measure-Object Length -Sum).Sum / 1MB
        Write-Host "输出目录: $outputDir" -ForegroundColor Green
        Write-Host "总大小: $('{0:N1}' -f $size) MB" -ForegroundColor Green
        Write-Host "启动: $outputDir\$AppName.exe" -ForegroundColor Green
    }
}
