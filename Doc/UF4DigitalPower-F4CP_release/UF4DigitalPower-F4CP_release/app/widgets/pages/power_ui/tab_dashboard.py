# -*- coding: utf-8 -*-
"""Dashboard tab: bench instrument readout + 60 FPS scope + slider & customizable preset control dock."""

from __future__ import annotations

import math
import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    CheckBox,
    DoubleSpinBox,
    FluentIcon as FIF,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    Slider,
    SubtitleLabel,
)

from app.session import PowerStatus, pretty_faults

from .lab_scope import LabScopeWidget
from .preset_store import (
    MAX_CURRENT_A,
    MAX_VOLTAGE_V,
    PRESET_COUNT,
    OutputPreset,
    OutputPresetStore,
)
from .theme_tokens import card_frame_qss, current_palette, make_mono_font

VOLTAGE_SLIDER_STEP = 0.1  # 0.1 V per tick (0..650 -> 0.0..65.0 V)
CURRENT_SLIDER_STEP = 0.05  # 0.05 A per tick (0..200 -> 0.00..10.00 A)
SLIDER_DEBOUNCE_MS = 180


class _HeroMetricColumn(QWidget):
    """Single clean readout column inside the unified instrument header."""

    def __init__(self, title: str, unit: str, color_key: str, parent=None):
        super().__init__(parent)
        self._color_key = color_key
        self.dotLabel = QLabel("●", self)
        self.titleLabel = QLabel(title, self)
        self.valueLabel = QLabel("--.---", self)
        self.unitLabel = QLabel(unit, self)
        self.subLabel = QLabel("设定 --", self)

        self.valueLabel.setFont(make_mono_font(30, QFont.DemiBold))
        self.unitLabel.setFont(make_mono_font(14, QFont.DemiBold))

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self.dotLabel)
        top.addWidget(self.titleLabel)
        top.addStretch(1)

        val_row = QHBoxLayout()
        val_row.setContentsMargins(0, 0, 0, 0)
        val_row.setSpacing(6)
        val_row.addWidget(self.valueLabel)
        val_row.addWidget(self.unitLabel, 0, Qt.AlignmentFlag.AlignBottom)
        val_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addLayout(val_row)
        layout.addWidget(self.subLabel)
        self.refreshTheme()

    def setReading(self, value_text: str, sub_text: str) -> None:
        self.valueLabel.setText(value_text)
        self.subLabel.setText(sub_text)

    def refreshTheme(self) -> None:
        p = current_palette()
        accent = getattr(p, self._color_key, p.accent)
        self.dotLabel.setStyleSheet(f"color: {accent}; font-size: 10px; background: transparent;")
        self.titleLabel.setStyleSheet(f"color: {p.text_secondary}; font-size: 12px; font-weight: 600; background: transparent;")
        self.valueLabel.setStyleSheet(f"color: {p.text_primary}; background: transparent;")
        self.unitLabel.setStyleSheet(f"color: {accent}; padding-bottom: 5px; background: transparent;")
        self.subLabel.setStyleSheet(f"color: {p.text_muted}; font-size: 12px; background: transparent;")


class _PresetEditDialog(MessageBoxBase):
    """Fluent modal dialog for customizing a single M1..M6 voltage/current preset."""

    def __init__(
        self,
        slot_index: int,
        preset: OutputPreset,
        current_voltage_v: float,
        current_current_a: float,
        parent=None,
    ):
        super().__init__(parent)
        self._slotIndex = slot_index
        self._curV = current_voltage_v
        self._curI = current_current_a

        self.titleLabel = SubtitleLabel(f"自定义挡位 M{slot_index + 1}", self)

        self.nameLabel = QLabel("挡位名称（最多 12 字）", self)
        self.nameEdit = LineEdit(self)
        self.nameEdit.setText(preset.name)
        self.nameEdit.setMaxLength(12)
        self.nameEdit.setPlaceholderText(f"例如：12V 常用")

        self.voltageLabel = QLabel(f"目标电压（0.000 ~ {MAX_VOLTAGE_V:.0f}.000 V）", self)
        self.voltageSpin = DoubleSpinBox(self)
        self.voltageSpin.setRange(0.0, MAX_VOLTAGE_V)
        self.voltageSpin.setDecimals(3)
        self.voltageSpin.setSingleStep(0.1)
        self.voltageSpin.setSuffix(" V")
        self.voltageSpin.setValue(preset.voltage_v)
        self.voltageSpin.setFont(make_mono_font(11, QFont.DemiBold))
        self.voltageSpin.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.currentLabel = QLabel(f"限流设定（0.000 ~ {MAX_CURRENT_A:.0f}.000 A）", self)
        self.currentSpin = DoubleSpinBox(self)
        self.currentSpin.setRange(0.0, MAX_CURRENT_A)
        self.currentSpin.setDecimals(3)
        self.currentSpin.setSingleStep(0.1)
        self.currentSpin.setSuffix(" A")
        self.currentSpin.setValue(preset.current_a)
        self.currentSpin.setFont(make_mono_font(11, QFont.DemiBold))
        self.currentSpin.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.fillCurrentBtn = PushButton(
            FIF.SYNC,
            f"填入当前设定（{current_voltage_v:.2f} V / {current_current_a:.2f} A）",
            self,
        )
        self.fillCurrentBtn.clicked.connect(self._fillFromCurrent)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.nameLabel)
        self.viewLayout.addWidget(self.nameEdit)
        self.viewLayout.addWidget(self.voltageLabel)
        self.viewLayout.addWidget(self.voltageSpin)
        self.viewLayout.addWidget(self.currentLabel)
        self.viewLayout.addWidget(self.currentSpin)
        self.viewLayout.addWidget(self.fillCurrentBtn)

        self.yesButton.setText("保存挡位")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(360)

    def _fillFromCurrent(self) -> None:
        self.voltageSpin.setValue(self._curV)
        self.currentSpin.setValue(self._curI)

    def resultPreset(self) -> OutputPreset:
        self.voltageSpin.interpretText()
        self.currentSpin.interpretText()
        return OutputPreset(
            name=self.nameEdit.text().strip() or f"挡位 {self._slotIndex + 1}",
            voltage_v=self.voltageSpin.value(),
            current_a=self.currentSpin.value(),
        ).normalized(self._slotIndex)


