# -*- coding: utf-8 -*-
"""Compact persistent top bar: connection controls + segmented tab switcher."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel
from qfluentwidgets import (
    ComboBox,
    FluentIcon as FIF,
    PrimaryPushButton,
    SegmentedWidget,
    SwitchButton,
    ToolButton,
)

from .theme_tokens import card_frame_qss, current_palette


class PowerTopBar(QFrame):
    """Sleek 56 px header bar shared across all 4 power workbench tabs."""

    tabChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PowerTopBar")
        self.setFixedHeight(58)

        self.titleLabel = QLabel("UF4 Power", self)
        title_font = QFont()
        title_font.setPointSize(13)
        title_font.setWeight(QFont.DemiBold)
        self.titleLabel.setFont(title_font)

        self.statusPill = QLabel("○ 未连接", self)
        self.statusPill.setObjectName("ConnectionStatusPill")
        self.statusPill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.statusPill.setFixedHeight(26)
        self.statusPill.setMinimumWidth(78)

        self.pivot = SegmentedWidget(self)
        self.pivot.setFixedHeight(34)
        self.pivot.addItem("dashboard", "仪表盘")
        self.pivot.addItem("calibration", "多点校准")
        self.pivot.addItem("protection", "保护与诊断")
        self.pivot.addItem("logs", "通信日志")
        self.pivot.setCurrentItem("dashboard")
        self.pivot.currentItemChanged.connect(self.tabChanged.emit)

        self.portCombo = ComboBox(self)
        self.portCombo.setFixedWidth(198)
        self.portCombo.setFixedHeight(32)

        self.refreshPortsButton = ToolButton(FIF.SYNC, self)
        self.refreshPortsButton.setFixedSize(32, 32)
        self.refreshPortsButton.setToolTip("刷新串口列表")

        self.connectButton = PrimaryPushButton(FIF.LINK, "连接", self)
        self.connectButton.setFixedHeight(32)
        self.connectButton.setMinimumWidth(82)

        self.autoPollSwitch = SwitchButton(self)
        self.autoPollSwitch.setOnText("实时流")
        self.autoPollSwitch.setOffText("手动")
        self.autoPollSwitch.setChecked(True)

        self._online = False
        self._portText = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(10)
        layout.addWidget(self.titleLabel)
        layout.addWidget(self.statusPill)
        layout.addSpacing(12)
        layout.addWidget(self.pivot)
        layout.addStretch(1)
        layout.addWidget(self.autoPollSwitch)
        layout.addSpacing(4)
        layout.addWidget(self.portCombo)
        layout.addWidget(self.refreshPortsButton)
        layout.addWidget(self.connectButton)

        self.refreshTheme()

    def setOnline(self, online: bool, port: str = "") -> None:
        self._online = online
        self._portText = port if online else ""
        if online:
            text = f"● {port}" if port else "● 已连接"
            self.statusPill.setText(text)
            self.connectButton.setText("断开")
        else:
            self.statusPill.setText("○ 未连接")
            self.connectButton.setText("连接")
        self._applyPillTheme()

    def _applyPillTheme(self) -> None:
        p = current_palette()
        if self._online:
            bg, fg, border = p.ok_bg, p.ok_fg, p.ok_border
        else:
            bg, fg, border = p.surface_subtle, p.text_secondary, p.border
        self.statusPill.setStyleSheet(
            f"""
            QLabel#ConnectionStatusPill {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 13px;
                padding: 0 10px;
                font-size: 12px;
                font-weight: 600;
            }}
            """
        )

    def refreshTheme(self) -> None:
        p = current_palette()
        self.setStyleSheet(card_frame_qss("PowerTopBar", p, radius=12))
        self.titleLabel.setStyleSheet(f"color: {p.text_primary}; background: transparent;")
        self._applyPillTheme()
