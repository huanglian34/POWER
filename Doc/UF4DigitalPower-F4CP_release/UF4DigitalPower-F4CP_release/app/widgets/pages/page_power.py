# -*- coding: utf-8 -*-
"""Refactored minimalist lab workbench PowerPage (zero-scroll tabbed layout)."""

from __future__ import annotations

import time

from PyQt5.QtCore import QMetaObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from app.controllers import PowerPageController
from app.core.utility import showMessage
from app.manager import StyleSheet
from app.session import (
    DEFAULT_STATUS_VALUES,
    DebugSnapshot,
    F4CPPowerClient,
    PowerStatus,
    SerialConfig,
    SerialSession,
    build_status,
    listSerialPortEntries,
    listSerialPorts,
    pretty_faults,
)
from app.widgets.pages.power_ui import (
    CalibrationTab,
    DashboardTab,
    LogsTab,
    PowerTopBar,
    ProtectionTab,
    current_palette,
)
from config import get_logger

logger = get_logger("PowerPage")

DEFAULT_OVP_SET_VALUE_MV = 44000
POWER_POLL_INTERVAL_MS = 500
POWER_SERIAL_BAUD_RATE = 921600
WRITE_POLL_RESTART_DELAY_MS = 600
WRITE_WATCHDOG_TIMEOUT_MS = 6500
MOCK_SAMPLE_INTERVAL_MS = 20


