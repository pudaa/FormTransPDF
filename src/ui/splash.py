"""
启动画面 — 在 babeldoc 初始化和应用就绪前展示
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QSplashScreen


class StartupSplash(QSplashScreen):
    """FormTransPDF 启动画面 — 简约学术风"""

    def __init__(self) -> None:
        # 创建半透明底图
        pixmap = QPixmap(420, 180)
        pixmap.fill(Qt.GlobalColor.transparent)

        super().__init__(pixmap)
        self.setWindowFlags(
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )

    def drawContents(self, painter: QPainter) -> None:
        """自定义绘制启动画面内容"""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        # 半透明背景卡片
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(22, 22, 26, 230))
        painter.drawRoundedRect(10, 10, w - 20, h - 20, 12, 12)

        # 品牌名
        brand_font = QFont("Cormorant Garamond", 28)
        brand_font.setWeight(QFont.Weight.Bold)
        painter.setFont(brand_font)
        painter.setPen(QColor("#d4a853"))
        painter.drawText(20, 50, w - 40, 40, Qt.AlignmentFlag.AlignHCenter, "FormTransPDF")

        # 副标题
        sub_font = QFont("Microsoft YaHei", 11)
        painter.setFont(sub_font)
        painter.setPen(QColor("#8a8578"))
        painter.drawText(20, 90, w - 40, 24, Qt.AlignmentFlag.AlignHCenter, "PDF 科学论文翻译工坊")

        # 加载提示
        hint_font = QFont("Microsoft YaHei", 9)
        hint_font.setItalic(True)
        painter.setFont(hint_font)
        painter.setPen(QColor("#6b6152"))
        painter.drawText(20, 130, w - 40, 22, Qt.AlignmentFlag.AlignHCenter, "正在初始化翻译引擎…")
