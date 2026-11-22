# -*- coding: utf-8 -*-
"""Dedicated 6-point piecewise-linear output voltage calibration workbench."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    DoubleSpinBox,
    FluentIcon as FIF,
    PrimaryPushButton,
    PushButton,
    ToolButton,
)

from .theme_tokens import card_frame_qss, current_palette, make_mono_font

DEFAULT_VOUT_CAL_TARGETS_V = (5.0, 12.0, 20.0, 32.0, 48.0, 60.0)


class CalibrationTab(QWidget):
    """Spacious minimalist 6-point multimeter calibration view."""

    setTargetRequested = pyqtSignal(int, float)
    recordPointRequested = pyqtSignal(int, float)
    clearPointRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PowerCalibrationTab")
        self._points: list[tuple[int, int] | None] = [None] * len(DEFAULT_VOUT_CAL_TARGETS_V)
        self._rowReadyToRecord: list[bool] = [True] * len(DEFAULT_VOUT_CAL_TARGETS_V)
        self._writeEnabled = False
        self._liveRaw = 0
        self._liveVoutV = 0.0
        self._updatingInputs = False

        # --- Top Live Banner Card ---
        self.bannerCard = QFrame(self)
        self.bannerCard.setObjectName("LabCalBanner")

        self.liveVoutCaption = QLabel("实时输出电压", self.bannerCard)
        self.liveVoutValue = QLabel("--.--- V", self.bannerCard)
        self.liveVoutValue.setFont(make_mono_font(22, QFont.DemiBold))

        self.liveRawCaption = QLabel("最近 ADC RAW (16×)", self.bannerCard)
        self.liveRawValue = QLabel("--", self.bannerCard)
        self.liveRawValue.setFont(make_mono_font(20, QFont.DemiBold))

        self.activeCountCaption = QLabel("已激活锚点", self.bannerCard)
        self.activeCountValue = QLabel("0 / 6", self.bannerCard)
        self.activeCountValue.setFont(make_mono_font(20, QFont.DemiBold))

        self.readButton = PushButton(FIF.SYNC, "读取设备表", self.bannerCard)
        self.clearAllButton = PushButton(FIF.DELETE, "清除全部", self.bannerCard)
        self.saveFlashButton = PrimaryPushButton(FIF.SAVE, "保存到 Flash", self.bannerCard)
        for btn in (self.readButton, self.clearAllButton, self.saveFlashButton):
            btn.setFixedHeight(36)

        bannerLayout = QHBoxLayout(self.bannerCard)
        bannerLayout.setContentsMargins(20, 14, 20, 14)
        bannerLayout.setSpacing(20)
        self._bannerDividers: list[QFrame] = []

        stat_pairs = (
            (self.liveVoutCaption, self.liveVoutValue),
            (self.liveRawCaption, self.liveRawValue),
            (self.activeCountCaption, self.activeCountValue),
        )
        for idx, (cap, val) in enumerate(stat_pairs):
            if idx > 0:
                vdiv = QFrame(self.bannerCard)
                vdiv.setFixedWidth(1)
                self._bannerDividers.append(vdiv)
                bannerLayout.addWidget(vdiv)
            box = QVBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(2)
            box.addWidget(cap)
            box.addWidget(val)
            bannerLayout.addLayout(box)

        bannerLayout.addStretch(1)
        bannerLayout.addWidget(self.readButton)
        bannerLayout.addWidget(self.clearAllButton)
        bannerLayout.addWidget(self.saveFlashButton)

        # --- Clean 6-Point Table Card ---
        self.tableCard = QFrame(self)
        self.tableCard.setObjectName("LabCalTableCard")
        self.tableTitle = QLabel("6 点分段线性输出电压校准表", self.tableCard)
        self.tableSubtitle = QLabel("任意顺序单点或多点校准 · 记录原始 ADC 码互不干扰 · 完成后点击「保存到 Flash」", self.tableCard)

        self._headerLabels: list[QLabel] = []
        self._rowSeparators: list[QFrame] = []
        self._rows: list[dict[str, object]] = []

        grid = QGridLayout()
        grid.setContentsMargins(0, 6, 0, 4)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)

        headers = ("点位", "测试目标电压", "输出控制", "已记录锚点 (RAW → 实测)", "仪器实测电压", "回传生效", "")
        for col, text in enumerate(headers):
            lbl = QLabel(text, self.tableCard)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter if col in (0, 2, 3, 5, 6) else Qt.AlignmentFlag.AlignLeft)
            self._headerLabels.append(lbl)
            grid.addWidget(lbl, 0, col)

        for idx, default_v in enumerate(DEFAULT_VOUT_CAL_TARGETS_V):
            sep = QFrame(self.tableCard)
            sep.setFixedHeight(1)
            self._rowSeparators.append(sep)
            grid.addWidget(sep, idx * 2 + 1, 0, 1, 7)

            pointLabel = QLabel(f"P{idx + 1}", self.tableCard)
            pointLabel.setFont(make_mono_font(11, QFont.DemiBold))
            pointLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pointLabel.setFixedWidth(40)

            targetSpin = DoubleSpinBox(self.tableCard)
            targetSpin.setRange(1.0, 65.0)
            targetSpin.setDecimals(3)
            targetSpin.setSingleStep(1.0)
            targetSpin.setSuffix(" V")
            targetSpin.setValue(default_v)
            targetSpin.setFixedHeight(36)
            targetSpin.setFont(make_mono_font(10, QFont.Medium))
            targetSpin.setAlignment(Qt.AlignmentFlag.AlignRight)

            setBtn = PushButton("设为此电压", self.tableCard)
            setBtn.setFixedHeight(34)
            setBtn.setMinimumWidth(96)
            setBtn.clicked.connect(lambda _checked=False, row=idx: self._emitSetTarget(row))

            anchorLabel = QLabel("未校准", self.tableCard)
            anchorLabel.setFont(make_mono_font(10, QFont.Medium))
            anchorLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            anchorLabel.setFixedHeight(30)
            anchorLabel.setMinimumWidth(182)

            measuredSpin = DoubleSpinBox(self.tableCard)
            measuredSpin.setRange(0.0, 65.0)
            measuredSpin.setDecimals(3)
            measuredSpin.setSingleStep(0.001)
            measuredSpin.setSuffix(" V")
            measuredSpin.setValue(default_v)
            measuredSpin.setFixedHeight(36)
            measuredSpin.setFont(make_mono_font(10, QFont.DemiBold))
            measuredSpin.setAlignment(Qt.AlignmentFlag.AlignRight)
            measuredSpin.setAccelerated(True)
            measuredSpin.valueChanged.connect(lambda _v, row=idx: self._onMeasuredEdited(row))
            try:
                measuredSpin.lineEdit().textEdited.connect(lambda _t, row=idx: self._onMeasuredEdited(row))
            except Exception:
                pass

            recordBtn = PushButton("回传校准", self.tableCard)
            recordBtn.setFixedHeight(34)
            recordBtn.setMinimumWidth(92)
            recordBtn.clicked.connect(lambda _checked=False, row=idx: self._emitRecord(row))

            clearBtn = ToolButton(FIF.DELETE, self.tableCard)
            clearBtn.setFixedSize(34, 34)
            clearBtn.setToolTip(f"清除 P{idx + 1} 校准点")
            clearBtn.clicked.connect(lambda _checked=False, row=idx: self.clearPointRequested.emit(row))

            grid_row = idx * 2 + 2
            grid.addWidget(pointLabel, grid_row, 0)
            grid.addWidget(targetSpin, grid_row, 1)
            grid.addWidget(setBtn, grid_row, 2)
            grid.addWidget(anchorLabel, grid_row, 3)
            grid.addWidget(measuredSpin, grid_row, 4)
            grid.addWidget(recordBtn, grid_row, 5)
            grid.addWidget(clearBtn, grid_row, 6)

            self._rows.append(
                {
                    "pointLabel": pointLabel,
                    "targetSpin": targetSpin,
                    "setBtn": setBtn,
                    "rawLabel": anchorLabel,
                    "measuredSpin": measuredSpin,
                    "recordBtn": recordBtn,
                    "clearBtn": clearBtn,
                }
            )

        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(3, 3)
        grid.setColumnStretch(4, 2)

        self.guideBar = QLabel(
            "校准流程：① 空载接高精度万用表，点击行内「设为此电压」输出 → ② 填入万用表读数并点击「回传校准」"
            "（按钮变为「已校准」防误点；如需微调直接改数字按钮会亮起「微调校准」）→ ③ 全部测完点击右上角「保存到 Flash」。",
            self.tableCard,
        )
        self.guideBar.setWordWrap(True)

        tableLayout = QVBoxLayout(self.tableCard)
        tableLayout.setContentsMargins(22, 18, 22, 18)
        tableLayout.setSpacing(10)
        tableLayout.addWidget(self.tableTitle)
        tableLayout.addWidget(self.tableSubtitle)
        tableLayout.addLayout(grid)
        tableLayout.addSpacing(6)
        tableLayout.addWidget(self.guideBar)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.addWidget(self.bannerCard)
        root.addWidget(self.tableCard)
        root.addStretch(1)

        self.refreshTheme()

    def _refreshRowState(self, row: int) -> None:
        recordBtn: PushButton = self._rows[row]["recordBtn"]  # type: ignore[assignment]
        clearBtn: ToolButton = self._rows[row]["clearBtn"]  # type: ignore[assignment]
        calibrated = self._points[row] is not None
        ready = self._rowReadyToRecord[row]
        if calibrated and not ready:
            recordBtn.setText("✓ 已校准")
            recordBtn.setEnabled(False)
        elif calibrated and ready:
            recordBtn.setText("微调校准")
            recordBtn.setEnabled(self._writeEnabled)
        else:
            recordBtn.setText("回传校准")
            recordBtn.setEnabled(self._writeEnabled)
        clearBtn.setEnabled(self._writeEnabled and calibrated)

    def _onMeasuredEdited(self, row: int) -> None:
        if not self._updatingInputs:
            self._rowReadyToRecord[row] = True
            self._refreshRowState(row)

    def isRowReadyToRecord(self, row: int) -> bool:
        return 0 <= row < len(self._rowReadyToRecord) and self._rowReadyToRecord[row]

    def _emitSetTarget(self, row: int) -> None:
        spin: DoubleSpinBox = self._rows[row]["targetSpin"]  # type: ignore[assignment]
        target_v = spin.value()
        if self._points[row] is None:
            measured_spin: DoubleSpinBox = self._rows[row]["measuredSpin"]  # type: ignore[assignment]
            self._updatingInputs = True
            try:
                measured_spin.setValue(target_v)
            finally:
                self._updatingInputs = False
            self._rowReadyToRecord[row] = True
            self._refreshRowState(row)
        self.setTargetRequested.emit(row, target_v)

    def _emitRecord(self, row: int) -> None:
        measured_spin: DoubleSpinBox = self._rows[row]["measuredSpin"]  # type: ignore[assignment]
        measured_spin.interpretText()
        self.recordPointRequested.emit(row, measured_spin.value())

    def getPoints(self) -> list[tuple[int, int] | None]:
        return list(self._points)

    def setPoints(self, points: list[tuple[int, int]], live_raw: int = 0) -> None:
        cleaned = [(int(r), int(m)) for r, m in points if int(r) > 0 and int(m) > 0]
        cleaned.sort(key=lambda pair: pair[0])
        prev_points = list(self._points)
        self._points = [None] * len(self._rows)
        used_rows: set[int] = set()
        for pair in cleaned[:len(self._rows)]:
            mv_v = pair[1] / 1000.0
            best_row = min(
                (i for i in range(len(self._rows)) if i not in used_rows),
                key=lambda i: abs(self._rows[i]["targetSpin"].value() - mv_v),  # type: ignore[union-attr]
            )
            used_rows.add(best_row)
            self._points[best_row] = pair

        self._updatingInputs = True
        try:
            for idx, row in enumerate(self._rows):
                anchorLabel: QLabel = row["rawLabel"]  # type: ignore[assignment]
                measuredSpin: DoubleSpinBox = row["measuredSpin"]  # type: ignore[assignment]
                targetSpin: DoubleSpinBox = row["targetSpin"]  # type: ignore[assignment]
                pair = self._points[idx]
                if pair is not None:
                    raw_val, mv_val = pair
                    anchorLabel.setText(f"✓  {raw_val} → {mv_val / 1000.0:.3f} V")
                    measuredSpin.setValue(mv_val / 1000.0)
                    self._rowReadyToRecord[idx] = False
                else:
                    anchorLabel.setText("未校准")
                    if prev_points[idx] is not None:
                        measuredSpin.setValue(targetSpin.value())
                    self._rowReadyToRecord[idx] = True
                self._refreshRowState(idx)
        finally:
            self._updatingInputs = False

        if live_raw > 0:
            self._liveRaw = int(live_raw)
        self._updateBanner()
        self.refreshTheme()

    def updateLiveTelemetry(self, vout_v: float, live_raw: int | None = None) -> None:
        self._liveVoutV = vout_v
        if live_raw is not None and live_raw > 0:
            self._liveRaw = int(live_raw)
        self._updateBanner()

    def _updateBanner(self) -> None:
        active = sum(1 for item in self._points if item is not None)
        self.liveVoutValue.setText(f"{self._liveVoutV:.3f} V")
        self.liveRawValue.setText(f"{self._liveRaw}" if self._liveRaw > 0 else "--")
        self.activeCountValue.setText(f"{active} / {len(self._rows)}")

    def setWriteEnabled(self, enabled: bool) -> None:
        self._writeEnabled = bool(enabled)
        self.readButton.setEnabled(enabled)
        self.clearAllButton.setEnabled(enabled)
        self.saveFlashButton.setEnabled(enabled)
        for idx, row in enumerate(self._rows):
            row["targetSpin"].setEnabled(enabled)  # type: ignore[union-attr]
            row["setBtn"].setEnabled(enabled)  # type: ignore[union-attr]
            row["measuredSpin"].setEnabled(enabled)  # type: ignore[union-attr]
            self._refreshRowState(idx)

    def refreshTheme(self) -> None:
        p = current_palette()
        self.bannerCard.setStyleSheet(card_frame_qss("LabCalBanner", p, radius=12))
        self.tableCard.setStyleSheet(card_frame_qss("LabCalTableCard", p, radius=12))
        for cap in (self.liveVoutCaption, self.liveRawCaption, self.activeCountCaption):
            cap.setStyleSheet(f"color: {p.text_secondary}; font-size: 11px; background: transparent;")
        self.liveVoutValue.setStyleSheet(f"color: {p.vout}; background: transparent;")
        self.liveRawValue.setStyleSheet(f"color: {p.text_primary}; background: transparent;")
        self.activeCountValue.setStyleSheet(f"color: {p.accent}; background: transparent;")
        self.tableTitle.setStyleSheet(f"color: {p.text_primary}; font-size: 14px; font-weight: 600; background: transparent;")
        self.tableSubtitle.setStyleSheet(f"color: {p.text_muted}; font-size: 12px; background: transparent;")
        for lbl in self._headerLabels:
            lbl.setStyleSheet(f"color: {p.text_secondary}; font-size: 12px; font-weight: 600; background: transparent;")
        for sep in (*self._rowSeparators, *self._bannerDividers):
            sep.setStyleSheet(f"background-color: {p.border}; border: none;")
        for idx, row in enumerate(self._rows):
            row["pointLabel"].setStyleSheet(f"color: {p.text_primary}; background: transparent;")  # type: ignore[union-attr]
            anchor: QLabel = row["rawLabel"]  # type: ignore[assignment]
            if self._points[idx] is not None:
                anchor.setStyleSheet(
                    f"background-color: {p.ok_bg}; color: {p.ok_fg}; border: 1px solid {p.ok_border};"
                    f"border-radius: 8px; padding: 0 10px; font-weight: 600;"
                )
            else:
                anchor.setStyleSheet(
                    f"background-color: {p.surface_subtle}; color: {p.text_muted}; border: 1px solid {p.border};"
                    f"border-radius: 8px; padding: 0 10px;"
                )
        self.guideBar.setStyleSheet(
            f"background-color: {p.accent_soft_bg}; color: {p.text_secondary}; border: 1px solid {p.accent_soft_border};"
            f"border-radius: 8px; padding: 10px 14px; font-size: 12px;"
        )
