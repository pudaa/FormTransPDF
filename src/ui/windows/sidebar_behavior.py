"""
侧边栏行为 — 收起/展开动画与缩放状态标签。

以 mixin 形式注入 MainWindow（windows/main_window.py），
依赖 self._sidebar / self._sidebar_sep / self._sidebar_visible /
self._sidebar_anim / self._viewer / self._zoom_label 等状态。
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QVariantAnimation


class _SidebarBehaviorMixin:
    """侧边栏折叠动画与缩放百分比标签。"""

    def _toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        target_w = self.SIDEBAR_WIDTH if self._sidebar_visible else 0
        start_w = self._sidebar.width()

        # 确保展开前 widget 可见
        if self._sidebar_visible:
            self._sidebar.setVisible(True)
            self._sidebar_sep.setVisible(True)

        # 如果已在目标宽度，跳过
        if start_w == target_w:
            if not self._sidebar_visible:
                self._on_sidebar_collapsed()
            return

        # ── 使用 QVariantAnimation 驱动 setFixedWidth，
        #    直接强制宽度，不受布局缓存 / 子控件 min-size 干扰 ──
        self._sidebar_anim = QVariantAnimation()
        self._sidebar_anim.setDuration(260)
        self._sidebar_anim.setStartValue(start_w)
        self._sidebar_anim.setEndValue(target_w)
        self._sidebar_anim.setEasingCurve(
            QEasingCurve.Type.OutCubic if self._sidebar_visible
            else QEasingCurve.Type.InCubic
        )

        def _drive(value: float) -> None:
            w = int(value)
            self._sidebar.setFixedWidth(w)
            self._sidebar_sep.setFixedWidth(2 if w > 10 else 0)

        self._sidebar_anim.valueChanged.connect(_drive)

        if not self._sidebar_visible:
            self._sidebar_anim.finished.connect(self._on_sidebar_collapsed)
        else:
            self._sidebar_anim.finished.connect(self._on_sidebar_expanded)

        self._sidebar_anim.finished.connect(self._on_sidebar_anim_finished)
        self._sidebar_anim.start()

    def _on_sidebar_collapsed(self) -> None:
        """收起动画结束后隐藏侧边栏和分隔线"""
        self._sidebar.setVisible(False)
        self._sidebar_sep.setVisible(False)

    def _on_sidebar_expanded(self) -> None:
        """展开动画结束后恢复约束（为下次收起动画做准备）"""
        # 从 setFixedWidth 的锁死状态恢复为可动画的 min/max 模式
        self._sidebar.setMinimumWidth(0)
        self._sidebar.setMaximumWidth(self.SIDEBAR_WIDTH)
        self._sidebar_sep.setMinimumWidth(0)
        self._sidebar_sep.setMaximumWidth(2)

    def _on_sidebar_anim_finished(self) -> None:
        """动画结束后重定位 minimap（布局已稳定）"""
        if self._minimap and self._minimap.isVisible():
            self._position_minimap()

    def _update_zoom_label(self) -> None:
        if self._viewer.is_fit_width:
            self._zoom_label.setText("适应")
        else:
            self._zoom_label.setText(f"{int(self._viewer.scale * 100)}%")
