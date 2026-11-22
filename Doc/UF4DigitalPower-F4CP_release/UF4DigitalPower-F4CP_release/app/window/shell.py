# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication


class WindowShellMixin:
    """Window size, title, icon, and screen placement."""

    def initWindowShell(self) -> None:
        desktop = QApplication.desktop().availableGeometry()
        width = min(1360, max(960, int(desktop.width() * 0.92)))
        height = min(960, max(700, int(desktop.height() * 0.90)))
        self.resize(width, height)
        self.setWindowIcon(QIcon())
        self.setWindowTitle("F4CP")
        if hasattr(self, "titleBar") and hasattr(self.titleBar, "iconLabel"):
            self.titleBar.iconLabel.hide()
        self.centerOnScreen()

    def centerOnScreen(self) -> None:
        desktop = QApplication.desktop().availableGeometry()
        self.move(
            desktop.width() // 2 - self.width() // 2,
            desktop.height() // 2 - self.height() // 2,
        )
