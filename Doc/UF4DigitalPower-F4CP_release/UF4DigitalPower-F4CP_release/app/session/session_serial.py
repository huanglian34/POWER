# -*- coding: utf-8 -*-
# -------------------------------
#  @Project : F4CP
#  @Time    : 2026 - 01-08 12:30
#  @FileName: session_serial.py
#  @FileType: 串口会话文件，负责串口收发和 Qt 事件转发
#  @Software: PyCharm 2024.1.6 (Professional Edition)
#  @System  : Windows 11 23H2
#  @Author  : UF4
#  @Contact :
#  @Python  : 3.10
# -------------------------------

from __future__ import annotations

import sys
import time

from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Optional

from PyQt5.QtCore import QIODevice, QEvent, QCoreApplication, QObject, QMutex, QMutexLocker
from PyQt5.QtSerialPort import QSerialPort, QSerialPortInfo

from config import get_logger

logger = get_logger("SerialSession")

class SerialEventType(IntEnum):
    RX = QEvent.registerEventType()
    TX = QEvent.registerEventType()
    ERROR = QEvent.registerEventType()
    SEND = QEvent.registerEventType()
    STATE = QEvent.registerEventType()


class SerialEvent(QEvent):
    def __init__(self, etype: SerialEventType):  # NOQA
        super().__init__(QEvent.Type(int(etype)))
        self.timestamp = time.monotonic()


@dataclass
class RxPayload:
    data: bytes


class RxEvent(SerialEvent):
    def __init__(self, data: bytes):
        super().__init__(SerialEventType.RX)
        self.payload = RxPayload(data=data)


@dataclass
class TxPayload:
    data: bytes


class TxEvent(SerialEvent):
    def __init__(self, data: bytes):
        super().__init__(SerialEventType.TX)
        self.payload = TxPayload(data=data)


@dataclass
class SendPayload:
    data: bytes


class SendEvent(SerialEvent):
    def __init__(self, data: bytes):
        super().__init__(SerialEventType.SEND)
        self.payload = SendPayload(data=data)


@dataclass
class ErrorPayload:
    code: int
    message: str
    fatal: bool = False


class ErrorEvent(SerialEvent):
    def __init__(self, code: int, message: str, fatal: bool = False):
        super().__init__(SerialEventType.ERROR)
        self.payload = ErrorPayload(
            code=code,
            message=message,
            fatal=fatal,
        )


class SerialState(IntEnum):
    CLOSED = 0
    OPENING = 1
    OPEN = 2
    ERROR = 3


@dataclass
class StatePayload:
    state: SerialState
    info: Optional[str] = None


class StateEvent(SerialEvent):
    def __init__(self, state: SerialState, info: str | None = None):
        super().__init__(SerialEventType.STATE)
        self.payload = StatePayload(state=state, info=info)


@dataclass(frozen=True)
class SerialPortEntry:
    """Rich serial port metadata including Windows USB BusReportedDeviceDesc."""

    port: str
    description: str = ""
    bus_description: str = ""
    instance_id: str = ""
    vid: int = 0
    pid: int = 0
    role: str = ""  # "uf4com" | "vofa" | ""

    @property
    def compact_label(self) -> str:
        if self.role == "uf4com":
            return f"{self.port} · 控制 (UF4COM)"
        if self.role == "vofa":
            return f"{self.port} · 波形 (VOFA+)"
        detail = self.bus_description or self.description
        if detail and detail not in ("USB 串行设备", "USB Serial Device"):
            short = detail if len(detail) <= 18 else detail[:17] + "…"
            return f"{self.port} · {short}"
        return self.port

    @property
    def full_label(self) -> str:
        detail = self.bus_description or self.description
        return f"{self.port} · {detail}" if detail else self.port


