from __future__ import annotations

import ctypes
import os
from pathlib import Path


# Windows HCanbus.dll 发送超时，按官方示例使用单设备句柄接口。
TRANSMIT_TIMEOUT_MS = 100

# CANFD DLC 与真实数据长度的对应表。
DLC2LEN = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]
LEN2DLC = {length: dlc for dlc, length in enumerate(DLC2LEN)}


class DevInfo(ctypes.Structure):
    _fields_ = [
        ("HW_Type", ctypes.c_char * 32),
        ("HW_Ser", ctypes.c_char * 32),
        ("HW_Ver", ctypes.c_char * 32),
        ("FW_Ver", ctypes.c_char * 32),
        ("MF_Date", ctypes.c_char * 32),
    ]


class CanFDConfig(ctypes.Structure):
    _fields_ = [
        ("NomBaud", ctypes.c_uint),
        ("DatBaud", ctypes.c_uint),
        ("NomPres", ctypes.c_char),
        ("NomTseg1", ctypes.c_char),
        ("NomTseg2", ctypes.c_char),
        ("NomSJW", ctypes.c_char),
        ("DatPres", ctypes.c_char),
        ("DatTseg1", ctypes.c_char),
        ("DatTseg2", ctypes.c_char),
        ("DatSJW", ctypes.c_char),
        ("Config", ctypes.c_char),
        ("Model", ctypes.c_char),
        ("Cantype", ctypes.c_char),
        ("Reserved", ctypes.c_char),
    ]


class CanFDMsg(ctypes.Structure):
    _fields_ = [
        ("ID", ctypes.c_uint),
        ("TimeStamp", ctypes.c_uint),
        ("FrameType", ctypes.c_ubyte),
        ("DLC", ctypes.c_ubyte),
        ("ExternFlag", ctypes.c_ubyte),
        ("RemoteFlag", ctypes.c_ubyte),
        ("BusSatus", ctypes.c_ubyte),
        ("ErrSatus", ctypes.c_ubyte),
        ("TECounter", ctypes.c_ubyte),
        ("RECounter", ctypes.c_ubyte),
        ("Data", ctypes.c_ubyte * 64),
    ]


class CanStatus(ctypes.Structure):
    _fields_ = [
        ("BusSatus", ctypes.c_ubyte),
        ("ErrSatus", ctypes.c_ubyte),
        ("TECounter", ctypes.c_ubyte),
        ("RECounter", ctypes.c_ubyte),
        ("TimeStamp", ctypes.c_uint),
    ]


def _decode(value: bytes) -> str:
    """解码 DLL 返回的以 0 结尾的设备信息字符串。"""
    return value.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def len_to_dlc(length: int) -> int:
    """把真实数据长度转换成 HCanbus.dll 需要的 DLC。"""
    if length in LEN2DLC:
        return LEN2DLC[length]
    for candidate in DLC2LEN:
        if candidate >= length:
            return LEN2DLC[candidate]
    return 15


