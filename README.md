# FormTransPDF

<div align="center">
  <img src="src/resources/icons/app.png" alt="FormTransPDF Logo" width="128" height="128">
</div>

<div align="center">

![FormTransPDF](https://img.shields.io/badge/FormTransPDF-v0.1.0-d4a853?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python)
![PySide6](https://img.shields.io/badge/PySide-6-41CD52?style=flat-square&logo=qt)
![License](https://img.shields.io/badge/License-MIT-8a8578?style=flat-square)

**PDF 科学论文翻译查看器 — 基于 pdf2zh-next**

"Darkroom for Documents" — 专注的阅读与翻译工坊

</div>

---

## 设计美学

| 主题 | 名称 | 理念 |
|------|------|------|
| 暗色 | **Gilded Ink**（鎏金墨韵） | Warm Industrial × Scholarly Editorial |
| 亮色 | **Vellum**（羊皮纸） | Warm Academic × Manuscript |

- **字体**: Cormorant Garamond（标题）/ IBM Plex Sans（正文）
- **主题切换**: 顶栏一键切换暗色/亮色主题

## 功能

- **单窗口 PDF 查看**: 标签切换原始 / 译文，全宽渲染
- **16+ 翻译服务**: OpenAI, DeepSeek, DeepL, Google, Ollama, 智谱...
- **拖拽加载**: 拖入 PDF 立即查看
- **实时进度**: 逐页进度条
- **缩放**: Ctrl+滚轮（30%~800%）/ 自适应宽度
- **设置持久化**: 自动保存翻译配置
- **模型下拉**: 切换服务自动加载常用模型
- **下载译文**: 保存翻译结果 PDF
- **可收起侧边栏**: 点击菜单按钮释放空间
- **即时翻译浮窗**: 选中文本弹出，支持 Esc 关闭、拖拽关闭、自动定位右下角

## 关于翻译速度

基于 BabelDOC 引擎，每页每个文本块需要一次 API 往返。一篇 10 页论文通常 30 秒 ~ 2 分钟（取决于 API 速度和模型大小）。这是正常的。

## 项目结构

```text
FormTransPDF/
├── src/
│   ├── main.py / app.py     # 入口 + QApplication
│   ├── core/                # 翻译引擎封装
│   ├── ui/                  # 界面 + 主题系统
│   └── resources/icons/     # 图标（手动放置）
├── output/                  # 翻译输出（自动生成，已 gitignore）
├── FormTransPDF.spec        # PyInstaller 打包配置
├── FormTransPDF_thin.spec   # PyInstaller 优化版配置
├── build_nuitka.ps1         # Nuitka 打包脚本
├── requirements.txt
├── environment.yml
└── README.md
```

## 快速开始

### 1. 创建虚拟环境

```bash
# conda
conda env create -f environment.yml
conda activate formtranspdf

# 或 venv
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
```

### 2. 配置 API Key

至少配置一个翻译服务的 API Key。支持通过环境变量设置：

| 服务 | 环境变量 |
| ------ | ---------- |
| OpenAI | `OPENAI_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| DeepL | `DEEPL_API_KEY` |
| Google | `GOOGLE_API_KEY` |

或在应用内的设置面板中直接输入。

若使用 **Ollama** 本地部署，无需 API Key，只需确保 Ollama 服务已启动。

### 3. 启动

```bash
python -m src.main
```

## 使用流程

1. 拖拽 PDF 到窗口（或点击「选择」按钮）
2. 选择翻译服务、填写 API Key、选模型
3. 选择源语言 / 目标语言
4. 点击「翻译」
5. 完成后自动切换译文标签
6. 点击「下载译文」保存

## 打包为 EXE

本项目支持 **PyInstaller** 和 **Nuitka** 两种打包方式，可根据需求选择。

### 快速对比

| 维度 | PyInstaller (`FormTransPDF_thin.spec`) | Nuitka (`build_nuitka.ps1`) |
|------|----------------------------------------|-----------------------------|
| 打包速度 | **快**（1–3 分钟） | 慢（首次 ~20 分钟） |
| **启动速度** | ~20 秒 | **~1.4 秒**（**14× 提升** ✅） |
| 输出体积 | ~180–220 MB（启用 UPX） | ~300 + pymupdf 额外 ~50 MB |
| 增量构建 | 每次都完整打包 | ✅ 仅重新编译变更文件（~2 分钟） |
| 分发方式 | 文件夹 | 文件夹（或单文件 `-OneFile`） |

> **实测数据**（Windows 11, i7-13700H, 32GB RAM）：
> - PyInstaller 窗口出现: 19.8–20.6s
> - Nuitka 窗口出现: **1.41–1.44s** |

### 方式一：PyInstaller（传统方案）

```bash
pip install pyinstaller
pyinstaller FormTransPDF_thin.spec
# → dist/FormTransPDF/FormTransPDF.exe
```

打包后输出目录自动变为 `%USERPROFILE%\FormTransPDF\output\`。

可用 spec 文件：

| 文件 | 说明 |
|------|------|
| `FormTransPDF.spec` | 标准版 |
| `FormTransPDF_thin.spec` | **推荐** — UPX 压缩 + 排除冗余文件 |
| `FormTransPDF_debug.spec` | 调试版（带控制台窗口） |

### 方式二：Nuitka（推荐 — 启动更快）

#### 前置条件（仅首次）

```bash
conda install -c conda-forge gcc      # 需要 C 编译器
pip install nuitka
```

首次构建时，Nuitka 会自动下载配套的 MinGW64 编译器到本地缓存。

#### 日常构建

```powershell
# 常规模式（推荐）
.\build_nuitka.ps1
# → build-nuitka/main.dist/FormTransPDF.exe

# 日常开发调试（增量编译，1-5 分钟）
.\build_nuitka.ps1 -Quick

# 带控制台窗口（看日志）
.\build_nuitka.ps1 -Console

# 单文件发布
.\build_nuitka.ps1 -OneFile

# 完全重新构建
.\build_nuitka.ps1 -Clean
```

#### 增量更新

修改源码后，只需再次运行：

```powershell
.\build_nuitka.ps1 -Quick
```

Nuitka 会自动检测变更文件，仅重新编译修改的部分，速度很快。

### 打包兼容说明

两个打包系统互不干扰，源文件已同时兼容两者。修改代码后，你可以自由选择任一方式构建。

## 发布到 GitHub（手动步骤）

```bash
# 1. 在 GitHub 网页创建空仓库（不要勾选 README/.gitignore/LICENSE）

# 2. 初始化本地仓库
cd d:\Codes\FormTransPDF
git init
git add .
git commit -m "feat: FormTransPDF v0.1.0 — PySide6 PDF translation viewer"

# 3. 关联远程仓库
git remote add origin https://github.com/<你的用户名>/FormTransPDF.git
git branch -M main
git push -u origin main

# 4. 后续更新
git add .
git commit -m "描述改动"
git push
```

> 如果 GitHub 要求用 token 认证：Settings → Developer settings → Personal access tokens → Generate new token (classic)，勾选 `repo` 权限。

## 技术栈

| 层 | 技术 |
| ----- | ----- |
| 翻译引擎 | [pdf2zh-next](https://github.com/funstory-ai/BabelDOC) (BabelDOC) |
| UI 框架 | PySide6 |
| 异步桥接 | qasync |
| PDF 渲染 | QpdfView |

## License

MIT License © 2024 [FunStory AI](https://funstory.ai)