def _query_windows_port_bus_info() -> dict[str, tuple[str, str]]:
    """Return {COMx: (BusReportedDeviceDesc, InstanceId)} via SetupAPI (<5 ms)."""
    if sys.platform != "win32":
        return {}
    result: dict[str, tuple[str, str]] = {}
    try:
        import ctypes
        from ctypes import wintypes

        setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        class _DEVPROPKEY(ctypes.Structure):
            _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]

        class _SP_DEVINFO_DATA(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("ClassGuid", _GUID),
                ("DevInst", wintypes.DWORD),
                ("Reserved", ctypes.c_void_p),
            ]

        guid_ports = _GUID(
            0x4D36E978,
            0xE325,
            0x11CE,
            (ctypes.c_ubyte * 8)(0xBF, 0xC1, 0x08, 0x00, 0x2B, 0xE1, 0x03, 0x18),
        )
        pkey_bus_desc = _DEVPROPKEY(
            _GUID(
                0x540B947E,
                0x8B40,
                0x45BC,
                (ctypes.c_ubyte * 8)(0xA8, 0xA2, 0x6A, 0x0B, 0x89, 0x4C, 0xBD, 0xA2),
            ),
            4,
        )

        setupapi.SetupDiGetClassDevsW.argtypes = [
            ctypes.POINTER(_GUID),
            wintypes.LPCWSTR,
            wintypes.HWND,
            wintypes.DWORD,
        ]
        setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
        setupapi.SetupDiEnumDeviceInfo.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_SP_DEVINFO_DATA),
        ]
        setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
        setupapi.SetupDiGetDevicePropertyW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_SP_DEVINFO_DATA),
            ctypes.POINTER(_DEVPROPKEY),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
        ]
        setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL
        setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_SP_DEVINFO_DATA),
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
        setupapi.SetupDiOpenDevRegKey.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_SP_DEVINFO_DATA),
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        setupapi.SetupDiOpenDevRegKey.restype = wintypes.HKEY
        setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
        advapi32.RegQueryValueExW.argtypes = [
            wintypes.HKEY,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.RegCloseKey.argtypes = [wintypes.HKEY]

        invalid_handle = ctypes.c_void_p(-1).value
        hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid_ports), None, None, 0x02)
        if not hdev or hdev == invalid_handle:
            return {}
        try:
            idx = 0
            dev_info = _SP_DEVINFO_DATA()
            dev_info.cbSize = ctypes.sizeof(dev_info)
            while setupapi.SetupDiEnumDeviceInfo(hdev, idx, ctypes.byref(dev_info)):
                idx += 1
                port_name = ""
                hkey = setupapi.SetupDiOpenDevRegKey(hdev, ctypes.byref(dev_info), 1, 0, 1, 0x20019)
                if hkey and hkey != invalid_handle:
                    buf = ctypes.create_unicode_buffer(64)
                    sz = wintypes.DWORD(ctypes.sizeof(buf))
                    if advapi32.RegQueryValueExW(hkey, "PortName", None, None, buf, ctypes.byref(sz)) == 0:
                        port_name = buf.value.strip()
                    advapi32.RegCloseKey(hkey)
                if not port_name:
                    continue
                prop_type = wintypes.DWORD(0)
                bus_buf = ctypes.create_unicode_buffer(256)
                req = wintypes.DWORD(0)
                bus_desc = ""
                if setupapi.SetupDiGetDevicePropertyW(
                    hdev,
                    ctypes.byref(dev_info),
                    ctypes.byref(pkey_bus_desc),
                    ctypes.byref(prop_type),
                    bus_buf,
                    ctypes.sizeof(bus_buf),
                    ctypes.byref(req),
                    0,
                ):
                    bus_desc = bus_buf.value.strip()
                id_buf = ctypes.create_unicode_buffer(256)
                inst_id = ""
                if setupapi.SetupDiGetDeviceInstanceIdW(hdev, ctypes.byref(dev_info), id_buf, 256, None):
                    inst_id = id_buf.value.strip()
                result[port_name.upper()] = (bus_desc, inst_id)
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(hdev)
    except Exception as exc:
        logger.debug(f"Windows SetupAPI port description query skipped: {exc}")
    return result


def _detect_uf4_port_role(vid: int, pid: int, bus_desc: str, instance_id: str) -> str:
    upper_desc = bus_desc.upper()
    upper_id = instance_id.upper()
    if "VOFA" in upper_desc or "TELEMETRY" in upper_desc:
        return "vofa"
    if "UF4COM" in upper_desc or ("UF4" in upper_desc and "CONTROL" in upper_desc):
        return "uf4com"
    if vid == 0xCAFE and pid in (0x4741, 0x4742):
        if "&MI_02" in upper_id:
            return "vofa"
        return "uf4com"
    return ""