class WindowsL30Can:
    """Windows 专用 HCanbus.dll 封装，避免影响 Linux libcanbus.so 路径。"""

    def __init__(self, dll_path: str | os.PathLike[str]):
        self.dll_path = Path(dll_path)
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(self.dll_path.parent))
        self.dll = ctypes.WinDLL(str(self.dll_path))
        self.opened: set[int] = set()
        self._bind_api()

    def _bind_api(self) -> None:
        """声明 DLL 函数签名，减少 ctypes 默认类型导致的调用错误。"""
        self.dll.CAN_ScanDevice.argtypes = []
        self.dll.CAN_ScanDevice.restype = ctypes.c_int
        self.dll.CAN_OpenDevice.argtypes = [ctypes.c_uint]
        self.dll.CAN_OpenDevice.restype = ctypes.c_int
        self.dll.CAN_CloseDevice.argtypes = [ctypes.c_uint]
        self.dll.CAN_CloseDevice.restype = ctypes.c_int
        self.dll.CANFD_Init.argtypes = [ctypes.c_uint, ctypes.POINTER(CanFDConfig)]
        self.dll.CANFD_Init.restype = ctypes.c_int
        self.dll.CAN_SetFilter.argtypes = [
            ctypes.c_uint,
            ctypes.c_char,
            ctypes.c_char,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_char,
        ]
        self.dll.CAN_SetFilter.restype = ctypes.c_int
        self.dll.CANFD_Transmit.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(CanFDMsg),
            ctypes.c_uint,
            ctypes.c_int,
        ]
        self.dll.CANFD_Transmit.restype = ctypes.c_int
        if hasattr(self.dll, "CANFD_Receive"):
            self.dll.CANFD_Receive.argtypes = [
                ctypes.c_uint,
                ctypes.POINTER(CanFDMsg),
                ctypes.c_uint,
                ctypes.c_int,
            ]
            self.dll.CANFD_Receive.restype = ctypes.c_int
        if hasattr(self.dll, "CAN_ReadDevInfo"):
            self.dll.CAN_ReadDevInfo.argtypes = [ctypes.c_uint, ctypes.POINTER(DevInfo)]
            self.dll.CAN_ReadDevInfo.restype = ctypes.c_int
        if hasattr(self.dll, "CAN_GetStatus"):
            self.dll.CAN_GetStatus.argtypes = [ctypes.c_uint, ctypes.POINTER(CanStatus)]
            self.dll.CAN_GetStatus.restype = ctypes.c_int

    def scan(self) -> int:
        """扫描 Windows USB-CANFD 设备数量。"""
        return max(0, int(self.dll.CAN_ScanDevice()))

    def open(self, dev: int) -> dict[str, str]:
        """打开并初始化指定 Windows 设备。"""
        if dev in self.opened:
            return self.read_info(dev)

        count = self.scan()
        if count <= 0:
            raise RuntimeError("CAN_ScanDevice found no Windows HCanBus devices")
        if dev >= count:
            raise RuntimeError(f"CAN_ScanDevice found {count} devices, dev={dev} is unavailable")

        ret = int(self.dll.CAN_OpenDevice(ctypes.c_uint(dev)))
        if ret != 0:
            raise RuntimeError(f"CAN_OpenDevice dev={dev} failed: {ret}")

        try:
            cfg = CanFDConfig(
                NomBaud=1_000_000,
                DatBaud=5_000_000,
                NomPres=0,
                NomTseg1=0,
                NomTseg2=0,
                NomSJW=0,
                DatPres=0,
                DatTseg1=0,
                DatTseg2=0,
                DatSJW=0,
                Config=0x06,
                Model=0,
                Cantype=1,
                Reserved=0,
            )
            ret = int(self.dll.CANFD_Init(ctypes.c_uint(dev), ctypes.byref(cfg)))
            if ret != 0:
                raise RuntimeError(f"CANFD_Init dev={dev} failed: {ret}")

            ret = int(
                self.dll.CAN_SetFilter(
                    ctypes.c_uint(dev),
                    ctypes.c_char(0),
                    ctypes.c_char(0),
                    ctypes.c_uint(0),
                    ctypes.c_uint(0),
                    ctypes.c_char(1),
                )
            )
            if ret != 0:
                raise RuntimeError(f"CAN_SetFilter dev={dev} failed: {ret}")
        except Exception:
            self.close(dev)
            raise

        self.opened.add(dev)
        return self.read_info(dev)

    def close(self, dev: int) -> None:
        """关闭指定 Windows 设备。"""
        try:
            self.dll.CAN_CloseDevice(ctypes.c_uint(dev))
        finally:
            self.opened.discard(dev)

    def close_all(self) -> None:
        """关闭所有已打开的 Windows 设备。"""
        for dev in list(self.opened):
            self.close(dev)

    def read_info(self, dev: int) -> dict[str, str]:
        """读取设备硬件、序列号和固件信息。"""
        if not hasattr(self.dll, "CAN_ReadDevInfo"):
            return {}
        try:
            info = DevInfo()
            ret = int(self.dll.CAN_ReadDevInfo(ctypes.c_uint(dev), ctypes.byref(info)))
            if ret != 0:
                return {}
            return {
                "type": _decode(info.HW_Type),
                "serial": _decode(info.HW_Ser),
                "hardware": _decode(info.HW_Ver),
                "firmware": _decode(info.FW_Ver),
                "date": _decode(info.MF_Date),
            }
        except (OSError, ValueError):
            return {}

    def send(self, dev: int, can_id: int, payload: bytes) -> int:
        """发送一帧扩展 CANFD + BRS 数据帧。"""
        data = bytes(payload)
        frame = CanFDMsg()
        frame.ID = can_id & 0x1FFFFFFF
        frame.TimeStamp = 0
        frame.FrameType = 0x04
        frame.DLC = len_to_dlc(len(data))
        frame.ExternFlag = 1
        frame.RemoteFlag = 0
        for index, value in enumerate(data[:64]):
            frame.Data[index] = value

        ret = int(
            self.dll.CANFD_Transmit(
                ctypes.c_uint(dev),
                ctypes.byref(frame),
                ctypes.c_uint(1),
                ctypes.c_int(TRANSMIT_TIMEOUT_MS),
            )
        )
        if ret <= 0:
            raise RuntimeError(
                f"CANFD_Transmit dev={dev} failed: ret={ret}, "
                f"id=0x{can_id:08X}, dlc={frame.DLC}, frame_type=0x{int(frame.FrameType):02X}, "
                f"status={self.status_text(dev) or 'unavailable'}"
            )
        return ret

    def receive(self, dev: int, max_count: int = 32, timeout_ms: int = 100) -> list[tuple[int, bytes]]:
        """接收 Windows CANFD 数据帧。"""
        if not hasattr(self.dll, "CANFD_Receive"):
            return []
        rx_buf = (CanFDMsg * max_count)()
        ret = int(
            self.dll.CANFD_Receive(
                ctypes.c_uint(dev),
                rx_buf,
                ctypes.c_uint(max_count),
                ctypes.c_int(timeout_ms),
            )
        )
        if ret <= 0:
            return []
        messages = []
        for index in range(min(ret, max_count)):
            frame = rx_buf[index]
            data_len = DLC2LEN[frame.DLC] if frame.DLC < len(DLC2LEN) else 64
            data = bytes(int(frame.Data[i]) & 0xFF for i in range(data_len))
            messages.append((int(frame.ID), data))
        return messages

    def status_text(self, dev: int) -> str:
        """读取发送失败时的总线状态，便于定位接线或波特率问题。"""
        if not hasattr(self.dll, "CAN_GetStatus"):
            return ""
        try:
            status = CanStatus()
            ret = int(self.dll.CAN_GetStatus(ctypes.c_uint(dev), ctypes.byref(status)))
            if ret != 0:
                return f"CAN_GetStatus ret={ret}"
            return (
                f"bus=0x{int(status.BusSatus):02X}, err=0x{int(status.ErrSatus):02X}, "
                f"txerr={int(status.TECounter)}, rxerr={int(status.RECounter)}"
            )
        except (OSError, ValueError):
            return ""
