"""
主题系统 — 双主题：Gilded Ink（鎏金墨韵·暗）+ Vellum（羊皮纸·亮）

Design Direction
----------------
- **Dark**:  Warm Industrial + Scholarly Editorial  → "Darkroom for Documents"
- **Light**: Warm Academic + Manuscript             → "Reading Room by Daylight"
- **DFII**:  13 (dark) / 12 (light)
- **Anchor**: 暗色主题为琥珀色强调 / 亮色主题为青铜色强调
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette


# ═══════════════════════════════════════════════════════════════
# Typography（双主题共用）
# ═══════════════════════════════════════════════════════════════

FONT_DISPLAY = "Cormorant Garamond"
FONT_BODY = "IBM Plex Sans"
FONT_MONO = "JetBrains Mono"
FONT_CJK = "Microsoft YaHei"


def _resolve_font(family: str, fallback: str) -> str:
    families = QFontDatabase.families()
    return family if family in families else fallback


def create_display_font(size: int = 28, weight: int = 600) -> QFont:
    family = _resolve_font(FONT_DISPLAY, FONT_CJK)
    font = QFont(family, size)
    font.setWeight(weight)
    font.setStyleHint(QFont.StyleHint.Serif)
    return font


def create_body_font(size: int = 11, weight: int = 400) -> QFont:
    family = _resolve_font(FONT_BODY, FONT_CJK)
    font = QFont(family, size)
    font.setWeight(weight)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    return font


def create_mono_font(size: int = 10) -> QFont:
    family = _resolve_font(FONT_MONO, "Consolas")
    font = QFont(family, size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


# ═══════════════════════════════════════════════════════════════
# Theme Palette
# ═══════════════════════════════════════════════════════════════

class ThemeMode(Enum):
    DARK = auto()
    LIGHT = auto()


@dataclass
class ThemePalette:
    """一组主题色板——所有颜色均通过实例属性访问"""

    # 背景层级
    canvas: QColor       # 最深/最亮画布底色
    background: QColor   # 主背景
    surface: QColor      # 面板/卡片
    surface_hover: QColor  # 悬浮态面板

    # 强调色
    accent: QColor        # 主强调
    accent_hover: QColor
    accent_press: QColor
    accent_muted: QColor  # 低对比度强调背景

    # 文字层级
    text_primary: QColor
    text_secondary: QColor
    text_disabled: QColor

    # 语义色
    error: QColor
    success: QColor
    warning: QColor

    # 边框/分割
    divider: QColor

    # 主题名
    name: str = ""
    mode: ThemeMode = ThemeMode.LIGHT # 主题模式默认亮色


# ═══════════════════════════════════════════════════════════════
# 暗色主题 — Gilded Ink（鎏金墨韵）
# ═══════════════════════════════════════════════════════════════

DARK_PALETTE = ThemePalette(
    name="Gilded Ink · 鎏金墨韵",
    mode=ThemeMode.DARK,

    canvas=QColor("#0d0d0d"),
    background=QColor("#16161a"),
    surface=QColor("#1e1e24"),
    surface_hover=QColor("#25252c"),

    accent=QColor("#d4a853"),
    accent_hover=QColor("#e8c97a"),
    accent_press=QColor("#b8943d"),
    accent_muted=QColor("#3d3524"),

    text_primary=QColor("#e0dcd0"),
    text_secondary=QColor("#8a8578"),
    text_disabled=QColor("#4a4640"),

    error=QColor("#c44b4b"),
    success=QColor("#5a9e6f"),
    warning=QColor("#d4943a"),

    divider=QColor("#2a2a30"),
)


# ═══════════════════════════════════════════════════════════════
# 亮色主题 — Vellum（羊皮纸）
# ═══════════════════════════════════════════════════════════════

LIGHT_PALETTE = ThemePalette(
    name="Vellum · 羊皮纸",
    mode=ThemeMode.LIGHT,

    canvas=QColor("#e8e0d5"),          # 暖象牙底
    background=QColor("#f5f1ea"),      # 浅羊皮纸
    surface=QColor("#ffffff"),         # 白卡片
    surface_hover=QColor("#faf7f2"),   # 悬浮

    accent=QColor("#8b6914"),          # 深青铜
    accent_hover=QColor("#a67c1e"),
    accent_press=QColor("#6b4f10"),
    accent_muted=QColor("#f0e6d2"),    # 淡金背景

    text_primary=QColor("#2c2416"),    # 深墨色
    text_secondary=QColor("#6b6152"),  # 灰褐色
    text_disabled=QColor("#b5aa9a"),

    error=QColor("#c0392b"),
    success=QColor("#3a7d44"),
    warning=QColor("#b8860b"),

    divider=QColor("#d8cfc0"),
)


# ═══════════════════════════════════════════════════════════════
# Backward-compat Colors alias（指向当前主题）
# ═══════════════════════════════════════════════════════════════

class _ColorsProxy:
    """代理类：访问 Colors.XXX 时自动转发到当前主题的 palette"""
    _palette: ThemePalette = DARK_PALETTE

    def __getattr__(self, name: str):
        upper = name.upper()
        mapping = {
            "INK": "canvas", "VELVET": "background", "SLATE": "surface",
            "CHARCOAL": "surface_hover", "GOLD": "accent", "GOLD_HOVER": "accent_hover",
            "GOLD_PRESS": "accent_press", "GOLD_MUTED": "accent_muted",
            "PARCHMENT": "text_primary", "ASH": "text_secondary", "CHAR": "text_disabled",
            "EMBER": "error", "MOSS": "success", "AMBER": "warning",
            "DIVIDER": "divider", "GOLDEN_SPINE": "accent",
        }
        attr_name = mapping.get(upper, name.lower())
        return getattr(self._palette, attr_name, None)

    @classmethod
    def set_palette(cls, p: ThemePalette) -> None:
        cls._palette = p


Colors = _ColorsProxy()


# ═══════════════════════════════════════════════════════════════
# Theme Manager
# ═══════════════════════════════════════════════════════════════

class ThemeManager:
    """单例主题管理器——负责全局主题切换"""

    _instance: ThemeManager | None = None

    def __new__(cls) -> ThemeManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._mode = ThemeMode.LIGHT
            cls._instance._palette = LIGHT_PALETTE
            _ColorsProxy.set_palette(cls._instance._palette)  # ← 同步代理
        return cls._instance

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @property
    def palette(self) -> ThemePalette:
        return self._palette

    @property
    def is_dark(self) -> bool:
        return self._mode == ThemeMode.DARK

    def toggle(self) -> ThemePalette:
        """切换主题并返回新 palette"""
        if self._mode == ThemeMode.DARK:
            self._mode = ThemeMode.LIGHT
            self._palette = LIGHT_PALETTE
        else:
            self._mode = ThemeMode.DARK
            self._palette = DARK_PALETTE

        _ColorsProxy.set_palette(self._palette)
        return self._palette

    def set_mode(self, mode: ThemeMode) -> ThemePalette:
        self._mode = mode
        self._palette = DARK_PALETTE if mode == ThemeMode.DARK else LIGHT_PALETTE
        _ColorsProxy.set_palette(self._palette)
        return self._palette

    @staticmethod
    def get_toggle_icon() -> str:
        """返回主题切换按钮图标"""
        return "亮色主题" if theme_manager.is_dark else "暗色主题"


theme_manager = ThemeManager()


# ═══════════════════════════════════════════════════════════════
# Palette Builder
# ═══════════════════════════════════════════════════════════════

def build_palette(tp: ThemePalette) -> QPalette:
    """从 ThemePalette 构建 QPalette"""
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, tp.background)
    p.setColor(QPalette.ColorRole.WindowText, tp.text_primary)
    p.setColor(QPalette.ColorRole.Base, tp.surface)
    p.setColor(QPalette.ColorRole.AlternateBase, tp.surface_hover)
    p.setColor(QPalette.ColorRole.Text, tp.text_primary)
    p.setColor(QPalette.ColorRole.Button, tp.surface)
    p.setColor(QPalette.ColorRole.ButtonText, tp.text_primary)
    p.setColor(QPalette.ColorRole.Highlight, tp.accent)
    p.setColor(QPalette.ColorRole.HighlightedText, _contrast_text(tp.accent))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, tp.text_disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, tp.text_disabled)
    p.setColor(QPalette.ColorRole.ToolTipBase, tp.surface_hover)
    p.setColor(QPalette.ColorRole.ToolTipText, tp.text_primary)
    p.setColor(QPalette.ColorRole.Link, tp.accent)
    p.setColor(QPalette.ColorRole.LinkVisited, tp.accent_press)
    return p


def _contrast_text(bg: QColor) -> QColor:
    """根据背景亮度返回黑/白文字色"""
    luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return QColor("#0d0d0d") if luminance > 128 else QColor("#ffffff")


# ═══════════════════════════════════════════════════════════════
# Stylesheet Builder（模板化 — 使用 %s 占位符）
# ═══════════════════════════════════════════════════════════════

def build_stylesheet(tp: ThemePalette) -> str:
    """生成全局样式表"""
    c = tp  # shorthand
    return f"""
