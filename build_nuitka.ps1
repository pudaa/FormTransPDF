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
    "--include-data-dir=src/resources=src/resources"  # 资源文件
    "--follow-import-to=src"                # 跟踪项目自身模块
    "--follow-import-to=pdf2zh_next"        # 翻译引擎
    "--follow-import-to=babeldoc"           # BabelDOC 引擎
    "--follow-import-to=bitstring"          # 二进制解析
    "--follow-import-to=tiktoken"           # tokenizer
    "--follow-import-to=tiktoken_ext"       # tiktoken 编码插件
    "--follow-import-to=hyperscan"          # 高性能正则
    "--follow-import-to=qasync"             # 异步桥接    "--nofollow-import-to=pymupdf"            # 避免转译巨大 C 扩展（babeldoc 依赖）    "--nofollow-import-to=PyQt6"            # 排除竞争对手
    "--nofollow-import-to=PySide6.QtQml"    # 不需要的 Qt 模块
    "--nofollow-import-to=PySide6.QtQuick"
    "--nofollow-import-to=PySide6.QtQuickWidgets"
    "--nofollow-import-to=PySide6.QtSvg"
    "--nofollow-import-to=PySide6.QtSvgWidgets"
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
    "--no-prefer-source-code"               # 使用预编译 .pyd，避免重编译 C 扩展（如 pymupdf）
    "--output-dir=$OutputDir"
    "--jobs=8"                              # 并行编译（限制为 8，避免内存不足）
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
    Write-Host "=== 模式: 单文件 (onefile) ===" -ForegroundColor Yellow
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
python -m nuitka @NuitkaArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n!!! 构建失败 (exit code: $LASTEXITCODE) !!!" -ForegroundColor Red
    exit $LASTEXITCODE
}

$sw.Stop()

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
