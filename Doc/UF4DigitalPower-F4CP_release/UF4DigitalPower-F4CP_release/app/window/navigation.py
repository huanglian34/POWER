# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QWidget
from qfluentwidgets import FluentIconBase, NavigationItemPosition

from app.widgets.icon import UF4Icon
from app.widgets.pages import (
    DevicePage,
    PowerPage,
    SettingsPage,
)


@dataclass(frozen=True)
class NavigationEntry:
    attr_name: str
    interface_attr: str
    icon: FluentIconBase | QIcon | str
    text: str
    selected_icon: FluentIconBase | QIcon | str
    position: NavigationItemPosition = NavigationItemPosition.TOP


NAVIGATION_ENTRIES = (
    NavigationEntry("powerNavItem"  ,  "powerInterface"  , UF4Icon.DEVELOPER_BOARD, "电源", UF4Icon.DEVELOPER_BOARD_FILL),
    NavigationEntry("deviceNavItem" ,  "deviceInterface" , UF4Icon.SERIAL_PORT, "串口", UF4Icon.SERIAL_PORT_FILL),
    NavigationEntry("settingNavItem",  "settingInterface", UF4Icon.SERVER,"设置", UF4Icon.SERVER_FILL,NavigationItemPosition.BOTTOM),
)


class NavigationMixin:
    """Page construction and navigation registration."""

    deviceInterface: DevicePage
    powerInterface: PowerPage
    settingInterface: SettingsPage

    def createPages(self) -> None:
        self.deviceInterface = DevicePage(self)
        self.powerInterface = PowerPage(self)
        self.settingInterface = SettingsPage(self)

    def registerNavigation(self) -> None:
        for entry in NAVIGATION_ENTRIES:
            interface = getattr(self, entry.interface_attr)
            self._addNavigationEntry(entry, interface)

        self.switchTo(self.powerInterface)

    def _addNavigationEntry(self, entry: NavigationEntry, interface: QWidget) -> None:
        item = self.addSubInterface(
            interface,
            entry.icon,
            entry.text,
            entry.selected_icon,
            entry.position,
        )
        setattr(self, entry.attr_name, item)
