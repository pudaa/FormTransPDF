# FormTransPDF

<div align="center">
  <img src="src/resources/icons/app.png" alt="FormTransPDF Logo" width="128" height="128">
</div>

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
| 🌙 暗色 | **Gilded Ink**（鎏金墨韵） | Warm Industrial × Scholarly Editorial |
| ☀ 亮色 | **Vellum**（羊皮纸） | Warm Academic × Manuscript |

- **字体**: Cormorant Garamond（标题）/ IBM Plex Sans（正文）
- **主题切换**: 顶栏 ☀/🌙 按钮一键切换

## 功能

- 📄 **单窗口 PDF 查看**: 标签切换原始 / 译文，全宽渲染
- 🌐 **16+ 翻译服务**: OpenAI, DeepSeek, DeepL, Google, Ollama, 智谱...
- 🖱️ **拖拽加载**: 拖入 PDF 立即查看
- 📊 **实时进度**: 逐页进度条
- 🔍 **真缩放**: Ctrl+滚轮（30%~800%）/ 自适应宽度
- 💾 **设置持久化**: 自动保存翻译配置
- 📋 **模型下拉**: 切换服务自动加载常用模型
- ⬇ **下载译文**: 保存翻译结果 PDF
- 📁 **可收起侧边栏**: 点击 ☰ 释放空间

## 关于翻译速度

基于 BabelDOC 引擎，每页每个文本块需要一次 API 往返。一篇 10 页论文通常 30 秒 ~ 2 分钟（取决于 API 速度和模型大小）。这是正常的。

## 项目结构

```
FormTransPDF/
├── src/
│   ├── main.py / app.py     # 入口 + QApplication
│   ├── core/                # 翻译引擎封装
│   ├── ui/                  # 界面 + 主题系统
│   └── resources/icons/     # 图标（手动放置）
├── output/                  # 翻译输出（自动生成，已 gitignore）
├── FormTransPDF.spec        # PyInstaller 打包配置
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
|------|----------|
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

1. 拖拽 PDF 到窗口（或点击「📂 选择」）
2. 选择翻译服务、填写 API Key、选模型
3. 选择源语言 / 目标语言
4. 点击「🚀 翻译」
5. 完成后自动切换译文标签
6. 点击「⬇ 下载译文」保存

## 打包为 EXE

```bash
pip install pyinstaller
pyinstaller FormTransPDF.spec
# → dist/FormTransPDF/FormTransPDF.exe
```

打包后输出目录自动变为 `%USERPROFILE%\FormTransPDF\output\`。

## 发布到 GitHub（手动步骤）

```bash
# 1. 在 GitHub 网页创建空仓库（不要勾选 README/.gitignore/LICENSE）

# 2. 初始化本地仓库
cd d:\Codes\FormTransPDF
git init
git add .
git commit -m "feat: FormTransPDF v0.1.0 — PyQt6 PDF translation viewer"

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