def listSerialPortEntries() -> list[SerialPortEntry]:
    """Return enriched serial port entries sorted with UF4COM control port first."""
    bus_map = _query_windows_port_bus_info()
    entries: list[SerialPortEntry] = []
    for info in QSerialPortInfo.availablePorts():
        port = info.portName().strip()
        if not port:
            continue
        vid = int(info.vendorIdentifier()) if info.hasVendorIdentifier() else 0
        pid = int(info.productIdentifier()) if info.hasProductIdentifier() else 0
        bus_desc, inst_id = bus_map.get(port.upper(), ("", ""))
        role = _detect_uf4_port_role(vid, pid, bus_desc, inst_id)
        entries.append(
            SerialPortEntry(
                port=port,
                description=info.description().strip(),
                bus_description=bus_desc,
                instance_id=inst_id,
                vid=vid,
                pid=pid,
                role=role,
            )
        )

    def _sort_key(entry: SerialPortEntry) -> tuple[int, int, str]:
        priority = 0 if entry.role == "uf4com" else (2 if entry.role == "vofa" else 1)
        digits = "".join(ch for ch in entry.port if ch.isdigit())
        return (priority, int(digits) if digits else 9999, entry.port)

    entries.sort(key=_sort_key)
    return entries


def listSerialPorts() -> list[str]:
    """
    返回串口列表字符串
    :return: str(portName)
    """

    return [e.port for e in listSerialPortEntries()]

def listSerialPortInfos() -> list[QSerialPortInfo]:
    """
    返回串口列表对象
    :return: QSerialPortInfo
    """
    return QSerialPortInfo.availablePorts()

@dataclass
class SerialConfig:
    # 默认配置
    port: str | None
    baudrate: int | QSerialPort.BaudRate = 921600
    bytesize: QSerialPort.DataBits = QSerialPort.DataBits.Data8  # 8位数据位
    parity: QSerialPort.Parity = QSerialPort.Parity.NoParity  # 无校验位
    stopbits: QSerialPort.StopBits = QSerialPort.StopBits.OneStop  # NOQA 停止位
    read_timeout_s: float = 0.1  # 超时时间


