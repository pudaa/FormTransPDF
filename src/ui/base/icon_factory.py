"""
图标工厂 — 将 icons/ 目录下的单色 SVG 渲染为主题色 QIcon。

icons/ 下的 SVG 是单色图标，原始填充色为亮色主题青铜色 #8b6914
（sun.svg 为暗色主题金色 #d4a853）。本模块在加载时将这些模板色统一替换为
当前主题所需的颜色，保证明暗主题用色符合主题色设计：

- 正常态：主题强调色 accent（亮 #8b6914 / 暗 #d4a853）
- hover / 按下（背景反色为强调色时）：强调色对比色 _contrast_text(accent)
- 禁用态：主题 text_disabled（通过 QIcon 的 Disabled 模式变体）

背景说明：Qt 按钮在“激活窗口”中始终以 QIcon.Active 模式绘制图标
（QStyle::State_Active），无法用 QIcon 模式区分 hover；因此 hover 颜色由
IconHoverFilter 在 Enter/Leave 事件中显式切换，避免固定强调色图标在
hover 金色背景上“隐身”。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton

from src.ui.base.theme import theme_manager, _contrast_text


def _data_root() -> Path:
    """资源根目录：返回含 resources/ 子目录的那一级。

    探测顺序（不依赖 frozen/__compiled__ 标志，所有运行形态统一走同一套逻辑）：
      1. PyInstaller: sys._MEIPASS（onefile/onedir 均指向解包根，spec 映射
         resources/ 到该目录下）
      2. 编译模块 __file__ 虚拟路径向上探测 —— Nuitka 官方文档 "Onefile:
         Finding files" 推荐方式。注意 onefile 下 sys.executable 指向用户
         启动的原始 exe 位置而非解压目录，不可依赖。开发模式下同样命中
        （src/ui/base/icon_factory.py 向上到 src/）。
      3. 兜底：exe 同级（standalone 布局）
      4. 最终兜底：开发模式项目 src/
    """
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass and (Path(meipass) / "resources").is_dir():
        return Path(meipass)

    probe = Path(__file__).resolve().parent
    for _ in range(6):
        if (probe / "resources").is_dir():
            return probe
        if probe.parent == probe:
            break
        probe = probe.parent

    try:
        exe_dir = Path(sys.executable).parent
        if (exe_dir / "resources").is_dir():
            return exe_dir
    except Exception:
        pass

    # 开发模式默认：src/ui/base/icon_factory.py → 上三级即项目 src/
    return Path(__file__).resolve().parents[2]


ICON_DIR = _data_root() / "resources" / "icons"

# 模板色：icons/ 下 SVG 的原始填充色（加载时统一替换为目标颜色）
_TEMPLATE_COLORS = ("#8b6914", "#d4a853")

_pixmap_cache: dict[tuple[str, str, int], QPixmap] = {}


def svg_pixmap(name: str, color: QColor | str, size: int = 18) -> QPixmap:
    """渲染单色 SVG 为指定颜色的 QPixmap（带缓存）。"""
    hex_color = QColor(color).name()
    key = (name, hex_color, size)
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached

    data = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    for template in _TEMPLATE_COLORS:
        data = data.replace(template, hex_color)

    renderer = QSvgRenderer(data.encode("utf-8"))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()

    _pixmap_cache[key] = pixmap
    return pixmap


def svg_icon(name: str, color: QColor | str, size: int = 18) -> QIcon:
    """单色 QIcon（附带禁用态变体，禁用时自动使用主题禁用色）。"""
    qicon = QIcon(svg_pixmap(name, color, size))
    qicon.addPixmap(
        svg_pixmap(name, theme_manager.palette.text_disabled, size),
        QIcon.Mode.Disabled,
    )
    return qicon


def accent_icon(name: str, size: int = 18) -> QIcon:
    """按当前主题强调色渲染的图标。"""
    return svg_icon(name, theme_manager.palette.accent, size)


def accent_hover_pair(name: str, size: int = 18) -> tuple[QIcon, QIcon]:
    """返回 (正常强调色图标, hover 对比色图标)，供 IconHoverFilter 使用。"""
    tp = theme_manager.palette
    return (
        svg_icon(name, tp.accent, size),
        svg_icon(name, _contrast_text(tp.accent), size),
    )


class IconHoverFilter(QObject):
    """在按钮 Enter/Leave 时切换图标颜色（解决 hover 背景反色时图标隐身）。"""

    def __init__(self, button: QAbstractButton, name: str, size: int = 18):
        super().__init__(button)
        self._button = button
        self._name = name
        self._size = size
        self._apply_theme()
        button.installEventFilter(self)

    def _apply_theme(self) -> None:
        self._normal, self._hover = accent_hover_pair(self._name, self._size)
        self._button.setIcon(self._normal)

    def set_icon_name(self, name: str) -> None:
        """切换图标（如主题按钮：暗色→sun / 亮色→moon）。"""
        if name != self._name:
            self._name = name
            self._apply_theme()

    def refresh_theme(self) -> None:
        """主题切换后按新主题重建图标颜色。"""
        self._apply_theme()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Enter:
            self._button.setIcon(self._hover)
        elif event.type() == QEvent.Type.Leave:
            self._button.setIcon(self._normal)
        return False
