"""PDF 功能域 — 渲染查看、布局计算、文本提取与覆盖层。

- ``viewer``：PDF 查看器（QPdfView + 文本选择）
- ``selection``：文本选择 / 中键拖拽 / 浮动工具栏交互
- ``layout_engine``：镜像 QPdfView 的页面布局计算
- ``text_extractor``：PyMuPDF 异步文本提取（带版本控制）
- ``text_overlay``：透明高亮覆盖层、浮动工具栏、Toast
"""
