# -*- coding: utf-8 -*-
# -------------------------------
#  @Project : F4CP
#  @Time    : 2026 - 06-03 15:30
#  @FileName: controller_power_page.py
#  @FileType: 电源页面控制器文件，负责电源页面信号和操作入口编排
#  @Software: PyCharm 2024.1.6 (Professional Edition)
#  @System  : Windows 11 23H2
#  @Author  : UF4
#  @Contact :
#  @Python  : 3.10
# -------------------------------

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject

from config import cfg

if TYPE_CHECKING:
    from app.widgets.pages.page_power import PowerPage


class PowerPageController(QObject):
    """集中管理电源页面的 UI 事件、页面请求信号和后台 client 信号。"""

    def __init__(self, page: "PowerPage"):
        super().__init__(page)
        self.page = page

    def bind(self) -> None:
        """绑定页面全部交互入口。"""
        self._bindClientSignals()
        self._bindPageRequests()
        self._bindUserActions()

    def _bindClientSignals(self) -> None:
        page = self.page
        client = page._client

        # 后台 client 只把结果抛回页面，页面继续负责状态展示和日志渲染。
        client.log.connect(page._appendLog)
        client.error.connect(page._onClientError)
        client.connectionChanged.connect(page._onConnectionChanged)
        client.statusUpdated.connect(page._updateStatusView)
        client.debugSnapshotReady.connect(page._handleDebugSnapshotReady)
        client.outputLimitsWritten.connect(page._onOutputLimitsWritten)
        client.protectionValuesWritten.connect(page._onProtectionValuesWritten)
        client.powerStateWritten.connect(page._onPowerStateWritten)
        client.voutCalibrationLoaded.connect(page._onVoutCalibrationLoaded)
        client.voutCalibrationUpdated.connect(page._onVoutCalibrationUpdated)
        client.writeTransactionFinished.connect(page._onWriteTransactionFinished)
        client.writeFailureLimitReached.connect(page._disconnectAfterWriteFailures)
        client.communicationFailureLimitReached.connect(page._disconnectAfterCommunicationFailures)

    def _bindPageRequests(self) -> None:
        page = self.page
        client = page._client

        # 页面信号描述“想做什么”，client 方法负责真正执行。
        page.attachSessionRequested.connect(client.attach_session)
        page.detachSessionRequested.connect(client.detach_session)
        page.readStatusRequested.connect(client.request_read_status)
        page.debugSnapshotRequested.connect(client.request_debug_snapshot)
        page.outputLimitsRequested.connect(client.request_set_output_limits)
        page.protectionValuesRequested.connect(client.request_set_protection_values)
        page.powerStateRequested.connect(client.request_set_power_state)
        page.readVoutCalRequested.connect(client.request_read_vout_calibration)
        page.recordVoutCalPointRequested.connect(client.request_record_vout_cal_point)
        page.saveVoutCalRequested.connect(client.request_save_vout_calibration)
        page.startPollingRequested.connect(client.start_polling)
        page.stopPollingRequested.connect(client.stop_polling)

    def _bindUserActions(self) -> None:
        page = self.page

        page.refreshTargetButton.clicked.connect(page.refreshSerialPorts)
        page.connectButton.clicked.connect(page.toggleConnection)
        page.refreshButton.clicked.connect(page._readStatusOnce)
        page.debugButton.clicked.connect(page._runDebugSnapshot)
        page.autoPollSwitch.checkedChanged.connect(page._onAutoPollChanged)
        page.dashboardTab.outputToggleRequested.connect(page._onOutputSwitchChanged)
        page.dashboardTab.clearFaultRequested.connect(page._onClearFaultRequested)
        page.dashboardTab.applyOutputRequested.connect(page._applyOutputLimits)
        page.dashboardTab.quickApplyOutputRequested.connect(page._quickApplyOutputLimits)
        page.protectionTab.applyProtectionRequested.connect(page._applyProtectionValues)
        page.voutCalCard.readButton.clicked.connect(page._readVoutCalibration)
        page.voutCalCard.saveFlashButton.clicked.connect(page._saveVoutCalibrationToFlash)
        page.voutCalCard.clearAllButton.clicked.connect(page._clearAllVoutCalibration)
        page.voutCalCard.setTargetRequested.connect(page._setVoutCalTargetVoltage)
        page.voutCalCard.recordPointRequested.connect(page._recordVoutCalPoint)
        page.voutCalCard.clearPointRequested.connect(page._clearSingleVoutCalPoint)
        page.clearLogButton.clicked.connect(page.logEdit.clear)
        cfg.themeChanged.connect(page._onThemeChanged)