/* ── 全局重置 ─────────────────────────────── */
QWidget {{
    background-color: {c.background.name()};
    color: {c.text_primary.name()};
    font-family: "IBM Plex Sans", "Microsoft YaHei", sans-serif;
    font-size: 11pt;
    selection-background-color: {c.accent.name()};
    selection-color: {_contrast_text(c.accent).name()};
}}

/* ── 滚动条 ───────────────────────────────── */
QScrollBar:vertical {{
    background: {c.background.name()};
    width: 8px; margin: 0; border: none;
}}
QScrollBar::handle:vertical {{
    background: {c.divider.name()};
    min-height: 32px; border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{ background: {c.accent.name()}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

QScrollBar:horizontal {{
    background: {c.background.name()};
    height: 8px; margin: 0; border: none;
}}
QScrollBar::handle:horizontal {{
    background: {c.divider.name()};
    min-width: 32px; border-radius: 4px;
}}
QScrollBar::handle:horizontal:hover {{ background: {c.accent.name()}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── 进度条 ───────────────────────────────── */
QProgressBar {{
    background: {c.surface.name()};
    border: 1px solid {c.divider.name()};
    border-radius: 2px; height: 6px;
    text-align: center; font-size: 9pt;
    color: {c.text_secondary.name()};
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c.accent_press.name()}, stop:0.5 {c.accent.name()}, stop:1 {c.accent_hover.name()});
    border-radius: 1px;
}}

/* ── 按钮 ─────────────────────────────────── */
QPushButton {{
    background: {c.surface.name()};
    color: {c.text_primary.name()};
    border: 1px solid {c.divider.name()};
    border-radius: 4px; padding: 8px 16px;
    font-weight: 500; letter-spacing: 0.3px;
}}
QPushButton:hover {{
    background: {c.surface_hover.name()};
    border-color: {c.accent.name()};
}}
QPushButton:pressed {{
    background: {c.surface.name()};
    border-color: {c.accent_press.name()};
}}
QPushButton:disabled {{
    background: {c.surface.name()};
    color: {c.text_disabled.name()};
    border-color: {c.divider.name()};
}}

QPushButton#primaryBtn {{
    background: {c.accent.name()};
    color: {_contrast_text(c.accent).name()};
    border: none; font-weight: 600; padding: 10px 24px;
}}
QPushButton#primaryBtn:hover {{ background: {c.accent_hover.name()}; }}
QPushButton#primaryBtn:pressed {{ background: {c.accent_press.name()}; }}
QPushButton#primaryBtn:disabled {{
    background: {c.accent_muted.name()};
    color: {c.text_disabled.name()};
}}

