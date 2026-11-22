# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt5.QtCore import Qt
from qfluentwidgets import MSFluentWindow

from app.widgets.components import DynamicIsland

from .navigation import NavigationMixin
from .shell import WindowShellMixin
from .theme import ThemeBackgroundMixin


class Window(WindowShellMixin, NavigationMixin, ThemeBackgroundMixin, MSFluentWindow):
    """Main application window, composed from focused window concerns."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Window")
        self.initWindowShell()

        self.createPages()
        self.registerNavigation()

        self.titleBar.raise_()
        self.titleBar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        if hasattr(self.titleBar, "iconLabel"):
            self.titleBar.iconLabel.hide()

        self.dynamicIsland = DynamicIsland(self.titleBar)
        self.dynamicIsland.recenter()
        self.dynamicIsland.raise_()

        self.initThemeBackground()
        self.show()

    def showDynamicIsland(self, title: str, content: str = "", level: str = "info", duration: int = 3200):
        self.dynamicIsland.notify(title, content, level, duration)