class SerialSession(QObject):
    def __init__(self, cfg: SerialConfig,_event_receiver: Optional[QObject] = None):
        """创建串口会话。

        这个类只关心串口收发和事件投递，不理解上层协议内容。
        """
        super().__init__()

        # 事件接收器
        self._event_receiver: Optional[QObject] = _event_receiver

        # 回调函数
        self.on_rx: Optional[Callable[[bytes], None]] = None
        self.on_tx: Optional[Callable[[bytes], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None

        self._write_lock = QMutex()
        # protect access to the event receiver when set from different threads
        self._event_lock = QMutex()

        self._ser = None
        self.cfg = cfg

    def set_event_receiver(self, receiver: Optional[QObject]) -> None:
        """设置将接收本次会话发布的Qt事件的QObject。

        如果设置为 None，事件将通过 Python 回调传递
        （on_rx/on_tx/on_error）作为备选方案。

        接收方必须是QObject（或无）的。该任务受保护
        通过互斥体实现，以便在不同线程调用时更安全。
        """
        if receiver is not None and not isinstance(receiver, QObject):
            raise TypeError("event receiver must be a QObject or None")

        with QMutexLocker(self._event_lock):
            self._event_receiver = receiver


    def open(self):
        """按配置打开串口，并把 readyRead/error 信号接到本会话。"""
        if self.is_open:
            return
        self._post_event(StateEvent(SerialState.OPENING))
        self._ser = QSerialPort()  # 创建端口
        self._ser.setPortName(self.cfg.port)  # 端口名称
        self._ser.setBaudRate(self.cfg.baudrate)  # 波特率

        self._ser.setDataBits(self.cfg.bytesize)
        self._ser.setParity(self.cfg.parity)
        self._ser.setStopBits(self.cfg.stopbits)

        # 打开串口
        if not self._ser.open(QIODevice.OpenModeFlag.ReadWrite):
            # error() 返回 QSerialPort.SerialPortError 枚举
            error_code = self._ser.error()
            error_str = self._ser.errorString()
            message = (
                f"串口打开失败 {self.cfg.port}: "
                f"{error_str} (代码: {error_code})"
            )
            logger.error(message)
            raise RuntimeError(message)
        self._ser.readyRead.connect(self._on_ready_read)
        self._ser.errorOccurred.connect(self._on_error_occurred)
        self._post_event(StateEvent(SerialState.OPEN))

    @property
    def is_open(self) -> bool:
        return bool(self._ser) and self._ser.isOpen()

    def close(self):
        """关闭串口并广播 CLOSED 状态，便于页面和协议层统一收尾。"""
        if not self._ser:
            self._post_event(StateEvent(SerialState.CLOSED))
            return

        if self._ser.isOpen():
            self._ser.readyRead.disconnect()
            self._ser.errorOccurred.disconnect()
            self._ser.close()

        self._ser = None
        self._post_event(StateEvent(SerialState.CLOSED))

    def write(self, data: bytes) -> int:
        """线程内写入数据，成功后再投递 TX 事件给界面日志。"""
        if not self.is_open:
            message = "Serial port is not open"
            logger.error(message)
            raise RuntimeError(message)
        if not data:
            return 0

        with QMutexLocker(self._write_lock):  # PyQt 的 QMutex 并不完全兼容 Python 的 with 语法，需要用 QMutexLocker 来自动解锁
            written = self._ser.write(data)
            if written != len(data):
                message = f"串口写入不完整: 期望 {len(data)} 字节，实际 {written} 字节"
                logger.error(message)
                raise RuntimeError(message)
            timeout_ms = max(500, int(getattr(self.cfg, "read_timeout_s", 0.1) * 1000))
            if not self._ser.waitForBytesWritten(timeout_ms):
                message = f"串口写入超时: {self._ser.errorString()}"
                logger.error(message)
                raise RuntimeError(message)
            logger.info(f"写入数据: {data.hex()}")

        self._post_event(TxEvent(data))
        return len(data)

    def clear_input_buffer(self) -> None:
        """丢弃驱动层已经收到但上层还没消费的输入字节。"""
        if not self.is_open:
            return
        self._ser.clear(QSerialPort.Direction.Input)

    def _post_event(self, evt: SerialEvent):
        """优先把事件投递给 Qt 接收者；没有接收者时退回到 Python 回调。"""
        if self._event_receiver is not None:
            QCoreApplication.postEvent(self._event_receiver, evt)
        else:
            # fallback to callbacks
            if isinstance(evt, RxEvent) and self.on_rx:
                self.on_rx(evt.payload.data)
            elif isinstance(evt, TxEvent) and self.on_tx:
                self.on_tx(evt.payload.data)
            elif isinstance(evt, ErrorEvent) and self.on_error:
                self.on_error(RuntimeError(evt.payload.message))

    def _on_ready_read(self):
        """读取 Qt 缓冲区里的全部字节，并交给上层协议去拆包。"""
        if not self._ser:
            return

        raw = self._ser.readAll()
        data = raw.data() if hasattr(raw, 'data') else bytes(raw)

        if data:
            self._post_event(RxEvent(data))

    def _on_error_occurred(self, err):
        """把 QSerialPort 的错误统一转成 ErrorEvent 和 StateEvent。"""
        if err == QSerialPort.SerialPortError.NoError:
            return
        msg = self._ser.errorString() if self._ser else str(err)
        self._post_event(ErrorEvent(code=int(err), message=msg, fatal=True))
        self._post_event(StateEvent(SerialState.ERROR, info=msg))

    def event(self, e: QEvent):
        """处理 UI 或协议层投递过来的 SEND 事件，保证写串口发生在对象所属线程。"""
        # 处理 从 UI 发布的发送事件：在该对象的线程中执行写入
        try:
            if e.type() == int(SerialEventType.SEND):
                payload = getattr(e, 'payload', None)
                data = getattr(payload, 'data', None) if payload is not None else None
                if data:
                    # perform write; write() will post TxEvent on success
                    try:
                        # write() expects port to be open
                        self.write(data)
                    except Exception as exc:
                        # notify UI of error
                        logger.error(f"{exc}")
                        self._post_event(ErrorEvent(code=-1, message=str(exc), fatal=True))
                        self._post_event(StateEvent(SerialState.ERROR, info=str(exc)))
                return True
        except Exception as exc:
            logger.error(f"{e}")
            pass
        return super().event(e)