/* ── 输入框 ───────────────────────────────── */
QLineEdit, QSpinBox, QComboBox {{
    background: {c.canvas.name()};
    color: {c.text_primary.name()};
    border: 1px solid {c.divider.name()};
    border-radius: 3px; padding: 6px 10px;
    font-family: "IBM Plex Sans", "Microsoft YaHei", sans-serif;
    font-size: 11pt;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {c.accent.name()};
}}
QLineEdit:disabled {{
    background: {c.surface.name()};
    color: {c.text_disabled.name()};
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c.text_secondary.name()};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {c.surface.name()};
    border: 1px solid {c.divider.name()};
    selection-background-color: {c.accent_muted.name()};
    selection-color: {c.accent.name()};
    outline: none;
}}

/* ── 标签页 ───────────────────────────────── */
QTabWidget::pane {{
    background: {c.background.name()};
    border: none;
    border-top: 1px solid {c.divider.name()};
}}
QTabBar::tab {{
    background: {c.surface.name()};
    color: {c.text_secondary.name()};
    border: none; padding: 10px 24px; margin-right: 2px;
    font-size: 11pt;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {c.accent.name()};
    background: {c.background.name()};
    border-bottom: 2px solid {c.accent.name()};
}}
QTabBar::tab:hover:!selected {{
    color: {c.text_primary.name()};
    background: {c.surface_hover.name()};
}}

/* ── 分组框 ───────────────────────────────── */
QGroupBox {{
    border: 1px solid {c.divider.name()};
    border-radius: 4px; margin-top: 12px; padding-top: 16px;
    font-weight: 600; color: {c.accent.name()};
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 6px;
}}

/* ── 工具提示 ─────────────────────────────── */
QToolTip {{
    background: {c.surface_hover.name()};
    color: {c.text_primary.name()};
    border: 1px solid {c.accent.name()};
    padding: 6px 10px; border-radius: 3px; font-size: 10pt;
}}

/* ── 标签 ─────────────────────────────────── */
QLabel#statusLabel {{
    color: {c.text_secondary.name()};
    font-size: 10pt; font-style: italic;
}}
QLabel#brandLabel {{
    font-family: "Cormorant Garamond", "Microsoft YaHei", serif;
    font-size: 22pt; font-weight: 600;
    color: {c.accent.name()}; letter-spacing: 1px;
}}
QLabel#fileLabel {{
    color: {c.text_secondary.name()};
    font-size: 10pt; padding: 6px 0;
}}
"""


# ═══════════════════════════════════════════════════════════════
# 便捷函数（兼容旧代码）
# ═══════════════════════════════════════════════════════════════

def build_dark_palette() -> QPalette:
    return build_palette(DARK_PALETTE)


def build_light_palette() -> QPalette:
    return build_palette(LIGHT_PALETTE)


# 全局样式表别名（初始为暗色，由 App 启动时覆盖）
GLOBAL_STYLESHEET = build_stylesheet(DARK_PALETTE)

# 旧版引用兼容
GOLDEN_SPINE_STYLE = """
QFrame#goldenSpine {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop: 0 rgba(180, 148, 61, 0),
        stop: 0.2 rgba(212, 168, 83, 255),
        stop: 0.8 rgba(212, 168, 83, 255),
        stop: 1 rgba(180, 148, 61, 0));
    border: none;
    min-width: 3px;
    max-width: 3px;
}
"""

# 分隔线样式（兼容旧代码）
DIVIDER_STYLE = """
QFrame#sectionDivider {
    background: #2a2a30;
    border: none;
    min-height: 1px;
    max-height: 1px;
}
"""