class _PresetSlotCard(QFrame):
    """Clickable M1..M6 memory slot card with inline edit button & context menu."""

    activated = pyqtSignal(int)
    editRequested = pyqtSignal(int)
    saveCurrentRequested = pyqtSignal(int)
    resetRequested = pyqtSignal(int)

    def __init__(self, index: int, preset: OutputPreset, parent=None):
        super().__init__(parent)
        self.index = index
        self._preset = preset
        self._active = False
        self.setObjectName("LabPresetSlot")
        self.setFixedHeight(48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._showContextMenu)

        self.tagLabel = QLabel(f"M{index + 1}", self)
        self.tagLabel.setFont(make_mono_font(8, QFont.Bold))
        self.tagLabel.setFixedHeight(16)
        self.tagLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.nameLabel = QLabel(preset.name, self)
        self.nameLabel.setMinimumWidth(0)
        self.editBtn = QPushButton("✎", self)
        self.editBtn.setFixedSize(18, 18)
        self.editBtn.setToolTip(f"编辑挡位 M{index + 1}")
        self.editBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.editBtn.clicked.connect(lambda: self.editRequested.emit(self.index))

        topRow = QHBoxLayout()
        topRow.setContentsMargins(0, 0, 0, 0)
        topRow.setSpacing(5)
        topRow.addWidget(self.tagLabel)
        topRow.addWidget(self.nameLabel, 1)
        topRow.addWidget(self.editBtn)

        self.valueLabel = QLabel("", self)
        self.valueLabel.setFont(make_mono_font(9, QFont.DemiBold))
        self.valueLabel.setMinimumWidth(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 6, 4)
        layout.setSpacing(2)
        layout.addLayout(topRow)
        layout.addWidget(self.valueLabel)

        self.setPreset(preset)
        self.refreshTheme()

    @staticmethod
    def _fmtCompact(val: float, unit: str) -> str:
        milli = int(round(val * 1000))
        if milli % 100 == 0:
            return f"{val:.1f}{unit}"
        if milli % 10 == 0:
            return f"{val:.2f}{unit}"
        return f"{val:.3f}{unit}"

    def setPreset(self, preset: OutputPreset) -> None:
        self._preset = preset
        self.nameLabel.setText(preset.name)
        v_str = self._fmtCompact(preset.voltage_v, "V")
        i_str = self._fmtCompact(preset.current_a, "A")
        self.valueLabel.setText(f"{v_str} / {i_str}")
        self.setToolTip(
            f"M{self.index + 1} · {preset.name}\n"
            f"目标电压: {preset.voltage_v:.3f} V\n"
            f"限流设定: {preset.current_a:.3f} A\n"
            f"左键快速应用 · 点 ✎ 或右键自定义修改"
        )

    def setActive(self, active: bool) -> None:
        val = bool(active)
        if self._active != val:
            self._active = val
            self.refreshTheme()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.activated.emit(self.index)
        super().mouseReleaseEvent(event)

    def _showContextMenu(self, pos) -> None:
        menu = RoundMenu(parent=self)
        menu.addAction(Action(FIF.EDIT, f"编辑挡位 M{self.index + 1}...", triggered=lambda: self.editRequested.emit(self.index)))
        menu.addAction(
            Action(FIF.SAVE, f"用当前电压/电流覆盖 M{self.index + 1}", triggered=lambda: self.saveCurrentRequested.emit(self.index))
        )
        menu.addAction(Action(FIF.SYNC, f"恢复 M{self.index + 1} 默认值", triggered=lambda: self.resetRequested.emit(self.index)))
        menu.exec(self.mapToGlobal(pos))

    def refreshTheme(self) -> None:
        p = current_palette()
        if self._active:
            bg = p.accent_soft_bg
            border = p.accent
            hover_bg = p.accent_soft_bg
            tag_bg = p.accent
            tag_fg = "#FFFFFF"
            val_fg = p.accent
        else:
            bg = p.surface_subtle
            border = p.border
            hover_bg = p.accent_soft_bg
            tag_bg = p.border
            tag_fg = p.text_secondary
            val_fg = p.text_primary

        self.setStyleSheet(
            f"""
            QFrame#LabPresetSlot {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QFrame#LabPresetSlot:hover {{
                background-color: {hover_bg};
                border-color: {p.accent};
            }}
            """
        )
        self.tagLabel.setStyleSheet(
            f"background-color: {tag_bg}; color: {tag_fg}; border-radius: 4px; padding: 0 4px;"
        )
        self.nameLabel.setStyleSheet(
            f"color: {p.text_secondary}; font-size: 11px; font-weight: 600; background: transparent;"
        )
        self.valueLabel.setStyleSheet(f"color: {val_fg}; background: transparent;")
        self.editBtn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {p.text_muted}; border: none; border-radius: 4px; font-size: 11px; }}"
            f"QPushButton:hover {{ background-color: {p.border}; color: {p.text_primary}; }}"
        )


