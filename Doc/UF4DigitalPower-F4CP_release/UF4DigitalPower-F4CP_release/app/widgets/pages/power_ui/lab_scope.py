# -*- coding: utf-8 -*-
"""VOFA+-inspired 60 FPS realtime oscilloscope powered by pyqtgraph + pre-allocated numpy ring buffer."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .theme_tokens import current_palette, make_mono_font


@dataclass(frozen=True)
class _ChannelDef:
    key: str
    label: str
    unit: str
    axis: str  # "left" or "right"
    color_key: str
    fallback_color: str
    decimals: int
    default_on: bool
    col: int


CHANNELS: tuple[_ChannelDef, ...] = (
    _ChannelDef("vout", "输出电压", "V", "left", "vout", "#059669", 3, True, 2),
    _ChannelDef("iout", "输出电流", "A", "right", "iout", "#D97706", 3, True, 3),
    _ChannelDef("vin", "输入电压", "V", "left", "vin", "#64748B", 3, False, 1),
    _ChannelDef("pout", "输出功率", "W", "left", "pout", "#0891B2", 2, False, 4),
)

WINDOWS_S: tuple[tuple[str, float], ...] = (
    ("5s", 5.0),
    ("15s", 15.0),
    ("30s", 30.0),
    ("60s", 60.0),
)

# 20,000 pre-allocated samples -> >6.5 min @ 50 Hz or 40 s @ 500 Hz with zero heap churn
RING_CAPACITY = 20000
RENDER_INTERVAL_MS = 16  # 60 FPS batch rendering


class LabScopeWidget(QWidget):
    """High-throughput 60 FPS dual-axis oscilloscope with peak-hold downsampling & hover crosshair."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LabScopeWidget")

        self._enabled: dict[str, bool] = {ch.key: ch.default_on for ch in CHANNELS}
        self._windowSec = 15.0
        self._paused = False
        self._autoFollow = True
        self._dirty = False

        # Pre-allocated numpy ring buffer: cols = [t, vin, vout, iout, pout]
        self._buf = np.empty((RING_CAPACITY, 5), dtype=np.float64)
        self._head = 0
        self._count = 0

        # Cached rendered window arrays for O(log N) hover lookup
        self._lastRelT = np.empty(0, dtype=np.float64)
        self._lastRows = np.empty((0, 5), dtype=np.float64)
        self._leftRange: tuple[float, float] | None = None
        self._rightRange: tuple[float, float] | None = None

        self._channelButtons: dict[str, QPushButton] = {}
        self._windowButtons: dict[float, QPushButton] = {}

        # --- Compact 1-row toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)

        for ch in CHANNELS:
            btn = QPushButton(f"● {ch.label}", self)
            btn.setCheckable(True)
            btn.setChecked(ch.default_on)
            btn.setFixedHeight(27)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.toggled.connect(lambda checked, key=ch.key: self._toggleChannel(key, checked))
            self._channelButtons[ch.key] = btn
            toolbar.addWidget(btn)

        toolbar.addStretch(1)

        for text, secs in WINDOWS_S:
            btn = QPushButton(text, self)
            btn.setCheckable(True)
            btn.setChecked(abs(secs - self._windowSec) < 0.1)
            btn.setFixedHeight(25)
            btn.setFixedWidth(38)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, s=secs: self.setTimeWindow(s))
            self._windowButtons[secs] = btn
            toolbar.addWidget(btn)

        self.followButton = QPushButton("跟踪", self)
        self.followButton.setCheckable(True)
        self.followButton.setChecked(True)
        self.followButton.setFixedHeight(25)
        self.followButton.setMinimumWidth(46)
        self.followButton.setToolTip("自动跟踪最新波形（滚轮缩放/拖拽后会暂停跟踪，双击波形或点击此处恢复）")
        self.followButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.followButton.clicked.connect(self._onFollowClicked)

        self.pauseButton = QPushButton("暂停", self)
        self.pauseButton.setCheckable(True)
        self.pauseButton.setFixedHeight(25)
        self.pauseButton.setMinimumWidth(46)
        self.pauseButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pauseButton.toggled.connect(self._onPause)

        self.clearButton = QPushButton("清屏", self)
        self.clearButton.setFixedHeight(25)
        self.clearButton.setMinimumWidth(46)
        self.clearButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clearButton.clicked.connect(self.clearData)

        toolbar.addSpacing(4)
        toolbar.addWidget(self.followButton)
        toolbar.addWidget(self.pauseButton)
        toolbar.addWidget(self.clearButton)

        # --- pyqtgraph Dual-Axis PlotWidget ---
        self.plotWidget = pg.PlotWidget(self)
        self.plotWidget.setMinimumHeight(250)
        self.plotWidget.setAntialiasing(True)
        self.plotWidget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._plotItem = self.plotWidget.getPlotItem()
        self._plotItem.setMenuEnabled(False)
        self._plotItem.hideButtons()
        self._plotItem.showGrid(x=True, y=True, alpha=0.18)
        self._plotItem.setContentsMargins(4, 6, 4, 2)
        self._plotItem.setMouseEnabled(x=True, y=True)
        self._plotItem.enableAutoRange(x=False, y=False)
        self._plotItem.getAxis("bottom").setLabel(units=None)
        self._plotItem.getAxis("bottom").enableAutoSIPrefix(False)
        self._plotItem.getAxis("left").enableAutoSIPrefix(False)
        self._plotItem.getAxis("left").setWidth(52)

        # Right Y-Axis ViewBox (for IOUT)
        self._plotItem.showAxis("right")
        self._rightAxis = self._plotItem.getAxis("right")
        self._rightAxis.enableAutoSIPrefix(False)
        self._rightAxis.setWidth(50)

        self._rightVb = pg.ViewBox()
        self._rightVb.setMenuEnabled(False)
        self._rightVb.setMouseEnabled(x=False, y=True)
        self._rightVb.enableAutoRange(x=False, y=False)
        self._plotItem.scene().addItem(self._rightVb)
        self._rightAxis.linkToView(self._rightVb)
        self._rightVb.setXLink(self._plotItem)
        self._plotItem.vb.sigResized.connect(self._syncRightView)
        self._plotItem.vb.sigRangeChangedManually.connect(self._onManualRangeChanged)

        # Curves with VOFA+-style peak-hold downsampling + viewport clipping
        self._curves: dict[str, pg.PlotDataItem] = {}
        for ch in CHANNELS:
            curve = pg.PlotDataItem(antialias=True)
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)
            curve.setSkipFiniteCheck(True)
            if ch.axis == "right":
                self._rightVb.addItem(curve)
            else:
                self._plotItem.addItem(curve)
            curve.setVisible(ch.default_on)
            self._curves[ch.key] = curve

        # Hover vertical crosshair
        self._crosshair = pg.InfiniteLine(angle=90, movable=False)
        self._crosshair.setZValue(20)
        self._crosshair.hide()
        self._plotItem.addItem(self._crosshair, ignoreBounds=True)

        # Floating hover readout HUD (zero repaint overhead on curves!)
        self._hudLabel = QLabel(self.plotWidget)
        self._hudLabel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._hudLabel.setFont(make_mono_font(9, QFont.Medium))
        self._hudLabel.hide()

        # Centered waiting placeholder
        self._emptyLabel = QLabel("等待连接数据...", self.plotWidget)
        self._emptyLabel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._emptyLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._plotItem.scene().sigMouseMoved.connect(self._onMouseMoved)
        self.plotWidget.viewport().installEventFilter(self)

        # Backward-compatible attribute alias
        self.canvas = self.plotWidget

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addWidget(self.plotWidget, 1)

        # Fixed 60 FPS batch render timer
        self._timer = QTimer(self)
        self._timer.setInterval(RENDER_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.refreshTheme()
        self._applyInitialRanges()

    @property
    def windowSec(self) -> float:
        return self._windowSec

    def isChannelEnabled(self, key: str) -> bool:
        return self._enabled.get(key, False)

    def pushSample(
        self,
        *,
        vin: float,
        vout: float,
        iout: float,
        pout: float,
        t: float | None = None,
        **_ignored,
    ) -> None:
        """Ingest one sample into the pre-allocated numpy ring buffer (O(1), zero GC)."""
        if self._paused:
            return
        ts = time.time() if t is None else float(t)
        if self._count > 0:
            prev_idx = (self._head - 1) % RING_CAPACITY
            if ts < self._buf[prev_idx, 0]:
                ts = self._buf[prev_idx, 0] + 1e-4

        row = self._buf[self._head]
        row[0] = ts
        row[1] = float(vin)
        row[2] = float(vout)
        row[3] = float(iout)
        row[4] = float(pout)

        self._head = (self._head + 1) % RING_CAPACITY
        if self._count < RING_CAPACITY:
            self._count += 1
        self._dirty = True

    def _orderedBuffer(self) -> np.ndarray:
        if self._count == 0:
            return self._buf[:0]
        if self._count < RING_CAPACITY:
            return self._buf[: self._count]
        return np.concatenate((self._buf[self._head :], self._buf[: self._head]), axis=0)

    def visibleSamples(self) -> list[tuple[float, float, float, float, float]]:
        """Return currently buffered samples within windowSec (API compatibility)."""
        if self._count == 0:
            return []
        ordered = self._orderedBuffer()
        cutoff = ordered[-1, 0] - self._windowSec
        start = int(np.searchsorted(ordered[:, 0], cutoff, side="left"))
        return [tuple(float(v) for v in row) for row in ordered[start:]]

    def setTimeWindow(self, seconds: float) -> None:
        self._windowSec = max(1.0, float(seconds))
        for s, btn in self._windowButtons.items():
            btn.setChecked(abs(s - self._windowSec) < 0.1)
        self._autoFollow = True
        self.followButton.setChecked(True)
        self._leftRange = None
        self._rightRange = None
        self._dirty = True
        self._renderFrame()

    def resetView(self) -> None:
        self._autoFollow = True
        self.followButton.setChecked(True)
        self._leftRange = None
        self._rightRange = None
        self._dirty = True
        self._renderFrame()

    def clearData(self) -> None:
        self._head = 0
        self._count = 0
        self._dirty = False
        self._leftRange = None
        self._rightRange = None
        self._lastRelT = np.empty(0, dtype=np.float64)
        self._lastRows = np.empty((0, 5), dtype=np.float64)
        for curve in self._curves.values():
            curve.setData([], [])
        self._crosshair.hide()
        self._hudLabel.hide()
        self._emptyLabel.show()
        self._applyInitialRanges()

    def _onFollowClicked(self, checked: bool) -> None:
        self._autoFollow = bool(checked)
        if self._autoFollow:
            self._leftRange = None
            self._rightRange = None
            self._dirty = True
            self._renderFrame()

    def _onManualRangeChanged(self, *_args) -> None:
        if self._count > 0:
            self._autoFollow = False
            self.followButton.setChecked(False)

    def _onPause(self, paused: bool) -> None:
        self._paused = bool(paused)
        self.pauseButton.setText("继续" if self._paused else "暂停")
        if not self._paused and self._count > 0:
            self._dirty = True

    def _toggleChannel(self, key: str, checked: bool) -> None:
        self._enabled[key] = bool(checked)
        self._curves[key].setVisible(bool(checked))
        self._styleChannelButton(key)
        self._rightAxis.setVisible(self._enabled.get("iout", False))
        self._leftRange = None
        self._rightRange = None
        self._dirty = True
        self._renderFrame()

    def _syncRightView(self) -> None:
        self._rightVb.setGeometry(self._plotItem.vb.sceneBoundingRect())
        self._rightVb.linkedViewChanged(self._plotItem.vb, self._rightVb.XAxis)
        self._positionEmptyLabel()

    def _positionEmptyLabel(self) -> None:
        rect = self.plotWidget.rect()
        self._emptyLabel.setGeometry(rect)

    def _applyInitialRanges(self) -> None:
        self._plotItem.setXRange(-self._windowSec, 0.0, padding=0.0)
        self._plotItem.setYRange(0.0, 25.0, padding=0.0)
        self._rightVb.setYRange(0.0, 2.0, padding=0.0)

    def _tick(self) -> None:
        if self._dirty and self.isVisible():
            self._dirty = False
            self._renderFrame()

    def _renderFrame(self) -> None:
        if self._count == 0:
            self._emptyLabel.show()
            return
        self._emptyLabel.hide()

        ordered = self._orderedBuffer()
        t_end = ordered[-1, 0]
        rel_t = ordered[:, 0] - t_end

        if self._autoFollow:
            # Slice only visible window + small left margin for smooth edge continuity
            cutoff = -self._windowSec * 1.05
            start = int(np.searchsorted(rel_t, cutoff, side="left"))
            if start > 0:
                start -= 1
            x_view = rel_t[start:]
            rows_view = ordered[start:]
        else:
            x_view = rel_t
            rows_view = ordered

        self._lastRelT = x_view
        self._lastRows = rows_view

        # Drop antialiasing automatically only if huge unclipped zoom point count
        aa = len(x_view) <= 6000
        for ch in CHANNELS:
            curve = self._curves[ch.key]
            if not self._enabled.get(ch.key, False):
                continue
            curve.setData(x_view, rows_view[:, ch.col], antialias=aa)

        if self._autoFollow:
            self._plotItem.setXRange(-self._windowSec, 0.0, padding=0.0)

            left_cols = [ch.col for ch in CHANNELS if ch.axis == "left" and self._enabled.get(ch.key, False)]
            right_cols = [ch.col for ch in CHANNELS if ch.axis == "right" and self._enabled.get(ch.key, False)]

            if left_cols:
                lo, hi = self._computeSmoothRange(rows_view, left_cols, min_span=1.0, floor_zero=False, prev=self._leftRange)
                self._leftRange = (lo, hi)
                self._plotItem.setYRange(lo, hi, padding=0.0)

            if right_cols:
                lo, hi = self._computeSmoothRange(rows_view, right_cols, min_span=0.5, floor_zero=True, prev=self._rightRange)
                self._rightRange = (lo, hi)
                self._rightVb.setYRange(lo, hi, padding=0.0)

    @staticmethod
    def _computeSmoothRange(
        rows: np.ndarray,
        cols: list[int],
        *,
        min_span: float,
        floor_zero: bool,
        prev: tuple[float, float] | None,
    ) -> tuple[float, float]:
        sub = rows[:, cols]
        lo = float(np.min(sub))
        hi = float(np.max(sub))
        span = hi - lo
        if span < min_span:
            mid = 0.5 * (lo + hi)
            if floor_zero and mid < min_span * 0.45:
                target_lo, target_hi = 0.0, min_span
            else:
                target_lo, target_hi = mid - 0.5 * min_span, mid + 0.5 * min_span
        else:
            pad = span * 0.14
            target_lo = lo - pad
            if floor_zero and lo >= 0.0 and target_lo < 0.0:
                target_lo = 0.0
            target_hi = hi + pad

        if prev is None:
            return target_lo, target_hi

        prev_lo, prev_hi = prev
        prev_span = max(1e-6, prev_hi - prev_lo)
        # Immediate expand on new spike; smooth gentle settle when shrinking so axis never jitters
        out_lo = target_lo if target_lo < prev_lo else prev_lo + (target_lo - prev_lo) * 0.18
        out_hi = target_hi if target_hi > prev_hi else prev_hi + (target_hi - prev_hi) * 0.18
        if abs(out_lo - prev_lo) < prev_span * 0.008 and abs(out_hi - prev_hi) < prev_span * 0.008:
            return prev_lo, prev_hi
        return out_lo, out_hi

    def _onMouseMoved(self, scene_pos) -> None:
        if len(self._lastRelT) == 0:
            self._crosshair.hide()
            self._hudLabel.hide()
            return
        vb = self._plotItem.vb
        if not vb.sceneBoundingRect().contains(scene_pos):
            self._crosshair.hide()
            self._hudLabel.hide()
            return

        mouse_pt = vb.mapSceneToView(scene_pos)
        tx = float(mouse_pt.x())
        idx = int(np.searchsorted(self._lastRelT, tx))
        if idx >= len(self._lastRelT):
            idx = len(self._lastRelT) - 1
        elif idx > 0 and abs(self._lastRelT[idx - 1] - tx) < abs(self._lastRelT[idx] - tx):
            idx -= 1

        snap_t = float(self._lastRelT[idx])
        row = self._lastRows[idx]
        self._crosshair.setPos(snap_t)
        self._crosshair.show()

        lines = [f"t = {snap_t:+.2f} s"]
        for ch in CHANNELS:
            if self._enabled.get(ch.key, False):
                lines.append(f"{ch.label}: {row[ch.col]:.{ch.decimals}f} {ch.unit}")

        self._hudLabel.setText("\n".join(lines))
        self._hudLabel.adjustSize()

        view_pt = self.plotWidget.mapFromScene(scene_pos)
        box_w = self._hudLabel.width()
        box_h = self._hudLabel.height()
        pw = self.plotWidget.width()
        ph = self.plotWidget.height()
        bx = int(view_pt.x()) + 14
        if bx + box_w > pw - 58:
            bx = int(view_pt.x()) - box_w - 14
        bx = max(60, min(bx, max(60, pw - box_w - 8)))
        by = max(10, min(int(view_pt.y()) - box_h // 2, max(10, ph - box_h - 28)))
        self._hudLabel.move(bx, by)
        self._hudLabel.show()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.plotWidget.viewport():
            etype = event.type()
            if etype == QEvent.Type.Leave:
                self._crosshair.hide()
                self._hudLabel.hide()
            elif etype == QEvent.Type.MouseButtonDblClick:
                self.resetView()
                return True
            elif etype == QEvent.Type.Resize:
                self._positionEmptyLabel()
        return super().eventFilter(obj, event)

    def _channelColor(self, ch: _ChannelDef) -> str:
        p = current_palette()
        return str(getattr(p, ch.color_key, ch.fallback_color))

    def _styleChannelButton(self, key: str) -> None:
        p = current_palette()
        ch = next(c for c in CHANNELS if c.key == key)
        color = self._channelColor(ch)
        btn = self._channelButtons[key]
        if btn.isChecked():
            c = QColor(color)
            bg = f"rgba({c.red()}, {c.green()}, {c.blue()}, {0.18 if p.dark else 0.11})"
            bd = f"rgba({c.red()}, {c.green()}, {c.blue()}, {0.58 if p.dark else 0.45})"
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {bg}; color: {color}; border: 1px solid {bd};"
                f"border-radius: 13px; padding: 0 10px; font-size: 12px; font-weight: 600; }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {p.surface_subtle}; color: {p.text_muted}; border: 1px solid {p.border};"
                f"border-radius: 13px; padding: 0 10px; font-size: 12px; font-weight: 500; }}"
                f"QPushButton:hover {{ color: {p.text_secondary}; border-color: {p.border_strong}; }}"
            )

    def refreshTheme(self) -> None:
        p = current_palette()
        self.plotWidget.setBackground(QColor(p.surface))

        tick_font = make_mono_font(9, QFont.Normal)
        axis_pen = pg.mkPen(p.border_strong, width=1.0)
        text_pen = pg.mkPen(p.text_muted)

        for name in ("bottom", "left", "right"):
            axis = self._plotItem.getAxis(name)
            axis.setPen(axis_pen)
            axis.setTickFont(tick_font)
            if name == "right":
                axis.setTextPen(pg.mkPen(p.iout))
            elif name == "left":
                axis.setTextPen(pg.mkPen(p.vout if self._enabled.get("vout") and not self._enabled.get("vin") and not self._enabled.get("pout") else p.text_secondary))
            else:
                axis.setTextPen(text_pen)

        self._rightAxis.setVisible(self._enabled.get("iout", False))

        for ch in CHANNELS:
            color = self._channelColor(ch)
            pen = pg.mkPen(color=color, width=2.0)
            self._curves[ch.key].setPen(pen)
            self._styleChannelButton(ch.key)

        self._crosshair.setPen(pg.mkPen(p.accent, width=1.0, style=Qt.PenStyle.DashLine))
        self._hudLabel.setStyleSheet(
            f"background-color: {p.surface}; color: {p.text_primary};"
            f"border: 1px solid {p.border_strong}; border-radius: 6px; padding: 6px 8px;"
        )
        self._emptyLabel.setStyleSheet(f"color: {p.text_muted}; font-size: 12px; background: transparent;")

        win_qss = (
            f"QPushButton {{ background-color: {p.surface_subtle}; color: {p.text_secondary};"
            f"border: 1px solid {p.border}; border-radius: 6px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:checked {{ background-color: {p.accent_soft_bg}; color: {p.accent};"
            f"border: 1px solid {p.accent_soft_border}; }}"
        )
        for btn in self._windowButtons.values():
            btn.setStyleSheet(win_qss)

        follow_qss = (
            f"QPushButton {{ background-color: {p.surface}; color: {p.text_secondary};"
            f"border: 1px solid {p.border}; border-radius: 6px; padding: 0 9px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {p.surface_subtle}; color: {p.text_primary}; }}"
            f"QPushButton:checked {{ background-color: {p.accent_soft_bg}; color: {p.accent}; border-color: {p.accent_soft_border}; }}"
        )
        act_qss = (
            f"QPushButton {{ background-color: {p.surface}; color: {p.text_secondary};"
            f"border: 1px solid {p.border}; border-radius: 6px; padding: 0 9px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {p.surface_subtle}; color: {p.text_primary}; }}"
            f"QPushButton:checked {{ background-color: {p.warn_bg}; color: {p.warn_fg}; border-color: {p.warn_fg}; }}"
        )
        self.followButton.setStyleSheet(follow_qss)
        self.pauseButton.setStyleSheet(act_qss)
        self.clearButton.setStyleSheet(act_qss)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