class PowerPage(QWidget):
    """Minimalist lab-grade digital power workbench with 4 focused zero-scroll views."""

    attachSessionRequested = pyqtSignal(object)
    detachSessionRequested = pyqtSignal()
    readStatusRequested = pyqtSignal()
    debugSnapshotRequested = pyqtSignal()
    outputLimitsRequested = pyqtSignal(int, int, bool)
    protectionValuesRequested = pyqtSignal(int, int, int, int)
    powerStateRequested = pyqtSignal(bool)
    readVoutCalRequested = pyqtSignal()
    recordVoutCalPointRequested = pyqtSignal(int, int, object)
    saveVoutCalRequested = pyqtSignal(object, bool)
    startPollingRequested = pyqtSignal(int)
    stopPollingRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("PowerPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._shutdownDone = False
        self._manualSession = None
        self._lastStatus: PowerStatus | None = None
        self._stagedOutputEnabled: bool | None = None
        self._pendingOutputSettings: tuple[int, int] | None = None
        self._pendingProtectionSettings: tuple[int, int, int, int] | None = None
        self._queuedQuickOutputSettings: tuple[int, int, str] | None = None
        self._lastOutputWriteSilent = False
        self._lastOutputWriteReason = ""
        self._writeInFlight = False
        self._writePollingRestartPending = False
        self._lastVerboseLogTs = 0.0
        self._lastCardUpdateTs = 0.0
        self._emaVin: float | None = None
        self._emaVout: float | None = None
        self._emaIin: float | None = None
        self._emaIout: float | None = None
        self._appendLogBusy = False
        self._connectedPort = ""

        self._client = F4CPPowerClient()
        self._clientThread = QThread(self)
        self._client.moveToThread(self._clientThread)
        self._clientThread.finished.connect(self._client.deleteLater)
        self._clientThread.start()

        self._writePollRestartTimer = QTimer(self)
        self._writePollRestartTimer.setSingleShot(True)
        self._writePollRestartTimer.timeout.connect(self._restartAutoPollingAfterWrite)

        self._writeWatchdogTimer = QTimer(self)
        self._writeWatchdogTimer.setSingleShot(True)
        self._writeWatchdogTimer.timeout.connect(self._onWriteWatchdogTimeout)

        self._mockTimer = QTimer(self)
        self._mockTimer.setInterval(MOCK_SAMPLE_INTERVAL_MS)
        self._mockTimer.timeout.connect(self.pushMockPowerSample)

        # --- Modular Zero-Scroll UI Composition ---
        self.topBar = PowerTopBar(self)
        self.stack = QStackedWidget(self)
        self.dashboardTab = DashboardTab(self.stack)
        self.calibrationTab = CalibrationTab(self.stack)
        self.protectionTab = ProtectionTab(self.stack)
        self.logsTab = LogsTab(self.stack)

        self._tabMap: dict[str, QWidget] = {
            "dashboard": self.dashboardTab,
            "calibration": self.calibrationTab,
            "protection": self.protectionTab,
            "logs": self.logsTab,
        }
        for widget in self._tabMap.values():
            self.stack.addWidget(widget)
        self.stack.setCurrentWidget(self.dashboardTab)
        self.topBar.tabChanged.connect(self._onTabChanged)

        # Backward-compatible widget aliases used by controller / tests
        self.voutCalCard = self.calibrationTab
        self.portCombo = self.topBar.portCombo
        self.refreshTargetButton = self.topBar.refreshPortsButton
        self.connectButton = self.topBar.connectButton
        self.autoPollSwitch = self.topBar.autoPollSwitch
        self.refreshButton = self.protectionTab.refreshButton
        self.debugButton = self.protectionTab.debugButton
        self.logLevelCombo = self.logsTab.logLevelCombo
        self.clearLogButton = self.logsTab.clearLogButton
        self.logEdit = self.logsTab.logEdit

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        root.addWidget(self.topBar)
        root.addWidget(self.stack, 1)

        self._initEmptyStatusView()
        self._controller = PowerPageController(self)
        self._bindSignals()
        self._applyDisconnectedState()
        StyleSheet.POWER_PAGE.apply(self)
        self._refreshThemeWidgets()
        self.refreshSerialPorts()

    def _onTabChanged(self, route_key: str) -> None:
        target = self._tabMap.get(route_key)
        if target is not None:
            self.stack.setCurrentWidget(target)

    def _bindSignals(self) -> None:
        self._controller.bind()

    def _onThemeChanged(self, *_args) -> None:
        StyleSheet.POWER_PAGE.apply(self)
        self._refreshThemeWidgets()
        QTimer.singleShot(0, self._refreshThemeWidgets)

    def _refreshThemeWidgets(self) -> None:
        p = current_palette()
        self.setStyleSheet(f"QWidget#PowerPage {{ background-color: {p.canvas}; }}")
        self.topBar.refreshTheme()
        self.dashboardTab.refreshTheme()
        self.calibrationTab.refreshTheme()
        self.protectionTab.refreshTheme()
        self.logsTab.refreshTheme()

    def refreshSerialPorts(self) -> None:
        entries = listSerialPortEntries()
        self._portEntries = {entry.port: entry for entry in entries}
        prev_port = str(self.portCombo.currentData() or "").strip()
        if not prev_port:
            prev_port = self.portCombo.currentText().split(" ", 1)[0].strip()

        self.portCombo.blockSignals(True)
        self.portCombo.clear()
        vofa_ports: list[str] = []
        control_ports: list[str] = []
        selected_idx = -1
        for idx, entry in enumerate(entries):
            self.portCombo.addItem(entry.compact_label, userData=entry.port)
            if entry.role == "uf4com":
                control_ports.append(entry.port)
            elif entry.role == "vofa":
                vofa_ports.append(entry.port)
            if entry.port == prev_port:
                selected_idx = idx
        if selected_idx >= 0:
            self.portCombo.setCurrentIndex(selected_idx)
        elif entries:
            self.portCombo.setCurrentIndex(0)
        self.portCombo.blockSignals(False)

        tooltip_lines = [entry.full_label for entry in entries]
        if vofa_ports:
            tooltip_lines.append(f"VOFA+ FireWater 波形口: {', '.join(vofa_ports)}")
        self.portCombo.setToolTip("\n".join(tooltip_lines) if tooltip_lines else "未检测到可用串口")
        if vofa_ports:
            self.refreshTargetButton.setToolTip(
                f"刷新串口列表（控制口: {', '.join(control_ports) or '未发现'} / VOFA+ 波形口: {', '.join(vofa_ports)}）"
            )
        else:
            self.refreshTargetButton.setToolTip("刷新串口列表")
        logger.info(f"Serial ports: {[entry.full_label for entry in entries]}")

    def _initEmptyStatusView(self) -> None:
        self._renderStatusView(
            build_status(dict(DEFAULT_STATUS_VALUES)),
            update_chart=False,
            log_changes=False,
        )

    def toggleConnection(self) -> None:
        if self._client.is_connected:
            self._disconnectManualSession()
        else:
            self._connectSerialTarget()

    def _connectSerialTarget(self) -> None:
        port = str(self.portCombo.currentData() or "").strip()
        if not port:
            port = self.portCombo.currentText().split(" ", 1)[0].strip()
        if not port:
            message = "未选择串口。请先刷新并选择串口。"
            self._appendLog(f"错误: {message}")
            logger.warning(message)
            return
        entry = getattr(self, "_portEntries", {}).get(port)
        if entry is not None and entry.role == "vofa":
            ctrl = next((e.port for e in getattr(self, "_portEntries", {}).values() if e.role == "uf4com"), "")
            hint = f"请选择控制口 {ctrl} 连接 F4CP，{port} 为 VOFA+ 波形专用口。" if ctrl else f"{port} 为 VOFA+ 波形专用口。"
            self._appendLog(f"提示: {hint}")
            showMessage(self, "端口用途提示", hint, level="warning")
            return
        session = SerialSession(SerialConfig(port=port, baudrate=POWER_SERIAL_BAUD_RATE))
        try:
            session.open()
        except Exception as exc:
            self._setAllWriteEnabled(False)
            self._appendLog(f"错误: {exc}")
            logger.error(f"Failed to open serial port {port}: {exc}")
            return
        self._manualSession = session
        self.onDeviceConnected(session)

    def onDeviceConnected(self, session) -> None:
        port = getattr(getattr(session, "cfg", None), "port", "未知")
        self._connectedPort = port
        self._emaVin = None
        self._emaVout = None
        self._emaIin = None
        self._emaIout = None
        self.attachSessionRequested.emit(session)
        self.topBar.setOnline(True, port)
        self._setAllWriteEnabled(True)
        self._appendLog(f"已连接: {port}")
        logger.info(f"Device session attached on {port}")
        showMessage(self, "设备已连接", f"已连接至 {port}。", level="success")
        if self.autoPollSwitch.isChecked():
            self.startPollingRequested.emit(POWER_POLL_INTERVAL_MS)
        else:
            self.readStatusRequested.emit()
        QTimer.singleShot(1200, self._readVoutCalibrationQuiet)

    def _disconnectManualSession(self) -> None:
        self.stopPollingRequested.emit()
        self.detachSessionRequested.emit()
        try:
            if self._manualSession:
                self._manualSession.close()
        except Exception as exc:
            logger.error(f"PowerPage manual session close failed: {exc}")
        self._manualSession = None
        self._applyDisconnectedState()
        self._appendLog("设备已断开")

    def _closeManualSessionSilently(self) -> None:
        session = self._manualSession
        self._manualSession = None
        if session is None:
            return
        try:
            session.set_event_receiver(None)
            session.close()
        except Exception as exc:
            logger.error(f"PowerPage serial cleanup failed: {exc}")

    def _onConnectionChanged(self, connected: bool) -> None:
        self.topBar.setOnline(connected, self._connectedPort if connected else "")
        self._setAllWriteEnabled(connected)
        if not connected:
            self._closeManualSessionSilently()
            self._applyDisconnectedState()

    def _setAllWriteEnabled(self, enabled: bool, *, busy: bool = False) -> None:
        self.dashboardTab.setWriteEnabled(enabled, busy=busy)
        self.calibrationTab.setWriteEnabled(enabled)
        self.protectionTab.setWriteEnabled(enabled)

    def _updateStatusView(self, status: PowerStatus) -> None:
        self._renderStatusView(status, update_chart=True, log_changes=True)

    def _renderStatusView(
        self,
        status: PowerStatus,
        *,
        update_chart: bool,
        log_changes: bool,
    ) -> None:
        previous = self._lastStatus
        if log_changes:
            self._lastStatus = status

        def _smooth(prev_val: float | None, raw_val: float, snap_thresh: float) -> float:
            if prev_val is None or abs(raw_val - prev_val) >= snap_thresh:
                return raw_val
            return prev_val * 0.65 + raw_val * 0.35

        self._emaVin = _smooth(self._emaVin, status.vin_v, 0.08)
        self._emaVout = _smooth(self._emaVout, status.vout_v, 0.05)
        self._emaIin = _smooth(self._emaIin, status.iin_a, 0.03)
        self._emaIout = _smooth(self._emaIout, status.iout_a, 0.03)

        if update_chart:
            # Feed full-rate raw samples into the 60 FPS oscilloscope so transients stay crisp like VOFA+
            self.dashboardTab.pushChartValues(
                vin=status.vin_v,
                vout=status.vout_v,
                iout=status.iout_a,
                pout=status.pout_w,
            )

        now_ts = time.monotonic()
        fault_changed = previous is not None and previous.fault_state != status.fault_state
        state_changed = previous is not None and previous.power_state != status.power_state
        if update_chart and not fault_changed and not state_changed and (now_ts - self._lastCardUpdateTs) < 0.066:
            return
        self._lastCardUpdateTs = now_ts

        self._syncPendingParameterWrites(status)
        self.dashboardTab.setEditorsFromStatus(status)
        if self.protectionTab.isVisible() or fault_changed or state_changed or not update_chart:
            self.protectionTab.setFromStatus(status)

        vin_disp = self._emaVin
        vout_disp = self._emaVout
        iin_disp = self._emaIin
        iout_disp = self._emaIout
        pin_disp = vin_disp * iin_disp
        pout_disp = vout_disp * iout_disp
        eff_disp = 0.0 if pin_disp <= 0.05 else max(0.0, min(100.0, pout_disp / pin_disp * 100.0))

        self.dashboardTab.updateReadouts(
            vin_disp, iin_disp, vout_disp, iout_disp, pin_disp, pout_disp, eff_disp, status
        )
        self.calibrationTab.updateLiveTelemetry(vout_disp)

        switch_value = status.power_enabled
        faulted = status.fault_state != 0
        if faulted:
            self._stagedOutputEnabled = None
        elif self._stagedOutputEnabled is not None:
            if self._stagedOutputEnabled == status.power_enabled:
                self._stagedOutputEnabled = None
            else:
                switch_value = self._stagedOutputEnabled
        self.dashboardTab.setOutputEnabledVisual(switch_value)

        faults = pretty_faults(status.fault_state)
        if not faults or faults == "None":
            faults = "无"
        if log_changes and (
            previous is None
            or previous.power_state != status.power_state
            or previous.state_machine_flag_bits != status.state_machine_flag_bits
            or previous.fault_state != status.fault_state
        ):
            self._appendLog(
                f"状态 输出={'开启' if status.power_enabled else '关闭'} "
                f"状态机={status.state_flag_name} 拓扑={status.topology_name} "
                f"故障={faults} VOUT={status.vout_v:.3f}V IOUT={status.iout_a:.3f}A"
            )
            if faulted and (previous is None or previous.fault_state != status.fault_state):
                self._appendLog(f"保护触发：已停止输出，故障={faults} (0x{status.fault_state:04X})")
        if log_changes and self.logLevelCombo.currentData() == "verbose" and time.monotonic() - self._lastVerboseLogTs >= 2.0:
            self._lastVerboseLogTs = time.monotonic()
            self._appendLog(self._client.pretty_print_status(status))

    def _readStatusOnce(self) -> None:
        if not self._client.is_connected:
            self._appendLog("错误: 串口会话未连接")
            return
        self.readStatusRequested.emit()

    def _runDebugSnapshot(self) -> None:
        if not self._client.is_connected:
            self._appendLog("错误: 串口会话未连接")
            return
        self.debugSnapshotRequested.emit()

    def _onAutoPollChanged(self, checked: bool) -> None:
        if checked:
            self.startPollingRequested.emit(POWER_POLL_INTERVAL_MS)
        else:
            self.stopPollingRequested.emit()
            self._appendLog("主机轮询已关闭")

    def _onOutputSwitchChanged(self, checked: bool) -> None:
        self._stagedOutputEnabled = checked
        if not self._client.is_connected or self._isWriteBlocked():
            return
        if self._client.is_busy:
            self._appendLog("协议请求进行中，请稍后再写入")
            return
        self._beginWriteOperation()
        self._writePollingRestartPending = True
        self._appendLog(f"输出开关正在设置为{'开启' if checked else '关闭'}")
        self.powerStateRequested.emit(checked)

    def _onClearFaultRequested(self) -> None:
        """清除锁存故障并回到安全待机态（不自动打开输出）。"""
        if not self._client.is_connected or self._isWriteBlocked() or self._client.is_busy:
            return
        self._stagedOutputEnabled = False
        self._beginWriteOperation()
        self._writePollingRestartPending = True
        self._appendLog("正在清除故障并复位到待机状态...")
        self.powerStateRequested.emit(False)

    def _applyOutputLimits(self) -> None:
        if not self._client.is_connected:
            self._appendLog("错误: 串口会话未连接")
            return
        if self._isWriteBlocked():
            return
        if self._client.is_busy:
            self._appendLog("协议请求进行中，请稍后再写入")
            return
        self.dashboardTab.outputVoltage.interpretText()
        self.dashboardTab.outputCurrent.interpretText()
        voltage_mv = int(round(self.dashboardTab.outputVoltage.value() * 1000))
        current_ma = int(round(self.dashboardTab.outputCurrent.value() * 1000))
        self._dispatchOutputLimits(voltage_mv, current_ma, silent=False, reason="")

    def _quickApplyOutputLimits(self, voltage_mv: int, current_ma: int, reason: str = "") -> None:
        """Coalesced fast write for sliders & M1..M6 presets without interrupting active drags."""
        if not self._client.is_connected:
            return
        if self._writeInFlight or self._client.is_busy:
            self._queuedQuickOutputSettings = (int(voltage_mv), int(current_ma), str(reason))
            return
        self._dispatchOutputLimits(int(voltage_mv), int(current_ma), silent=(not reason), reason=str(reason))

    def _dispatchOutputLimits(
        self,
        voltage_mv: int,
        current_ma: int,
        *,
        silent: bool,
        reason: str,
    ) -> None:
        enabled = self._stagedOutputEnabled if self._stagedOutputEnabled is not None else self.dashboardTab._outputEnabled
        self._pendingOutputSettings = (voltage_mv, current_ma)
        self._lastOutputWriteSilent = silent
        self._lastOutputWriteReason = reason
        self._beginWriteOperation()
        self._writePollingRestartPending = True
        if reason:
            self._appendLog(f"快捷挡位 {reason}: 设定 {voltage_mv / 1000.0:.3f} V / {current_ma / 1000.0:.3f} A")
        elif silent:
            self._appendLog(f"滑块快调: 设定 {voltage_mv / 1000.0:.3f} V / {current_ma / 1000.0:.3f} A")
        self.outputLimitsRequested.emit(voltage_mv, current_ma, enabled)

    def _applyProtectionValues(self) -> None:
        if not self._client.is_connected:
            self._appendLog("错误: 串口会话未连接")
            return
        if self._isWriteBlocked():
            return
        if self._client.is_busy:
            self._appendLog("协议请求进行中，请稍后再写入")
            return
        self.protectionTab.ovp.interpretText()
        self.protectionTab.ocp.interpretText()
        self.protectionTab.otp.interpretText()
        ovp_mv = int(round(self.protectionTab.ovp.value() * 1000))
        ocp_ma = int(round(self.protectionTab.ocp.value() * 1000))
        otp_mc = int(round(self.protectionTab.otp.value() * 100))
        fan_value = int(self.protectionTab.fan.value())
        self._pendingProtectionSettings = (ovp_mv, ocp_ma, otp_mc, fan_value)
        self._beginWriteOperation()
        self._writePollingRestartPending = True
        self.protectionValuesRequested.emit(ovp_mv, ocp_ma, otp_mc, fan_value)

    def _syncPendingParameterWrites(self, status: PowerStatus) -> None:
        if self._pendingOutputSettings is not None:
            voltage_mv, current_ma = self._pendingOutputSettings
            editor_voltage_mv = int(round(self.dashboardTab.outputVoltage.value() * 1000))
            editor_current_ma = int(round(self.dashboardTab.outputCurrent.value() * 1000))
            if (
                status.set_voltage_limit_mv == voltage_mv
                and status.set_current_limit_ma == current_ma
                and editor_voltage_mv == voltage_mv
                and editor_current_ma == current_ma
            ):
                self.dashboardTab.clearDirty()
                self._pendingOutputSettings = None

        if self._pendingProtectionSettings is not None:
            ovp_mv, ocp_ma, otp_mc, fan_value = self._pendingProtectionSettings
            editor_ovp_mv = int(round(self.protectionTab.ovp.value() * 1000))
            editor_ocp_ma = int(round(self.protectionTab.ocp.value() * 1000))
            editor_otp_mc = int(round(self.protectionTab.otp.value() * 100))
            editor_fan_value = int(self.protectionTab.fan.value())
            if (
                status.ovp_set_value_mv == ovp_mv
                and status.ocp_set_value_ma == ocp_ma
                and status.otp_set_value_mc == otp_mc
                and status.fan_set_value == fan_value
                and editor_ovp_mv == ovp_mv
                and editor_ocp_ma == ocp_ma
                and editor_otp_mc == otp_mc
                and editor_fan_value == fan_value
            ):
                self.protectionTab.clearDirty()
                self._pendingProtectionSettings = None

    def _setWriteControlsEnabled(self, enabled: bool, *, busy: bool = False) -> None:
        self._writeInFlight = not enabled and busy
        self._setAllWriteEnabled(enabled, busy=busy)
        if enabled:
            self._writeWatchdogTimer.stop()

    def _beginWriteOperation(self) -> None:
        self._setWriteControlsEnabled(False, busy=True)
        self._writeWatchdogTimer.start(WRITE_WATCHDOG_TIMEOUT_MS)

    def _isWriteBlocked(self) -> bool:
        if not self._writeInFlight:
            return False
        if not self._client.is_busy:
            self._appendLog("上次写入已结束，已恢复写入状态")
            self._setWriteControlsEnabled(True)
            return False
        if not self._writeWatchdogTimer.isActive():
            self._writeWatchdogTimer.start(WRITE_WATCHDOG_TIMEOUT_MS)
        self._appendLog("写入进行中，请等待当前操作完成")
        return True

    def _onWriteWatchdogTimeout(self) -> None:
        if not self._writeInFlight:
            return
        self._writeInFlight = False
        self._setAllWriteEnabled(self._client.is_connected)
        self._appendLog("写入等待超时，已恢复按钮；若设备仍忙，请稍后再写")
        logger.error("Power write watchdog released UI controls")
        if self._writePollingRestartPending:
            self._scheduleAutoPollingRestartAfterWrite()

    def _onOutputLimitsWritten(self) -> None:
        self._setWriteControlsEnabled(True)
        reason = self._lastOutputWriteReason
        silent = self._lastOutputWriteSilent
        self._lastOutputWriteReason = ""
        self._lastOutputWriteSilent = False
        if reason:
            showMessage(self, f"已切换至 {reason}", "目标电压与限流值已下发生效。", level="success")
        elif not silent:
            showMessage(self, "输出设定已更新", "目标电压、限流与输出状态已生效。", level="success")
        self._scheduleAutoPollingRestartAfterWrite()

    def _onProtectionValuesWritten(self) -> None:
        self._setWriteControlsEnabled(True)
        showMessage(self, "保护阈值已更新", "OVP / OCP / OTP / 风扇参数已写入。", level="success")
        self._scheduleAutoPollingRestartAfterWrite()

    def _onPowerStateWritten(self, enabled: bool) -> None:
        self._setWriteControlsEnabled(True)
        if self._stagedOutputEnabled == enabled:
            self._stagedOutputEnabled = None
        self._appendLog(f"输出已设置为{'开启' if enabled else '关闭'}")
        self._scheduleAutoPollingRestartAfterWrite()

    # --- Multi-Point Output Voltage Calibration Handlers ---
    def _readVoutCalibrationQuiet(self) -> None:
        if not self._client.is_connected or self._writeInFlight or self._client.is_busy:
            return
        self._readVoutCalibration()

    def _readVoutCalibration(self) -> None:
        if not self._client.is_connected:
            self._appendLog("错误: 串口会话未连接")
            return
        if self._isWriteBlocked() or self._client.is_busy:
            return
        self._beginWriteOperation()
        self._writePollingRestartPending = True
        self._appendLog("正在读取下位机输出电压多点校准表...")
        self.readVoutCalRequested.emit()

    def _setVoutCalTargetVoltage(self, row: int, target_v: float) -> None:
        if not self._client.is_connected:
            self._appendLog("错误: 串口会话未连接")
            return
        if self._isWriteBlocked() or self._client.is_busy:
            return
        voltage_mv = int(round(target_v * 1000))
        current_ma = int(round(self.dashboardTab.outputCurrent.value() * 1000))
        if current_ma <= 0:
            current_ma = 1000
        self.dashboardTab.outputVoltage.setValue(target_v)
        self._stagedOutputEnabled = True
        self._pendingOutputSettings = (voltage_mv, current_ma)
        self._beginWriteOperation()
        self._writePollingRestartPending = True
        self._appendLog(f"校准测试点 P{row + 1}: 正在设定输出 {target_v:.3f} V（开启输出）")
        self.outputLimitsRequested.emit(voltage_mv, current_ma, True)

    def _recordVoutCalPoint(self, row: int, measured_v: float) -> None:
        if not self._client.is_connected:
            self._appendLog("错误: 串口会话未连接")
            return
        if self._isWriteBlocked() or self._client.is_busy:
            return
        if not self.calibrationTab.isRowReadyToRecord(row):
            showMessage(
                self,
                "请先输入最新万用表读数",
                f"P{row + 1} 已完成校准。若需微调，请修改实测值后再点击「微调校准」。",
                level="warning",
            )
            return
        measured_mv = int(round(measured_v * 1000))
        if measured_mv <= 0 or measured_mv > 65000:
            showMessage(self, "实测电压无效", "请输入 0.001 ~ 65.000 V 之间的实测电压。", level="warning")
            return
        if self._lastStatus is not None and self._lastStatus.vout_v > 0.2:
            if abs(measured_v - self._lastStatus.vout_v) > max(2.0, measured_v * 0.25):
                showMessage(
                    self,
                    "当前输出电压不匹配",
                    f"设备当前实时输出为 {self._lastStatus.vout_v:.3f} V，与填入的 {measured_v:.3f} V 相差过大，请先点「设为此电压」等待稳定后再回传。",
                    level="warning",
                )
                return
        self._beginWriteOperation()
        self._writePollingRestartPending = True
        self._appendLog(f"校准点 P{row + 1}: 正在采集 VOUT_RAW 并绑定仪器值 {measured_v:.3f} V...")
        self.recordVoutCalPointRequested.emit(row, measured_mv, self.calibrationTab.getPoints())

    def _clearSingleVoutCalPoint(self, row: int) -> None:
        if not self._client.is_connected or self._isWriteBlocked() or self._client.is_busy:
            return
        pts = self.calibrationTab.getPoints()
        if row < len(pts):
            pts[row] = None
        remaining = [pair for pair in pts if pair is not None]
        self._beginWriteOperation()
        self._writePollingRestartPending = True
        self._appendLog(f"正在清除校准点 P{row + 1} 并更新下位机校准表...")
        self.saveVoutCalRequested.emit(remaining, False)

    def _saveVoutCalibrationToFlash(self) -> None:
        if not self._client.is_connected or self._isWriteBlocked() or self._client.is_busy:
            return
        pts = [pair for pair in self.calibrationTab.getPoints() if pair is not None]
        self._beginWriteOperation()
        self._writePollingRestartPending = True
        self._appendLog(f"正在写入 {len(pts)} 个校准点并固化到片内 Flash...")
        self.saveVoutCalRequested.emit(pts, True)

    def _clearAllVoutCalibration(self) -> None:
        if not self._client.is_connected or self._isWriteBlocked() or self._client.is_busy:
            return
        self._beginWriteOperation()
        self._writePollingRestartPending = True
        self._appendLog("正在清除全部输出电压校准点并保存到 Flash...")
        self.saveVoutCalRequested.emit([], True)

    def _onVoutCalibrationLoaded(self, points: object, live_raw: int) -> None:
        self._setWriteControlsEnabled(True)
        pts = list(points or [])
        self.calibrationTab.setPoints(pts, live_raw=live_raw)
        desc = ", ".join(f"(RAW={r} -> {m / 1000.0:.3f}V)" for r, m in pts) or "无（使用理论公式）"
        self._appendLog(f"已读取校准表 ({len(pts)} 点, 当前 RAW={live_raw}): {desc}")
        self._scheduleAutoPollingRestartAfterWrite()

    def _onVoutCalibrationUpdated(self, points: object, live_raw: int, committed: bool) -> None:
        self._setWriteControlsEnabled(True)
        pts = list(points or [])
        self.calibrationTab.setPoints(pts, live_raw=live_raw)
        desc = ", ".join(f"(RAW={r} -> {m / 1000.0:.3f}V)" for r, m in pts) or "已恢复出厂理论公式"
        if committed:
            self._appendLog(f"校准表已写入并保存到 Flash ({len(pts)} 点): {desc}")
            showMessage(self, "校准已保存到 Flash", f"已固化 {len(pts)} 个输出电压校准点，断电不丢失。", level="success")
        else:
            self._appendLog(f"校准点已实时生效 ({len(pts)} 点, 本次 RAW={live_raw}): {desc}")
            showMessage(self, "校准点已生效", "闭环已立即按新曲线修正输出；全部测完后请点「保存到 Flash」。", level="success")
        self._scheduleAutoPollingRestartAfterWrite()

    def _onWriteTransactionFinished(self) -> None:
        if self._writeInFlight:
            self._setWriteControlsEnabled(self._client.is_connected)
        queued = self._queuedQuickOutputSettings
        if queued is not None:
            self._queuedQuickOutputSettings = None
            if self._client.is_connected and not self._client.is_busy:
                voltage_mv, current_ma, reason = queued
                self._dispatchOutputLimits(voltage_mv, current_ma, silent=(not reason), reason=reason)

    def _onClientError(self, message: str) -> None:
        self._appendLog(f"错误: {message}")
        logger.error(f"Power client error: {message}")
        self._setWriteControlsEnabled(True)
        showMessage(self, "通信错误", message, level="error")
        if self._writePollingRestartPending:
            self._scheduleAutoPollingRestartAfterWrite()

    def _disconnectAfterWriteFailures(self) -> None:
        if not self._client.is_connected and self._manualSession is None:
            return
        self._appendLog("连续写入失败 3 次，已断开串口")
        showMessage(self, "串口已断开", "连续写入失败 3 次，已关闭当前串口连接。", level="error")
        self._disconnectManualSession()

    def _disconnectAfterCommunicationFailures(self, message: str) -> None:
        if not self._client.is_connected and self._manualSession is None:
            return
        self._appendLog(f"连续通信失败 3 次，已断开串口。最后错误: {message}")
        showMessage(self, "串口已断开", "连续 3 次无回复或错误，已关闭当前串口连接。", level="error")
        self._disconnectManualSession()

    def _scheduleAutoPollingRestartAfterWrite(self) -> None:
        # F4CPPowerClient already resumes streaming after WRITE_POLL_RESUME_DELAY_MS (60 ms);
        # avoid a redundant second STOP/READ/STREAM_START cycle here that causes a 600 ms freeze.
        self._writePollingRestartPending = False
        self._writePollRestartTimer.stop()

    def _restartAutoPollingAfterWrite(self) -> None:
        if self._shutdownDone or not self.autoPollSwitch.isChecked() or not self._client.is_connected:
            return
        self.startPollingRequested.emit(POWER_POLL_INTERVAL_MS)
        self._appendLog("写入后已请求重启主机轮询")

    def _handleDebugSnapshotReady(self, snapshot: DebugSnapshot) -> None:
        self._appendLog(self._client.pretty_print_debug_snapshot(snapshot))
        self._appendLog(self._diagnoseDebugSnapshot(snapshot))
        showMessage(self, "调试快照已采集", "已写入「通信日志」页签。", level="info")

    @staticmethod
    def _diagnoseDebugSnapshot(snapshot: DebugSnapshot) -> str:
        if snapshot.output_voltage_raw >= 65400:
            return "调试判断: type27 接近 65520 (16x 过采满量程)，请优先检查 MCU ADC 或前端电路。"
        if snapshot.ovp_set_value_mv is not None and snapshot.ovp_set_value_mv == DEFAULT_OVP_SET_VALUE_MV and abs(snapshot.output_voltage_mv - snapshot.ovp_set_value_mv) <= 5:
            return "调试判断: type32=44000 与 type12 重叠，主机字段映射可能有误。"
        if snapshot.loop_current_reference_ma is not None:
            return "调试判断: 已收到控制环调试量；带载时重点看 type41/type42 是否跟随，以及 type43 是否顶到设定电压。"
        return "调试判断: type27 数值合理；如果界面仍异常，请检查主机解析和绑定。"

    def _applyDisconnectedState(self) -> None:
        self._stagedOutputEnabled = None
        self._pendingOutputSettings = None
        self._pendingProtectionSettings = None
        self._queuedQuickOutputSettings = None
        self._lastOutputWriteSilent = False
        self._lastOutputWriteReason = ""
        self._writePollingRestartPending = False
        self._connectedPort = ""
        self._setWriteControlsEnabled(False, busy=False)
        self._writePollRestartTimer.stop()
        self.dashboardTab.clearDirty()
        self.protectionTab.clearDirty()
        self.topBar.setOnline(False)
        self.dashboardTab.setOutputEnabledVisual(False)
        if self.protectionTab.ovp.value() <= 0:
            self.protectionTab.ovp.setValue(DEFAULT_OVP_SET_VALUE_MV / 1000.0)

    def _appendLog(self, text: str) -> None:
        if self._appendLogBusy:
            return
        self._appendLogBusy = True
        try:
            if not (text.startswith("RX ") or text.startswith("TX ")):
                logger.info(text)
            ts = time.strftime("%H:%M:%S")
            if getattr(self, "logEdit", None) is not None:
                self.logEdit.append(f"[{ts}] {text}")
        finally:
            self._appendLogBusy = False

    def pushMockPowerSample(self) -> None:
        self.dashboardTab.pushMock()

    def suspendForDaplink(self) -> None:
        self._appendLog("DAPLink 操作开始，已暂停主机轮询。")
        self.stopPollingRequested.emit()

    def resumeAfterDaplink(self) -> None:
        if self._shutdownDone:
            return
        if self.autoPollSwitch.isChecked() and self._client.is_connected:
            self.startPollingRequested.emit(POWER_POLL_INTERVAL_MS)
            self._appendLog("DAPLink 操作结束，已恢复主机轮询。")

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        if self._shutdownDone:
            return
        self._shutdownDone = True
        self._mockTimer.stop()
        self._writePollRestartTimer.stop()
        try:
            if self._manualSession:
                self._manualSession.close()
        except Exception as exc:
            logger.error(f"PowerPage manual session shutdown failed: {exc}")
        self._manualSession = None
        try:
            if self._clientThread.isRunning():
                QMetaObject.invokeMethod(self._client, "shutdown", Qt.ConnectionType.BlockingQueuedConnection)
        except Exception as exc:
            logger.error(f"PowerPage client shutdown failed: {exc}")
        if self._clientThread.isRunning():
            self._clientThread.quit()
            if not self._clientThread.wait(3000):
                logger.error("PowerPage client thread did not exit within 3000 ms")