class DashboardTab(QWidget):
    """Main bench workbench: Left (Instrument Header + 60 FPS Scope) | Right (Slider + Preset Control Dock)."""

    outputToggleRequested = pyqtSignal(bool)
    clearFaultRequested = pyqtSignal()
    applyOutputRequested = pyqtSignal()
    quickApplyOutputRequested = pyqtSignal(int, int, str)

    def __init__(self, parent=None, *, preset_store: OutputPresetStore | None = None):
        super().__init__(parent)
        self.setObjectName("PowerDashboardTab")
        self._updatingFromStatus = False
        self._syncingControls = False
        self._sliderDragging = False
        self._dirtyEditors: set[QWidget] = set()
        self._outputEnabled = False
        self._writeEnabled = False
        self._writeBusy = False
        self._faultActive = False

        self._presetStore = preset_store if preset_store is not None else OutputPresetStore()

        # Debounce timer so dragging sliders never spams serial writes
        self._sliderApplyTimer = QTimer(self)
        self._sliderApplyTimer.setSingleShot(True)
        self._sliderApplyTimer.setInterval(SLIDER_DEBOUNCE_MS)
        self._sliderApplyTimer.timeout.connect(self._onSliderDebounceTimeout)

        # --- Left column: Unified Instrument Header + 60 FPS Realtime Scope ---
        self.headerCard = QFrame(self)
        self.headerCard.setObjectName("LabInstrumentHeader")
        self.voutCol = _HeroMetricColumn("输出电压 VOUT", "V", "vout", self.headerCard)
        self.ioutCol = _HeroMetricColumn("输出电流 IOUT", "A", "iout", self.headerCard)
        self.poutCol = _HeroMetricColumn("输出功率 POUT", "W", "pout", self.headerCard)

        self._vDiv1 = QFrame(self.headerCard)
        self._vDiv1.setFixedWidth(1)
        self._vDiv2 = QFrame(self.headerCard)
        self._vDiv2.setFixedWidth(1)

        heroRow = QHBoxLayout()
        heroRow.setContentsMargins(0, 0, 0, 0)
        heroRow.setSpacing(14)
        heroRow.addWidget(self.voutCol, 1)
        heroRow.addWidget(self._vDiv1)
        heroRow.addWidget(self.ioutCol, 1)
        heroRow.addWidget(self._vDiv2)
        heroRow.addWidget(self.poutCol, 1)

        self._hDiv = QFrame(self.headerCard)
        self._hDiv.setFixedHeight(1)

        # Compact single-row input + live status strip
        self.inputStripLabel = QLabel("输入  VIN --.--- V   ·   IIN --.--- A   ·   PIN --.-- W", self.headerCard)
        self.inputStripLabel.setFont(make_mono_font(10, QFont.Medium))

        self.topologyChip = QLabel("BUCK", self.headerCard)
        self.modeChip = QLabel("CV", self.headerCard)
        self.tempChip = QLabel("--.- °C", self.headerCard)
        self.faultChip = QLabel("状态正常", self.headerCard)
        for chip in (self.topologyChip, self.modeChip, self.tempChip, self.faultChip):
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFixedHeight(24)

        self.clearFaultButton = QPushButton("清除故障", self.headerCard)
        self.clearFaultButton.setFixedHeight(24)
        self.clearFaultButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clearFaultButton.clicked.connect(self.clearFaultRequested.emit)
        self.clearFaultButton.hide()

        stripRow = QHBoxLayout()
        stripRow.setContentsMargins(4, 2, 4, 0)
        stripRow.setSpacing(8)
        stripRow.addWidget(self.inputStripLabel)
        stripRow.addStretch(1)
        stripRow.addWidget(self.topologyChip)
        stripRow.addWidget(self.modeChip)
        stripRow.addWidget(self.tempChip)
        stripRow.addWidget(self.faultChip)
        stripRow.addWidget(self.clearFaultButton)

        headerLayout = QVBoxLayout(self.headerCard)
        headerLayout.setContentsMargins(18, 14, 18, 12)
        headerLayout.setSpacing(10)
        headerLayout.addLayout(heroRow)
        headerLayout.addWidget(self._hDiv)
        headerLayout.addLayout(stripRow)

        # Scope Card
        self.scopeCard = QFrame(self)
        self.scopeCard.setObjectName("LabScopeCard")
        self.scopeTitle = QLabel("实时波形示波器", self.scopeCard)
        self.scopeTip = QLabel("60 FPS 峰值抽稀 · 滚轮缩放 · 双击恢复跟踪", self.scopeCard)
        scopeHead = QHBoxLayout()
        scopeHead.setContentsMargins(0, 0, 0, 0)
        scopeHead.addWidget(self.scopeTitle)
        scopeHead.addStretch(1)
        scopeHead.addWidget(self.scopeTip)

        self.chart = LabScopeWidget(self.scopeCard)

        scopeLayout = QVBoxLayout(self.scopeCard)
        scopeLayout.setContentsMargins(16, 12, 16, 12)
        scopeLayout.setSpacing(8)
        scopeLayout.addLayout(scopeHead)
        scopeLayout.addWidget(self.chart, 1)

        leftCol = QVBoxLayout()
        leftCol.setContentsMargins(0, 0, 0, 0)
        leftCol.setSpacing(12)
        leftCol.addWidget(self.headerCard)
        leftCol.addWidget(self.scopeCard, 1)

        # --- Right column: Output Control Dock (Sliders + Customizable M1..M6 Presets) ---
        self.controlCard = QFrame(self)
        self.controlCard.setObjectName("LabControlDock")
        self.controlCard.setFixedWidth(386)

        self.ctrlTitle = QLabel("输出控制", self.controlCard)
        ctrl_font = QFont()
        ctrl_font.setPointSize(13)
        ctrl_font.setWeight(QFont.DemiBold)
        self.ctrlTitle.setFont(ctrl_font)

        self.liveApplyCheck = CheckBox("滑块/挡位即时下发", self.controlCard)
        self.liveApplyCheck.setChecked(self._presetStore.live_apply)
        self.liveApplyCheck.setToolTip("开启后，拖动滑块松手或点击 M1~M6 挡位立即下发到下位机；关闭则仅填值，点「应用输出设定」才生效")
        self.liveApplyCheck.stateChanged.connect(self._onLiveApplyChanged)

        ctrlHeader = QHBoxLayout()
        ctrlHeader.setContentsMargins(0, 0, 0, 0)
        ctrlHeader.addWidget(self.ctrlTitle)
        ctrlHeader.addStretch(1)
        ctrlHeader.addWidget(self.liveApplyCheck)

        self.outputToggleButton = QPushButton("○  输出已关闭 · 点击开启", self.controlCard)
        self.outputToggleButton.setObjectName("LabOutputToggleButton")
        self.outputToggleButton.setFixedHeight(44)
        self.outputToggleButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.outputToggleButton.clicked.connect(self._onToggleClicked)

        self._ctrlDiv1 = QFrame(self.controlCard)
        self._ctrlDiv1.setFixedHeight(1)

        # --- Voltage SpinBox + Fast Slider ---
        self.vsetLabel = QLabel("目标电压 (V)", self.controlCard)
        self.vsetHint = QLabel("滑块 0.1 V · 精调 1 mV", self.controlCard)
        vHead = QHBoxLayout()
        vHead.setContentsMargins(0, 0, 0, 0)
        vHead.addWidget(self.vsetLabel)
        vHead.addStretch(1)
        vHead.addWidget(self.vsetHint)

        self.outputVoltage = DoubleSpinBox(self.controlCard)
        self.outputVoltage.setRange(0.0, MAX_VOLTAGE_V)
        self.outputVoltage.setDecimals(3)
        self.outputVoltage.setSingleStep(0.01)
        self.outputVoltage.setSuffix(" V")
        self.outputVoltage.setFixedHeight(36)
        self.outputVoltage.setFont(make_mono_font(12, QFont.DemiBold))
        self.outputVoltage.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.outputVoltage.setAccelerated(True)

        self.voltageSlider = Slider(Qt.Orientation.Horizontal, self.controlCard)
        self.voltageSlider.setRange(0, int(round(MAX_VOLTAGE_V / VOLTAGE_SLIDER_STEP)))
        self.voltageSlider.setSingleStep(1)
        self.voltageSlider.setPageStep(10)
        self.voltageSlider.setFixedHeight(22)
        self.voltageSlider.setCursor(Qt.CursorShape.PointingHandCursor)

        vScaleRow = self._makeScaleRow(("0V", "12V", "24V", "36V", "48V", "65V"))

        # --- Current SpinBox + Fast Slider ---
        self.isetLabel = QLabel("限流设定 (A)", self.controlCard)
        self.isetHint = QLabel("滑块 0.05 A · 精调 1 mA", self.controlCard)
        iHead = QHBoxLayout()
        iHead.setContentsMargins(0, 0, 0, 0)
        iHead.addWidget(self.isetLabel)
        iHead.addStretch(1)
        iHead.addWidget(self.isetHint)

        self.outputCurrent = DoubleSpinBox(self.controlCard)
        self.outputCurrent.setRange(0.0, MAX_CURRENT_A)
        self.outputCurrent.setDecimals(3)
        self.outputCurrent.setSingleStep(0.01)
        self.outputCurrent.setSuffix(" A")
        self.outputCurrent.setFixedHeight(36)
        self.outputCurrent.setFont(make_mono_font(12, QFont.DemiBold))
        self.outputCurrent.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.outputCurrent.setAccelerated(True)

        self.currentSlider = Slider(Qt.Orientation.Horizontal, self.controlCard)
        self.currentSlider.setRange(0, int(round(MAX_CURRENT_A / CURRENT_SLIDER_STEP)))
        self.currentSlider.setSingleStep(1)
        self.currentSlider.setPageStep(10)
        self.currentSlider.setFixedHeight(22)
        self.currentSlider.setCursor(Qt.CursorShape.PointingHandCursor)

        iScaleRow = self._makeScaleRow(("0A", "2.5A", "5A", "7.5A", "10A"))

        self._ctrlDiv2 = QFrame(self.controlCard)
        self._ctrlDiv2.setFixedHeight(1)

        # --- Customizable Memory Presets (M1..M6, 2 cols x 3 rows) ---
        self.presetLabel = QLabel("记忆挡位（✎/右键修改）", self.controlCard)
        self.saveCurrentPresetButton = QPushButton("存至选中", self.controlCard)
        self.saveCurrentPresetButton.setFixedHeight(22)
        self.saveCurrentPresetButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.saveCurrentPresetButton.setToolTip("把当前电压和限流值直接保存到当前高亮的挡位中")
        self.saveCurrentPresetButton.clicked.connect(self._saveCurrentToActiveSlot)

        self.resetPresetsButton = QPushButton("重置", self.controlCard)
        self.resetPresetsButton.setFixedHeight(22)
        self.resetPresetsButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.resetPresetsButton.setToolTip("恢复 M1~M6 默认挡位")
        self.resetPresetsButton.clicked.connect(self._resetAllPresets)

        presetHead = QHBoxLayout()
        presetHead.setContentsMargins(0, 0, 0, 0)
        presetHead.setSpacing(6)
        presetHead.addWidget(self.presetLabel)
        presetHead.addStretch(1)
        presetHead.addWidget(self.saveCurrentPresetButton)
        presetHead.addWidget(self.resetPresetsButton)

        self._presetSlots: list[_PresetSlotCard] = []
        self._presetButtons = self._presetSlots  # alias for compatibility
        presetGrid = QGridLayout()
        presetGrid.setContentsMargins(0, 0, 0, 0)
        presetGrid.setHorizontalSpacing(8)
        presetGrid.setVerticalSpacing(6)
        for idx in range(PRESET_COUNT):
            card = _PresetSlotCard(idx, self._presetStore.get(idx), self.controlCard)
            card.activated.connect(self._onPresetActivated)
            card.editRequested.connect(self._editPresetSlot)
            card.saveCurrentRequested.connect(self._saveCurrentToSlot)
            card.resetRequested.connect(self._resetSinglePreset)
            self._presetSlots.append(card)
            presetGrid.addWidget(card, idx // 2, idx % 2)

        self.applyOutputButton = PrimaryPushButton(FIF.ACCEPT, "应用输出设定", self.controlCard)
        self.applyOutputButton.setFixedHeight(38)
        self.applyOutputButton.clicked.connect(self.applyOutputRequested.emit)

        self.ctrlFootNote = QLabel("斜坡限速 升40 / 降20 V/s · 挡位自动持久记忆", self.controlCard)
        self.ctrlFootNote.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Wire bidirectional spinbox <-> slider bindings
        self.outputVoltage.valueChanged.connect(self._onVoltageSpinChanged)
        self.outputCurrent.valueChanged.connect(self._onCurrentSpinChanged)
        for editor in (self.outputVoltage, self.outputCurrent):
            try:
                editor.lineEdit().textEdited.connect(lambda _t, item=editor: self._markDirty(item))
            except Exception:
                pass

        self.voltageSlider.sliderPressed.connect(self._onSliderPressed)
        self.voltageSlider.sliderMoved.connect(self._onVoltageSliderMoved)
        self.voltageSlider.valueChanged.connect(self._onVoltageSliderValueChanged)
        self.voltageSlider.sliderReleased.connect(self._onSliderReleased)

        self.currentSlider.sliderPressed.connect(self._onSliderPressed)
        self.currentSlider.sliderMoved.connect(self._onCurrentSliderMoved)
        self.currentSlider.valueChanged.connect(self._onCurrentSliderValueChanged)
        self.currentSlider.sliderReleased.connect(self._onSliderReleased)

        ctrlLayout = QVBoxLayout(self.controlCard)
        ctrlLayout.setContentsMargins(16, 14, 16, 14)
        ctrlLayout.setSpacing(7)
        ctrlLayout.addLayout(ctrlHeader)
        ctrlLayout.addWidget(self.outputToggleButton)
        ctrlLayout.addWidget(self._ctrlDiv1)
        ctrlLayout.addLayout(vHead)
        ctrlLayout.addWidget(self.outputVoltage)
        ctrlLayout.addWidget(self.voltageSlider)
        ctrlLayout.addLayout(vScaleRow)
        ctrlLayout.addSpacing(2)
        ctrlLayout.addLayout(iHead)
        ctrlLayout.addWidget(self.outputCurrent)
        ctrlLayout.addWidget(self.currentSlider)
        ctrlLayout.addLayout(iScaleRow)
        ctrlLayout.addSpacing(2)
        ctrlLayout.addWidget(self._ctrlDiv2)
        ctrlLayout.addLayout(presetHead)
        ctrlLayout.addLayout(presetGrid)
        ctrlLayout.addSpacing(4)
        ctrlLayout.addWidget(self.applyOutputButton)
        ctrlLayout.addStretch(1)
        ctrlLayout.addWidget(self.ctrlFootNote)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addLayout(leftCol, 1)
        root.addWidget(self.controlCard)

        self._scaleLabels: list[QLabel] = []
        for row_layout in (vScaleRow, iScaleRow):
            for idx in range(row_layout.count()):
                item = row_layout.itemAt(idx)
                if item is not None and isinstance(item.widget(), QLabel):
                    self._scaleLabels.append(item.widget())

        self._syncActiveSlotVisual()
        self.refreshTheme()

    def _makeScaleRow(self, ticks: tuple[str, ...]) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(0)
        for idx, text in enumerate(ticks):
            lbl = QLabel(text, self.controlCard)
            lbl.setFont(make_mono_font(8, QFont.Normal))
            row.addWidget(lbl)
            if idx < len(ticks) - 1:
                row.addStretch(1)
        return row

    # --- Slider & SpinBox bidirectional sync ---
    def _onVoltageSpinChanged(self, val: float) -> None:
        if not self._updatingFromStatus:
            self._dirtyEditors.add(self.outputVoltage)
        if self._syncingControls:
            return
        self._syncingControls = True
        try:
            tick = int(round(max(0.0, min(MAX_VOLTAGE_V, val)) / VOLTAGE_SLIDER_STEP))
            self.voltageSlider.setValue(tick)
        finally:
            self._syncingControls = False
        self._updateActiveSlotFromEditors()

    def _onCurrentSpinChanged(self, val: float) -> None:
        if not self._updatingFromStatus:
            self._dirtyEditors.add(self.outputCurrent)
        if self._syncingControls:
            return
        self._syncingControls = True
        try:
            tick = int(round(max(0.0, min(MAX_CURRENT_A, val)) / CURRENT_SLIDER_STEP))
            self.currentSlider.setValue(tick)
        finally:
            self._syncingControls = False
        self._updateActiveSlotFromEditors()

    def _onSliderPressed(self) -> None:
        self._sliderDragging = True

    def _onVoltageSliderMoved(self, tick: int) -> None:
        self._applyVoltageTick(tick)
        if self._writeEnabled and self.liveApplyCheck.isChecked():
            self._sliderApplyTimer.start()

    def _onVoltageSliderValueChanged(self, tick: int) -> None:
        if self._syncingControls:
            return
        self._applyVoltageTick(tick)
        if not self._sliderDragging and self._writeEnabled and self.liveApplyCheck.isChecked():
            self._sliderApplyTimer.start()

    def _onCurrentSliderMoved(self, tick: int) -> None:
        self._applyCurrentTick(tick)
        if self._writeEnabled and self.liveApplyCheck.isChecked():
            self._sliderApplyTimer.start()

    def _onCurrentSliderValueChanged(self, tick: int) -> None:
        if self._syncingControls:
            return
        self._applyCurrentTick(tick)
        if not self._sliderDragging and self._writeEnabled and self.liveApplyCheck.isChecked():
            self._sliderApplyTimer.start()

    def _applyVoltageTick(self, tick: int) -> None:
        volts = round(tick * VOLTAGE_SLIDER_STEP, 3)
        self._syncingControls = True
        try:
            self.outputVoltage.setValue(volts)
        finally:
            self._syncingControls = False
        self._markDirty(self.outputVoltage)
        self._updateActiveSlotFromEditors()

    def _applyCurrentTick(self, tick: int) -> None:
        amps = round(tick * CURRENT_SLIDER_STEP, 3)
        self._syncingControls = True
        try:
            self.outputCurrent.setValue(amps)
        finally:
            self._syncingControls = False
        self._markDirty(self.outputCurrent)
        self._updateActiveSlotFromEditors()

    def _onSliderReleased(self) -> None:
        self._sliderDragging = False
        if self._writeEnabled and self.liveApplyCheck.isChecked():
            self._sliderApplyTimer.stop()
            self._emitQuickApply("")

    def _onSliderDebounceTimeout(self) -> None:
        if self._writeEnabled and self.liveApplyCheck.isChecked():
            self._emitQuickApply("")

    def _onLiveApplyChanged(self, state: int) -> None:
        self._presetStore.set_live_apply(state == Qt.CheckState.Checked)

    def _emitQuickApply(self, reason: str) -> None:
        self.outputVoltage.interpretText()
        self.outputCurrent.interpretText()
        voltage_mv = int(round(self.outputVoltage.value() * 1000))
        current_ma = int(round(self.outputCurrent.value() * 1000))
        self.quickApplyOutputRequested.emit(voltage_mv, current_ma, reason)

    # --- Customizable Memory Preset Operations ---
    def _onPresetActivated(self, index: int) -> None:
        preset = self._presetStore.get(index)
        self._presetStore.set_active_index(index)
        self._syncingControls = True
        try:
            self.outputVoltage.setValue(preset.voltage_v)
            self.outputCurrent.setValue(preset.current_a)
            self.voltageSlider.setValue(int(round(preset.voltage_v / VOLTAGE_SLIDER_STEP)))
            self.currentSlider.setValue(int(round(preset.current_a / CURRENT_SLIDER_STEP)))
        finally:
            self._syncingControls = False
        self._markDirty(self.outputVoltage)
        self._markDirty(self.outputCurrent)
        self._syncActiveSlotVisual()

        if self._writeEnabled and self.liveApplyCheck.isChecked():
            self._sliderApplyTimer.stop()
            self._emitQuickApply(f"M{index + 1} · {preset.name}")

    def _editPresetSlot(self, index: int) -> None:
        self.outputVoltage.interpretText()
        self.outputCurrent.interpretText()
        dlg = _PresetEditDialog(
            index,
            self._presetStore.get(index),
            self.outputVoltage.value(),
            self.outputCurrent.value(),
            parent=self.window(),
        )
        if dlg.exec():
            updated = self._presetStore.set_preset(index, dlg.resultPreset())
            self._presetSlots[index].setPreset(updated)
            if self._presetStore.active_index == index:
                self._syncActiveSlotVisual()

    def _saveCurrentToActiveSlot(self) -> None:
        idx = self._presetStore.active_index
        if idx < 0 or idx >= PRESET_COUNT:
            idx = 0
        self._saveCurrentToSlot(idx)

    def _saveCurrentToSlot(self, index: int) -> None:
        self.outputVoltage.interpretText()
        self.outputCurrent.interpretText()
        existing = self._presetStore.get(index)
        updated = self._presetStore.set_preset(
            index,
            OutputPreset(
                name=existing.name,
                voltage_v=self.outputVoltage.value(),
                current_a=self.outputCurrent.value(),
            ),
        )
        self._presetStore.set_active_index(index)
        self._presetSlots[index].setPreset(updated)
        self._syncActiveSlotVisual()

    def _resetSinglePreset(self, index: int) -> None:
        restored = self._presetStore.reset_slot(index)
        self._presetSlots[index].setPreset(restored)
        self._syncActiveSlotVisual()

    def _resetAllPresets(self) -> None:
        restored = self._presetStore.reset_defaults()
        for idx, preset in enumerate(restored):
            self._presetSlots[idx].setPreset(preset)
        self._syncActiveSlotVisual()

    def _syncActiveSlotVisual(self) -> None:
        active = self._presetStore.active_index
        for idx, slot in enumerate(self._presetSlots):
            slot.setActive(idx == active)

    def _updateActiveSlotFromEditors(self) -> None:
        v = self.outputVoltage.value()
        i = self.outputCurrent.value()
        matched = -1
        for idx in range(PRESET_COUNT):
            p = self._presetStore.get(idx)
            if abs(p.voltage_v - v) < 0.002 and abs(p.current_a - i) < 0.002:
                matched = idx
                break
        if matched >= 0:
            self._presetStore.set_active_index(matched)
            self._syncActiveSlotVisual()
        else:
            for slot in self._presetSlots:
                slot.setActive(False)

    # --- Existing DashboardTab Public API ---
    def _onToggleClicked(self) -> None:
        if not self._writeEnabled or self._writeBusy:
            return
        if self._faultActive:
            self.outputToggleRequested.emit(True)
            return
        self.outputToggleRequested.emit(not self._outputEnabled)

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

    def setOutputEnabledVisual(self, enabled: bool) -> None:
        self._outputEnabled = bool(enabled)
        if self._faultActive:
            self.outputToggleButton.setText("↺  故障保护 · 点击复位并重新开启")
        elif self._outputEnabled:
            self.outputToggleButton.setText("●  输出已开启 · 点击关闭")
        else:
            self.outputToggleButton.setText("○  输出已关闭 · 点击开启")
        self._applyToggleButtonStyle()

    def setWriteEnabled(self, enabled: bool, *, busy: bool = False) -> None:
        """Enable/disable write controls. During brief in-flight writes (busy=True),
        keep sliders, spinboxes, and presets interactive so active drags are never interrupted."""
        self._writeEnabled = bool(enabled)
        self._writeBusy = bool(busy)
        interactive = self._writeEnabled or self._writeBusy

        self.outputToggleButton.setEnabled(self._writeEnabled and not self._writeBusy)
        self.clearFaultButton.setEnabled(self._writeEnabled and not self._writeBusy)
        self.applyOutputButton.setEnabled(self._writeEnabled and not self._writeBusy)

        # Keep spinboxes/sliders interactive while connected even during a transient write
        self.outputVoltage.setEnabled(interactive)
        self.outputCurrent.setEnabled(interactive)
        self.voltageSlider.setEnabled(interactive)
        self.currentSlider.setEnabled(interactive)
        # Preset cards stay usable at all times so users can edit/pre-select even offline
        for slot in self._presetSlots:
            slot.setEnabled(True)
        self._applyToggleButtonStyle()

    def setEditorsFromStatus(self, status: PowerStatus) -> None:
        if self._sliderDragging:
            return
        self._updatingFromStatus = True
        self._syncingControls = True
        try:
            v_val = status.set_voltage_limit_mv / 1000.0
            i_val = status.set_current_limit_ma / 1000.0
            if self.outputVoltage not in self._dirtyEditors and not self._hasFocus(self.outputVoltage):
                self.outputVoltage.setValue(v_val)
                self.voltageSlider.setValue(int(round(max(0.0, min(MAX_VOLTAGE_V, v_val)) / VOLTAGE_SLIDER_STEP)))
            if self.outputCurrent not in self._dirtyEditors and not self._hasFocus(self.outputCurrent):
                self.outputCurrent.setValue(i_val)
                self.currentSlider.setValue(int(round(max(0.0, min(MAX_CURRENT_A, i_val)) / CURRENT_SLIDER_STEP)))
        finally:
            self._syncingControls = False
            self._updatingFromStatus = False

    def pushChartValues(self, *, vin: float, vout: float, iout: float, pout: float) -> None:
        self.chart.pushSample(vin=vin, vout=vout, iout=iout, pout=pout, t=time.time())

    def pushChartStatus(self, status: PowerStatus) -> None:
        self.chart.pushSample(
            vin=status.vin_v,
            vout=status.vout_v,
            iout=status.iout_a,
            pout=status.pout_w,
            t=time.time(),
        )

    def pushMock(self) -> None:
        t = time.time()
        phase = t * 0.8
        vin = 20.0 + math.sin(phase) * 0.05
        vout = 12.0 + math.sin(phase * 1.2) * 0.08
        iout = 1.2 + math.sin(phase * 0.9) * 0.12
        self.chart.pushSample(vin=vin, vout=vout, iout=iout, pout=vout * iout, t=t)

    def updateReadouts(
        self,
        vin_v: float,
        iin_a: float,
        vout_v: float,
        iout_a: float,
        pin_w: float,
        pout_w: float,
        eff_pct: float,
        status: PowerStatus,
    ) -> None:
        self.voutCol.setReading(f"{vout_v:.3f}", f"设定 {status.set_voltage_limit_mv / 1000.0:.3f} V")
        self.ioutCol.setReading(f"{iout_a:.3f}", f"限流 {status.set_current_limit_ma / 1000.0:.3f} A")
        self.poutCol.setReading(f"{pout_w:.2f}", f"转换效率 {eff_pct:.1f} %")
        self.inputStripLabel.setText(
            f"输入   VIN {vin_v:.3f} V   ·   IIN {iin_a:.3f} A   ·   PIN {pin_w:.2f} W"
        )
        self.topologyChip.setText(status.topology_name)
        self.modeChip.setText(status.mode_name)
        self.tempChip.setText(f"{status.core_temp_c:.1f} °C")

        faults = pretty_faults(status.fault_state)
        self._faultActive = status.fault_state != 0
        if self._faultActive:
            self.faultChip.setText(f"故障: {faults}")
            self.clearFaultButton.show()
        else:
            self.faultChip.setText("状态正常")
            self.clearFaultButton.hide()
        self._applyChipsTheme()

    def _applyToggleButtonStyle(self) -> None:
        p = current_palette()
        if not self._writeEnabled and not self._writeBusy:
            bg, fg, border = p.surface_subtle, p.text_muted, p.border
            hover_bg = bg
        elif self._faultActive:
            bg, fg, border = p.danger_bg, p.danger_fg, p.danger_border
            hover_bg = "#FEE2E2" if not p.dark else "rgba(239, 68, 68, 0.28)"
        elif self._outputEnabled:
            bg, fg, border = p.vout, "#FFFFFF", p.vout
            hover_bg = "#047857" if not p.dark else "#10B981"
        else:
            bg, fg, border = p.surface_subtle, p.text_primary, p.border_strong
            hover_bg = p.accent_soft_bg
        self.outputToggleButton.setStyleSheet(
            f"""
            QPushButton#LabOutputToggleButton {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 10px;
                font-size: 14px;
                font-weight: 600;
            }}
            QPushButton#LabOutputToggleButton:hover {{
                background-color: {hover_bg};
            }}
            """
        )

    def _applyChipsTheme(self) -> None:
        p = current_palette()
        base_chip = (
            f"background-color: {p.surface_subtle}; color: {p.text_secondary};"
            f"border: 1px solid {p.border}; border-radius: 12px; padding: 0 10px; font-size: 11px; font-weight: 600;"
        )
        self.topologyChip.setStyleSheet(base_chip)
        self.modeChip.setStyleSheet(
            f"background-color: {p.accent_soft_bg}; color: {p.accent};"
            f"border: 1px solid {p.accent_soft_border}; border-radius: 12px; padding: 0 10px; font-size: 11px; font-weight: 600;"
        )
        self.tempChip.setStyleSheet(base_chip)
        if self._faultActive:
            self.faultChip.setStyleSheet(
                f"background-color: {p.danger_bg}; color: {p.danger_fg};"
                f"border: 1px solid {p.danger_border}; border-radius: 12px; padding: 0 10px; font-size: 11px; font-weight: 600;"
            )
        else:
            self.faultChip.setStyleSheet(
                f"background-color: {p.ok_bg}; color: {p.ok_fg};"
                f"border: 1px solid {p.ok_border}; border-radius: 12px; padding: 0 10px; font-size: 11px; font-weight: 600;"
            )
        self.clearFaultButton.setStyleSheet(
            f"QPushButton {{ background-color: {p.surface}; color: {p.danger_fg};"
            f"border: 1px solid {p.danger_border}; border-radius: 12px; padding: 0 10px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {p.danger_bg}; }}"
        )

    def refreshTheme(self) -> None:
        p = current_palette()
        self.headerCard.setStyleSheet(card_frame_qss("LabInstrumentHeader", p, radius=12))
        self.scopeCard.setStyleSheet(card_frame_qss("LabScopeCard", p, radius=12))
        self.controlCard.setStyleSheet(card_frame_qss("LabControlDock", p, radius=12))
        for div in (self._vDiv1, self._vDiv2, self._hDiv, self._ctrlDiv1, self._ctrlDiv2):
            div.setStyleSheet(f"background-color: {p.border}; border: none;")

        self.voutCol.refreshTheme()
        self.ioutCol.refreshTheme()
        self.poutCol.refreshTheme()
        self.inputStripLabel.setStyleSheet(f"color: {p.vin}; background: transparent;")
        self.scopeTitle.setStyleSheet(f"color: {p.text_primary}; font-size: 13px; font-weight: 600; background: transparent;")
        self.scopeTip.setStyleSheet(f"color: {p.text_muted}; font-size: 11px; background: transparent;")
        self.ctrlTitle.setStyleSheet(f"color: {p.text_primary}; background: transparent;")
        for lbl in (self.vsetLabel, self.isetLabel):
            lbl.setStyleSheet(f"color: {p.text_primary}; font-size: 12px; font-weight: 600; background: transparent;")
        for lbl in (self.vsetHint, self.isetHint):
            lbl.setStyleSheet(f"color: {p.text_muted}; font-size: 10px; background: transparent;")
        for lbl in self._scaleLabels:
            lbl.setStyleSheet(f"color: {p.text_muted}; background: transparent;")
        self.presetLabel.setStyleSheet(f"color: {p.text_secondary}; font-size: 11px; font-weight: 600; background: transparent;")

        mini_btn = (
            f"QPushButton {{ background-color: {p.surface_subtle}; color: {p.text_secondary};"
            f"border: 1px solid {p.border}; border-radius: 6px; padding: 0 7px; font-size: 10px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {p.accent_soft_bg}; color: {p.accent}; border-color: {p.accent_soft_border}; }}"
        )
        self.saveCurrentPresetButton.setStyleSheet(mini_btn)
        self.resetPresetsButton.setStyleSheet(mini_btn)

        self.ctrlFootNote.setStyleSheet(
            f"color: {p.text_muted}; font-size: 10px; background-color: {p.surface_subtle};"
            f"border: 1px solid {p.border}; border-radius: 8px; padding: 6px 8px;"
        )
        for slot in self._presetSlots:
            slot.refreshTheme()
        self._applyToggleButtonStyle()
        self._applyChipsTheme()
        self.chart.refreshTheme()
