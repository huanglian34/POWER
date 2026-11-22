# -*- coding: utf-8 -*-
"""Protection thresholds + 9-item hardware diagnostics tab."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import DoubleSpinBox, FluentIcon as FIF, PrimaryPushButton, PushButton, SpinBox

from app.session import PowerStatus, pretty_faults

from .theme_tokens import card_frame_qss, current_palette, make_mono_font


class _DiagTile(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("LabDiagTile")
        self.titleLabel = QLabel(title, self)
        self.valueLabel = QLabel("--", self)
        self.valueLabel.setFont(make_mono_font(13, QFont.DemiBold))
        self.valueLabel.setWordWrap(True)
        self.valueLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._highlightDanger = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(self.titleLabel)
        layout.addWidget(self.valueLabel)
        layout.addStretch(1)
        self.setMinimumHeight(88)
        self.refreshTheme()

    def setValue(self, text: str, *, danger: bool = False) -> None:
        self.valueLabel.setText(text)
        if self._highlightDanger != danger:
            self._highlightDanger = danger
            self.refreshTheme()

    def refreshTheme(self) -> None:
        p = current_palette()
        if self._highlightDanger:
            bg, border, val_color = p.danger_bg, p.danger_border, p.danger_fg
        else:
            bg, border, val_color = p.surface_subtle, p.border, p.text_primary
        self.setStyleSheet(
            f"QFrame#LabDiagTile {{ background-color: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
        )
        self.titleLabel.setStyleSheet(f"color: {p.text_secondary}; font-size: 11px; background: transparent;")
        self.valueLabel.setStyleSheet(f"color: {val_color}; background: transparent;")


class ProtectionTab(QWidget):
    """Balanced 2-column view: Left (OVP/OCP/OTP/Fan form) | Right (Hardware diagnostics)."""

    applyProtectionRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PowerProtectionTab")
        self._updatingFromStatus = False
        self._dirtyEditors: set[QWidget] = set()

        # --- Left Card: Protection Thresholds ---
        self.protectCard = QFrame(self)
        self.protectCard.setObjectName("LabProtectCard")
        self.protectCard.setFixedWidth(400)

        self.protectTitle = QLabel("保护与散热阈值", self.protectCard)
        self.protectSub = QLabel("硬件比较器 DAC 阈值实时联动下发", self.protectCard)

        self.ovp = self._makeDoubleSpin("V", 0.0, 65.0, 0.1, self.protectCard)
        self.ocp = self._makeDoubleSpin("A", 0.0, 12.0, 0.1, self.protectCard)
        self.otp = self._makeDoubleSpin("°C", 40.0, 150.0, 1.0, self.protectCard, decimals=1)
        self.fan = SpinBox(self.protectCard)
        self.fan.setRange(0, 1000)
        self.fan.setSingleStep(20)
        self.fan.setSuffix(" ‰")
        self.fan.setFixedHeight(40)
        self.fan.setFont(make_mono_font(11, QFont.Medium))
        self.fan.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.fan.setAccelerated(True)

        self._editors = (self.ovp, self.ocp, self.otp, self.fan)
        for editor in self._editors:
            editor.valueChanged.connect(lambda _v, item=editor: self._markDirty(item))
            try:
                editor.lineEdit().textEdited.connect(lambda _t, item=editor: self._markDirty(item))
            except Exception:
                pass

        self.applyProtectButton = PrimaryPushButton(FIF.SAVE, "应用保护阈值", self.protectCard)
        self.applyProtectButton.setFixedHeight(42)
        self.applyProtectButton.clicked.connect(self.applyProtectionRequested.emit)

        self._fieldLabels: list[QLabel] = []
        formLayout = QVBoxLayout(self.protectCard)
        formLayout.setContentsMargins(22, 20, 22, 20)
        formLayout.setSpacing(10)
        formLayout.addWidget(self.protectTitle)
        formLayout.addWidget(self.protectSub)
        formLayout.addSpacing(8)
        for caption, widget in (
            ("过压保护 OVP（上限 65.000 V）", self.ovp),
            ("过流保护 OCP（上限 12.000 A）", self.ocp),
            ("过温保护 OTP（40 ~ 150 °C）", self.otp),
            ("风扇转速设定（0 ~ 1000 ‰）", self.fan),
        ):
            lbl = QLabel(caption, self.protectCard)
            self._fieldLabels.append(lbl)
            formLayout.addWidget(lbl)
            formLayout.addWidget(widget)
            formLayout.addSpacing(4)
        formLayout.addSpacing(6)
        formLayout.addWidget(self.applyProtectButton)
        formLayout.addStretch(1)

        # --- Right Card: Hardware Diagnostics ---
        self.diagCard = QFrame(self)
        self.diagCard.setObjectName("LabDiagCard")
        self.diagTitle = QLabel("系统状态与硬件诊断", self.diagCard)
        self.refreshButton = PushButton(FIF.SYNC, "立即刷新", self.diagCard)
        self.debugButton = PushButton(FIF.SEARCH, "调试快照", self.diagCard)
        self.refreshButton.setFixedHeight(34)
        self.debugButton.setFixedHeight(34)

        diagHeader = QHBoxLayout()
        diagHeader.setContentsMargins(0, 0, 0, 0)
        diagHeader.setSpacing(8)
        diagHeader.addWidget(self.diagTitle)
        diagHeader.addStretch(1)
        diagHeader.addWidget(self.refreshButton)
        diagHeader.addWidget(self.debugButton)

        self.tiles = {
            "output": _DiagTile("输出使能", self.diagCard),
            "topology": _DiagTile("变换拓扑", self.diagCard),
            "mode": _DiagTile("控制模式", self.diagCard),
            "state": _DiagTile("状态机标志", self.diagCard),
            "fault": _DiagTile("当前故障", self.diagCard),
            "core_temp": _DiagTile("MCU 核心温度", self.diagCard),
            "board_temp": _DiagTile("功率板温度", self.diagCard),
            "pwm": _DiagTile("HRTIM PWM A / D", self.diagCard),
            "fan": _DiagTile("风扇转速 / 设定", self.diagCard),
        }

        tileGrid = QGridLayout()
        tileGrid.setContentsMargins(0, 8, 0, 0)
        tileGrid.setHorizontalSpacing(12)
        tileGrid.setVerticalSpacing(12)
        order = ("output", "topology", "mode", "state", "fault", "core_temp", "board_temp", "pwm", "fan")
        for idx, key in enumerate(order):
            tileGrid.addWidget(self.tiles[key], idx // 3, idx % 3)

        diagLayout = QVBoxLayout(self.diagCard)
        diagLayout.setContentsMargins(22, 20, 22, 20)
        diagLayout.setSpacing(12)
        diagLayout.addLayout(diagHeader)
        diagLayout.addLayout(tileGrid)
        diagLayout.addStretch(1)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(self.protectCard)
        root.addWidget(self.diagCard, 1)

        self.refreshTheme()

    @staticmethod
    def _makeDoubleSpin(suffix: str, low: float, high: float, step: float, parent, decimals: int = 3) -> DoubleSpinBox:
        spin = DoubleSpinBox(parent)
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setSuffix(f" {suffix}")
        spin.setFixedHeight(40)
        spin.setFont(make_mono_font(11, QFont.Medium))
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        spin.setAccelerated(True)
        return spin

    def _markDirty(self, editor: QWidget) -> None:
        if not self._updatingFromStatus:
            self._dirtyEditors.add(editor)

    def clearDirty(self) -> None:
        self._dirtyEditors.clear()

    @staticmethod
    def _hasFocus(editor) -> bool:
        try:
            return editor.hasFocus() or editor.lineEdit().hasFocus()
        except Exception:
            return editor.hasFocus()

    def setWriteEnabled(self, enabled: bool) -> None:
        for editor in self._editors:
            editor.setEnabled(enabled)
        self.applyProtectButton.setEnabled(enabled)
        self.refreshButton.setEnabled(enabled)
        self.debugButton.setEnabled(enabled)

    def setFromStatus(self, status: PowerStatus) -> None:
        self._updatingFromStatus = True
        try:
            values = (
                (self.ovp, status.ovp_set_value_v),
                (self.ocp, status.ocp_set_value_a),
                (self.otp, status.otp_set_value_c),
                (self.fan, status.fan_set_value),
            )
            for editor, val in values:
                if val is None or editor in self._dirtyEditors or self._hasFocus(editor):
                    continue
                editor.setValue(val)
        finally:
            self._updatingFromStatus = False

        faults = pretty_faults(status.fault_state)
        has_fault = status.fault_state != 0
        self.tiles["output"].setValue("已开启 (ON)" if status.power_enabled else "已关闭 (OFF)")
        self.tiles["topology"].setValue(status.topology_name)
        self.tiles["mode"].setValue(status.mode_name)
        self.tiles["state"].setValue(status.state_flag_name)
        self.tiles["fault"].setValue(faults if has_fault else "无故障", danger=has_fault)
        self.tiles["core_temp"].setValue(f"{status.core_temp_c:.2f} °C")
        self.tiles["board_temp"].setValue(f"{status.board_temp_c:.2f} °C")
        self.tiles["pwm"].setValue(f"{status.pwm_a_compare} / {status.pwm_d_compare}")
        self.tiles["fan"].setValue(f"{status.fan_speed} / 设定 {status.fan_set_value}")

    def refreshTheme(self) -> None:
        p = current_palette()
        self.protectCard.setStyleSheet(card_frame_qss("LabProtectCard", p, radius=12))
        self.diagCard.setStyleSheet(card_frame_qss("LabDiagCard", p, radius=12))
        self.protectTitle.setStyleSheet(f"color: {p.text_primary}; font-size: 14px; font-weight: 600; background: transparent;")
        self.protectSub.setStyleSheet(f"color: {p.text_muted}; font-size: 12px; background: transparent;")
        self.diagTitle.setStyleSheet(f"color: {p.text_primary}; font-size: 14px; font-weight: 600; background: transparent;")
        for lbl in self._fieldLabels:
            lbl.setStyleSheet(f"color: {p.text_secondary}; font-size: 12px; font-weight: 600; background: transparent;")
        for tile in self.tiles.values():
            tile.refreshTheme()
