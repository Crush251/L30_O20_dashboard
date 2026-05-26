from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .joint_config import clamp_joint_values, map_normalized_joints
from .o20_protocol import (
    O20_CONTROL_COUNT,
    O20_DEFAULT_DEVICE_ID,
    O20_FRAME_JOINT_COUNT,
    O20_REG_DEVICE_INFO,
    O20_REG_ERROR_STATUS,
    O20_REG_TARGET_POS,
    O20_REG_TARGET_VEL,
    O20_TARGET_VEL_RAW_MAX,
    o20_build_can_id,
    o20_encode_raw_int16_frame,
    o20_encode_target_positions,
    o20_encode_target_velocities,
    o20_map_normalized,
    o20_parse_device_info,
    o20_parse_error_status,
    o20_parse_int16_values,
)
from .protocol import (
    CH,
    DEVICE_ID,
    HOST_ID,
    PCMD_CONFIG,
    PCMD_JOINT_CTRL,
    PROTOCOL_PRIORITY,
    SCMD_DEVICE_INFO,
    SCMD_GLOBAL_DISABLE,
    SCMD_GLOBAL_ENABLE,
    SCMD_JOINT_POS,
    TRANSMIT_TIMEOUT_MS,
    build_can_id,
    encode_empty_payload,
    encode_joint_payload,
    len_to_dlc,
    parse_l30_device_info,
)
from .sequences import JOINT_COUNT
from .win_l30_can import WindowsL30Can


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
        ("NomPre", ctypes.c_ushort),
        ("NomTseg1", ctypes.c_ubyte),
        ("NomTseg2", ctypes.c_ubyte),
        ("NomSJW", ctypes.c_ubyte),
        ("DatPre", ctypes.c_ubyte),
        ("DatTseg1", ctypes.c_ubyte),
        ("DatTseg2", ctypes.c_ubyte),
        ("DatSJW", ctypes.c_ubyte),
        ("Config", ctypes.c_ubyte),
        ("Model", ctypes.c_ubyte),
        ("Cantype", ctypes.c_ubyte),
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


@dataclass
class DeviceState:
    """单个 CAN 设备的运行状态缓存。"""

    dev: int
    ch: int = CH
    opened: bool = False
    enabled: bool = False
    info: dict[str, str] = field(default_factory=dict)
    joints: list[int] = field(default_factory=lambda: [0] * JOINT_COUNT)


def _decode(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("utf-8", errors="replace")


class CanBusController:
    """统一封装 Linux so 和 Windows DLL 的 L30 CANFD 控制器。"""

    def __init__(self, lib_path: str | os.PathLike[str] | None = None):
        self.system = platform.system().lower()
        self.lib_path = Path(lib_path or os.environ.get("L30_CANBUS_LIB", self._default_library_path()))
        self.lock = threading.RLock()
        self.devices: dict[int, DeviceState] = {}
        self.mock = os.environ.get("L30_O20_DASHBOARD_MOCK", "").lower() in {"1", "true", "yes"}
        self.load_error = ""
        self.last_tx: list[dict] = []
        self.win: WindowsL30Can | None = None
        self.lib = None
        if not self.mock:
            if self.system == "windows":
                try:
                    self.win = WindowsL30Can(self.lib_path)
                except OSError as exc:
                    self.mock = True
                    self.load_error = str(exc)
            else:
                self.lib = self._load_library()

    def _default_library_path(self) -> Path:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            bundled_root = Path(sys._MEIPASS) / "libcanbus"
            if self.system == "windows":
                return bundled_root / "HCanbus.dll"
            return bundled_root / "libcanbus.so"

        package_root = Path(__file__).resolve().parent
        project_root = package_root.parents[1]
        workspace_root = package_root.parents[2]
        if self.system == "windows":
            candidates = [
                Path.cwd() / "HCanbus.dll",
                project_root / "HCanbus.dll",
                project_root / "libcanbus" / "HCanbus.dll",
                workspace_root / "HCanbus.dll",
                workspace_root / "libcanbus" / "HCanbus.dll",
                project_root / "HCanbus.dll",
                project_root / "libcanbus" / "HCanbus.dll",
                project_root / "libcanbus" / "libcanbus.dll",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return candidate
            return project_root / "HCanbus.dll"

        candidates = [
            project_root / "libcanbus" / "libcanbus.so",
            workspace_root / "libcanbus" / "libcanbus.so",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return project_root / "libcanbus" / "libcanbus.so"

    def _load_library(self):
        try:
            libusb_path = ctypes.util.find_library("usb-1.0")
            if libusb_path:
                ctypes.CDLL(libusb_path, mode=ctypes.RTLD_GLOBAL)
            lib = ctypes.CDLL(str(self.lib_path), mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            self.mock = True
            self.load_error = str(exc)
            return None

        lib.CAN_ScanDevice.restype = ctypes.c_int
        lib.CAN_ScanDevice.argtypes = []
        lib.CAN_OpenDevice.argtypes = [ctypes.c_uint, ctypes.c_uint]
        lib.CAN_CloseDevice.argtypes = [ctypes.c_uint, ctypes.c_uint]
        lib.CANFD_Init.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(CanFDConfig),
        ]
        lib.CANFD_Transmit.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(CanFDMsg),
            ctypes.c_uint,
            ctypes.c_int,
        ]
        if hasattr(lib, "CANFD_Receive"):
            lib.CANFD_Receive.argtypes = [
                ctypes.c_uint,
                ctypes.c_uint,
                ctypes.POINTER(CanFDMsg),
                ctypes.c_uint,
                ctypes.c_int,
            ]
            lib.CANFD_Receive.restype = ctypes.c_int
        lib.CAN_OpenDevice.restype = ctypes.c_int
        lib.CAN_CloseDevice.restype = ctypes.c_int
        lib.CAN_ReadDevInfo.argtypes = [ctypes.c_uint, ctypes.POINTER(DevInfo)]
        lib.CAN_ReadDevInfo.restype = ctypes.c_int
        lib.CANFD_Init.restype = ctypes.c_int
        lib.CANFD_Transmit.restype = ctypes.c_int
        return lib

    def scan(self, refresh: bool = True) -> dict:
        """扫描 USB-CANFD 设备并生成前端需要的状态 payload。

        Windows HCanbus.dll 在设备已打开后频繁 CAN_ScanDevice 可能扰动已打开句柄。
        状态轮询走 refresh=False，只返回缓存；用户手动扫描才重新访问硬件。
        """
        with self.lock:
            if refresh or not self.devices:
                if self.mock:
                    count = 2
                elif any(state.opened for state in self.devices.values()):
                    count = max(self.devices.keys(), default=-1) + 1
                elif self.win is not None:
                    count = self.win.scan()
                else:
                    count = max(0, self._scan_device_count())
                for dev in range(count):
                    self.devices.setdefault(dev, DeviceState(dev=dev))
            else:
                count = max(self.devices.keys(), default=-1) + 1
            return {
                "mock": self.mock,
                "load_error": self.load_error,
                "lib_path": str(self.lib_path),
                "count": count,
                "devices": [self._device_payload(self.devices[dev]) for dev in range(count)],
            }

    def open_devices(self, dev_ids: Iterable[int]) -> list[dict]:
        """打开指定设备，已打开的设备会直接复用。"""
        opened = []
        with self.lock:
            for dev in sorted({int(x) for x in dev_ids}):
                state = self.devices.setdefault(dev, DeviceState(dev=dev))
                if not state.opened:
                    self._open_one(state)
                opened.append(self._device_payload(state))
        return opened

    def close_all(self) -> None:
        """关闭所有已打开设备，并清空使能状态。"""
        with self.lock:
            if self.win is not None:
                self.win.close_all()
            for state in self.devices.values():
                if state.opened and not self.mock and self.win is None:
                    self._close_device(state.dev, state.ch)
                state.opened = False
                state.enabled = False

    def set_enabled(self, dev_ids: Iterable[int], enabled: bool) -> list[dict]:
        """向设备发送全局使能或失能命令。"""
        results = []
        scmd = SCMD_GLOBAL_ENABLE if enabled else SCMD_GLOBAL_DISABLE
        data = encode_empty_payload()
        with self.lock:
            for state in self._selected_open_devices(dev_ids):
                self._send(state, scmd, data, label="enable" if enabled else "disable")
                state.enabled = enabled
                results.append(self._device_payload(state))
        return results

    def set_joints(
        self,
        dev_ids: Iterable[int],
        positions: Iterable[int],
        label: str = "joint",
        normalized: bool = False,
        require_open: bool = False,
    ) -> list[dict]:
        """发送 17 个关节目标值；前端手动值可选择 normalized=True。"""
        values = map_normalized_joints(positions) if normalized else clamp_joint_values(positions)
        payload = encode_joint_payload(values)
        results = []
        with self.lock:
            states = (
                self._selected_existing_open_devices(dev_ids)
                if require_open
                else self._selected_open_devices(dev_ids)
            )
            for state in states:
                self._send(state, SCMD_JOINT_POS, payload, label=label)
                state.joints = values
                results.append(self._device_payload(state))
        return results

    def query_l30_device_info(self, dev_ids: Iterable[int], timeout_ms: int = 120) -> list[dict]:
        """按 L30 新协议读取 DeviceInFo(父命令0x3, 子命令0x02)。"""
        results = []
        read_id_cache: dict[int, int] = {}
        with self.lock:
            for state in self._selected_existing_open_devices(dev_ids):
                read_id = build_can_id(
                    PROTOCOL_PRIORITY, 0, PCMD_CONFIG, SCMD_DEVICE_INFO, DEVICE_ID, HOST_ID
                )
                read_id_cache[state.dev] = read_id
                self._send_can_frame(state, read_id, encode_empty_payload(), label="l30-info-read")
                messages = self._receive_canfd(state, timeout_ms=timeout_ms)
                self._record_rx_frames(state, messages, label="l30-info-rx")
                matched = None
                matched_can_id = None
                for can_id, data in messages:
                    rx_id = can_id & 0x1FFFFFFF
                    parent = (rx_id >> 21) & 0x0F
                    subcmd = (rx_id >> 13) & 0xFF
                    dst_id = (rx_id >> 8) & 0x1F
                    src_id = (rx_id >> 3) & 0x1F
                    if parent == PCMD_CONFIG and subcmd == SCMD_DEVICE_INFO and dst_id == HOST_ID and src_id == DEVICE_ID:
                        matched = data
                        matched_can_id = rx_id
                        break
                info = parse_l30_device_info(matched or b"") if matched is not None else {}
                if info:
                    state.info = {**state.info, **{key: str(value) for key, value in info.items()}}
                results.append(
                    {
                        "dev": state.dev,
                        "query_id": f"0x{read_id_cache[state.dev]:08X}",
                        "reply_id": f"0x{matched_can_id:08X}" if matched_can_id is not None else "",
                        "matched": matched is not None,
                        "info": info,
                        "rx_count": len(messages),
                    }
                )
        return results

    def send_raw_joint_payload(
        self, dev_ids: Iterable[int], payload: bytes, label: str = "raw-joint"
    ) -> list[dict]:
        """发送已经编码好的原始关节 payload，主要用于调试。"""
        if not 0 < len(payload) <= 64:
            raise ValueError(f"expected 1..64 payload bytes, got {len(payload)}")

        results = []
        with self.lock:
            for state in self._selected_open_devices(dev_ids):
                self._send(state, SCMD_JOINT_POS, payload, label=label)
                results.append(self._device_payload(state))
        return results

    def set_o20_joints(
        self,
        dev_ids: Iterable[int],
        positions: Iterable[int | float],
        device_id: int = O20_DEFAULT_DEVICE_ID,
        frame_type: int = 0x04,
        require_open: bool = False,
    ) -> list[dict]:
        """按 O20 寄存器协议发送 16DOF 目标位置。"""
        return self.set_o20_joints_by_device_ids(
            dev_ids,
            positions,
            device_ids=None,
            device_id=device_id,
            frame_type=frame_type,
            require_open=require_open,
        )

    def set_o20_joints_by_device_ids(
        self,
        dev_ids: Iterable[int],
        positions: Iterable[int | float],
        device_ids: dict[int, int] | None = None,
        device_id: int = O20_DEFAULT_DEVICE_ID,
        frame_type: int = 0x04,
        require_open: bool = False,
    ) -> list[dict]:
        """发送 O20 归一化目标位置；同一帧可按 DEV 使用不同左右手指令码。"""
        values = o20_map_normalized(positions)
        return self.set_o20_raw_joints_by_device_ids(
            dev_ids,
            values,
            device_ids=device_ids,
            device_id=device_id,
            frame_type=frame_type,
            label="o20-pos",
            require_open=require_open,
        )

    def set_o20_raw_joints(
        self,
        dev_ids: Iterable[int],
        positions: Iterable[int | float],
        device_id: int = O20_DEFAULT_DEVICE_ID,
        frame_type: int = 0x04,
        label: str = "o20-raw-pos",
        require_open: bool = False,
    ) -> list[dict]:
        """按 O20 寄存器协议发送原始目标位置，供 O20 dance/RPS 使用。"""
        return self.set_o20_raw_joints_by_device_ids(
            dev_ids,
            positions,
            device_ids=None,
            device_id=device_id,
            frame_type=frame_type,
            label=label,
            require_open=require_open,
        )

    def set_o20_raw_joints_by_device_ids(
        self,
        dev_ids: Iterable[int],
        positions: Iterable[int | float],
        device_ids: dict[int, int] | None = None,
        device_id: int = O20_DEFAULT_DEVICE_ID,
        frame_type: int = 0x04,
        label: str = "o20-raw-pos",
        require_open: bool = False,
    ) -> list[dict]:
        """同一帧内向多个 O20 设备发送原始目标位置，每个 DEV 可使用独立左右手指令码。"""
        raw_values = [int(round(float(value))) for value in positions]
        state_values = raw_values[:O20_CONTROL_COUNT]
        if len(state_values) < O20_CONTROL_COUNT:
            state_values = state_values + [0] * (O20_CONTROL_COUNT - len(state_values))
        payload = o20_encode_raw_int16_frame(state_values)
        state_values = state_values + [0]
        node_ids = {int(dev): int(node_id) for dev, node_id in (device_ids or {}).items()}
        results = []
        with self.lock:
            states = (
                self._selected_existing_open_devices(dev_ids)
                if require_open
                else self._selected_open_devices(dev_ids)
            )
            for state in states:
                state_device_id = node_ids.get(state.dev, int(device_id))
                can_id = o20_build_can_id(state_device_id, O20_REG_TARGET_POS, True)
                self._send_can_frame(state, can_id, payload, label=label, frame_type=frame_type)
                state.joints = state_values
                results.append(self._device_payload(state))
        self._schedule_o20_rx_probe(dev_ids, label=f"{label}-rx", timeout_ms=50)
        return results

    def set_o20_velocity(
        self,
        dev_ids: Iterable[int],
        velocity: int | float,
        device_id: int = O20_DEFAULT_DEVICE_ID,
        frame_type: int = 0x04,
        require_open: bool = False,
    ) -> list[dict]:
        """按 O20 寄存器协议发送统一目标速度。"""
        return self.set_o20_velocity_by_device_ids(
            dev_ids,
            velocity,
            device_ids=None,
            device_id=device_id,
            frame_type=frame_type,
            require_open=require_open,
        )

    def set_o20_velocity_by_device_ids(
        self,
        dev_ids: Iterable[int],
        velocity: int | float,
        device_ids: dict[int, int] | None = None,
        device_id: int = O20_DEFAULT_DEVICE_ID,
        frame_type: int = 0x04,
        require_open: bool = False,
    ) -> list[dict]:
        """按 O20 寄存器协议发送统一目标速度，每个 DEV 可使用独立左右手指令码。"""
        raw_velocity = max(0, min(O20_TARGET_VEL_RAW_MAX, int(round(float(velocity)))))
        payload = o20_encode_target_velocities(raw_velocity)
        node_ids = {int(dev): int(node_id) for dev, node_id in (device_ids or {}).items()}
        results = []
        with self.lock:
            states = (
                self._selected_existing_open_devices(dev_ids)
                if require_open
                else self._selected_open_devices(dev_ids)
            )
            for state in states:
                state_device_id = node_ids.get(state.dev, int(device_id))
                can_id = o20_build_can_id(state_device_id, O20_REG_TARGET_VEL, True)
                self._send_can_frame(state, can_id, payload, label="o20-vel", frame_type=frame_type)
                device_payload = self._device_payload(state)
                device_payload["o20_velocity_raw"] = raw_velocity
                results.append(device_payload)
        self._schedule_o20_rx_probe(dev_ids, label="o20-vel-rx", timeout_ms=50)
        return results

    def query_o20_error_status(
        self,
        dev_ids: Iterable[int],
        device_ids: dict[int, int] | None = None,
        device_id: int = O20_DEFAULT_DEVICE_ID,
        frame_type: int = 0x04,
        timeout_ms: int = 80,
    ) -> list[dict]:
        """读取 O20 SYS_ERROR_STATUS(0x02)。"""
        node_ids = {int(dev): int(node_id) for dev, node_id in (device_ids or {}).items()}
        results = []
        with self.lock:
            for state in self._selected_existing_open_devices(dev_ids):
                state_device_id = node_ids.get(state.dev, int(device_id))
                read_id = o20_build_can_id(state_device_id, O20_REG_ERROR_STATUS, False)
                self._send_can_frame(state, read_id, b"", label="o20-error-read", frame_type=frame_type)
                messages = self._receive_canfd(state, timeout_ms=timeout_ms)
                self._record_rx_frames(state, messages, label="o20-error-rx")
                matched = None
                matched_can_id = None
                for can_id, data in messages:
                    can_id = can_id & 0x1FFFFFFF
                    rx_device_id = (can_id >> 21) & 0xFF
                    rx_register = (can_id >> 13) & 0xFF
                    if rx_device_id == state_device_id and rx_register == O20_REG_ERROR_STATUS:
                        matched = data
                        matched_can_id = can_id
                        break
                results.append(
                    {
                        "dev": state.dev,
                        "device_id": state_device_id,
                        "query_id": f"0x{read_id:08X}",
                        "reply_id": f"0x{matched_can_id:08X}" if matched_can_id is not None else "",
                        "matched": matched is not None,
                        "errors": o20_parse_error_status(matched or b"") if matched is not None else [],
                        "rx_count": len(messages),
                    }
                )
        return results

    def clear_o20_error_status(
        self,
        dev_ids: Iterable[int],
        device_ids: dict[int, int] | None = None,
        device_id: int = O20_DEFAULT_DEVICE_ID,
        frame_type: int = 0x04,
    ) -> list[dict]:
        """写 0 清除 O20 SYS_ERROR_STATUS(0x02)。"""
        node_ids = {int(dev): int(node_id) for dev, node_id in (device_ids or {}).items()}
        payload = bytes([0] * O20_FRAME_JOINT_COUNT)
        results = []
        with self.lock:
            for state in self._selected_existing_open_devices(dev_ids):
                state_device_id = node_ids.get(state.dev, int(device_id))
                can_id = o20_build_can_id(state_device_id, O20_REG_ERROR_STATUS, True)
                self._send_can_frame(state, can_id, payload, label="o20-error-clear", frame_type=frame_type)
                results.append(self._device_payload(state))
        self._schedule_o20_rx_probe(dev_ids, label="o20-error-clear-rx", timeout_ms=50)
        return results

    def query_o20_device_info(
        self,
        dev_ids: Iterable[int],
        device_id: int = O20_DEFAULT_DEVICE_ID,
        frame_type: int = 0x04,
        timeout_ms: int = 120,
    ) -> list[dict]:
        """读取 O20 SYS_DEVICE_INFO，用于区分左右手和设备型号。"""
        probe_ids = [1, 2] if int(device_id) == 0 else [int(device_id)]
        results = []
        with self.lock:
            for state in self._selected_existing_open_devices(dev_ids):
                for probe_id in probe_ids:
                    read_id = o20_build_can_id(probe_id, O20_REG_DEVICE_INFO, False)
                    self._send_can_frame(state, read_id, b"", label="o20-info-read", frame_type=frame_type)
                    messages = self._receive_canfd(state, timeout_ms=timeout_ms)
                    self._record_rx_frames(state, messages, label="o20-info-rx")
                    matched = None
                    matched_can_id = None
                    for can_id, data in messages:
                        can_id = can_id & 0x1FFFFFFF
                        rx_device_id = (can_id >> 21) & 0xFF
                        rx_register = (can_id >> 13) & 0xFF
                        if (
                            rx_device_id == probe_id
                            and rx_register == O20_REG_DEVICE_INFO
                            and len(data) >= 51
                        ):
                            matched = data
                            matched_can_id = can_id
                            break
                    response_device_id = ((matched_can_id or 0) >> 21) & 0xFF
                    results.append(
                        {
                            "dev": state.dev,
                            "query_device_id": probe_id,
                            "device_id": response_device_id or probe_id,
                            "query_id": f"0x{read_id:08X}",
                            "reply_id": f"0x{matched_can_id:08X}" if matched_can_id is not None else "",
                            "can_id": f"0x{read_id:08X}",
                            "matched": matched is not None,
                            "info": o20_parse_device_info(matched or b"") if matched is not None else {},
                            "rx_count": len(messages),
                            "rx_ids": [f"0x{can_id & 0x1FFFFFFF:08X}" for can_id, _data in messages],
                        }
                    )
        return results

    def run_sequence(self, dev_ids: Iterable[int], sequence: Iterable[dict]) -> list[dict]:
        """按顺序执行内置手势序列。"""
        sent = []
        for step in sequence:
            positions = step.get("joints", [0] * JOINT_COUNT)
            hold_ms = int(step.get("hold_ms", 180))
            sent = self.set_joints(dev_ids, positions)
            time.sleep(max(0, hold_ms) / 1000)
        return sent

    def run_o20_sequence(
        self,
        dev_ids: Iterable[int],
        sequence: Iterable[dict],
        device_id: int = O20_DEFAULT_DEVICE_ID,
        device_ids: dict[int, int] | None = None,
        frame_type: int = 0x04,
    ) -> list[dict]:
        """按帧执行 O20 原始关节序列，保证多设备在同一帧发送后再等待。"""
        sent = []
        for step in sequence:
            positions = step.get("joints", [0] * O20_CONTROL_COUNT)
            hold_ms = int(step.get("hold_ms", 180))
            sent = self.set_o20_raw_joints_by_device_ids(
                dev_ids,
                positions,
                device_ids=device_ids,
                device_id=device_id,
                frame_type=frame_type,
                label="o20-sequence",
                require_open=True,
            )
            time.sleep(max(0, hold_ms) / 1000)
        return sent

    def status(self) -> dict:
        """返回扫描状态和最近发送记录。"""
        scan = self.scan(refresh=False)
        scan["last_tx"] = self.last_tx[-20:]
        return scan

    def explain_error(self, message: str) -> str:
        """把底层 DLL/so 错误转换成更适合界面展示的中文提示。"""
        if "CAN_OpenDevice" in message and "failed: -1" in message:
            command = ".\\run_windows.bat" if self.system == "windows" else "./run_sudo.sh"
            return (
                f"{message}. 设备已被扫描到，但打开失败。请检查 USB-CANFD 是否被其他程序占用、"
                "USB 线和供电是否稳定。"
                f"请在 l30_o20_dashboard 目录执行 {command}。"
            )
        if "cannot open shared object file" in message or "could not find module" in message.lower():
            return (
                f"{message}. 请把 Windows 的 HCanbus.dll 放到 L30_06 或 L30_06\\l30_o20_dashboard，"
                "或者设置 L30_CANBUS_LIB 指向 DLL 完整路径。"
            )
        if "access violation" in message.lower():
            return (
                f"{message}. Windows HCanbus.dll 调用发生访问冲突，通常是 DLL 位数、"
                "驱动版本或初始化调用顺序不匹配。请确认 Python、HCanbus.dll、驱动均为同一位数，"
                "并使用项目内 libcanbus\\HCanbus.dll。"
            )
        if "CANFD_Init" in message:
            return (
                f"{message}. 设备已打开但 CANFD 初始化失败，请确认设备支持 ISO CANFD、"
                "标称 1M / 数据 5M 波特率与总线一致。"
            )
        if "CANFD_Transmit" in message:
            return (
                f"{message}. CANFD 发送失败。请检查设备是否已使能供电、CANH/CANL 接线、"
                "终端电阻、标称 1M / 数据 5M 波特率，以及是否有其他程序占用同一个设备。"
            )
        return message

    def _open_one(self, state: DeviceState) -> None:
        if self.system == "windows":
            self._open_one_windows(state)
            return

        if not self.mock:
            ret = self._open_device_with_retry(state.dev, state.ch)
            if ret != 0:
                raise RuntimeError(f"CAN_OpenDevice dev={state.dev} failed: {ret}")

            config = CanFDConfig()
            config.Model = 0
            config.NomBaud = 1_000_000
            config.DatBaud = 5_000_000
            config.Config = 0x0001 | 0x0002 | 0x0004
            config.Cantype = 1
            ret = self._init_canfd(state.dev, state.ch, config)
            if ret != 0:
                self._close_device(state.dev, state.ch)
                raise RuntimeError(f"CANFD_Init dev={state.dev} failed: {ret}")
            state.info = self._read_device_info(state.dev)
        else:
            state.info = {
                "type": "MOCK-L30-CANFD",
                "serial": f"MOCK-{state.dev:02d}",
                "hardware": "mock",
                "firmware": "mock",
                "date": "",
            }
        state.opened = True

    def _open_one_windows(self, state: DeviceState) -> None:
        if self.mock:
            state.info = {
                "type": "MOCK-L30-CANFD",
                "serial": f"MOCK-{state.dev:02d}",
                "hardware": "mock",
                "firmware": "mock",
                "date": "",
            }
            state.opened = True
            return

        if self.win is None:
            raise RuntimeError("Windows CAN adapter is not loaded")
        state.info = self.win.open(state.dev)
        state.opened = True

    def _read_device_info(self, dev: int) -> dict[str, str]:
        if not hasattr(self.lib, "CAN_ReadDevInfo"):
            return {}
        try:
            info = DevInfo()
            ret = int(self.lib.CAN_ReadDevInfo(dev, ctypes.byref(info)))
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

    def _scan_device_count(self) -> int:
        return int(self.lib.CAN_ScanDevice())

    def _windows_device_type(self, dev: int) -> int | None:
        return None

    def _open_device_with_retry(self, dev: int, ch: int) -> int:
        return self._open_device(dev, ch)

    def _open_device(self, dev: int, ch: int) -> int:
        return int(self.lib.CAN_OpenDevice(dev, ch))

    def _close_device(self, dev: int, ch: int) -> int:
        return int(self.lib.CAN_CloseDevice(dev, ch))

    def _init_canfd(self, dev: int, ch: int, config: CanFDConfig) -> int:
        return int(self.lib.CANFD_Init(dev, ch, ctypes.byref(config)))

    def _send(self, state: DeviceState, scmd: int, data: bytes, label: str) -> int:
        can_id = build_can_id(PROTOCOL_PRIORITY, 1, PCMD_JOINT_CTRL, scmd, DEVICE_ID, HOST_ID)
        return self._send_can_frame(state, can_id, data, label)

    def _send_can_frame(
        self,
        state: DeviceState,
        can_id: int,
        data: bytes,
        label: str,
        frame_type: int | None = None,
    ) -> int:
        effective_frame_type = frame_type if frame_type is not None else (0x04 if self.system == "windows" else 0x0C)
        frame = {
            "dev": state.dev,
            "id": f"0x{can_id:08X}",
            "label": label,
            "data": data.hex(" ").upper(),
            "dlc": len_to_dlc(len(data)),
            "frame_type": f"0x{effective_frame_type:02X}",
            "extern_flag": 1,
            "direction": "TX",
        }
        if label.startswith(("o20", "l30")):
            print(
                f"TX DEV{state.dev} {label} 0x{can_id:08X} "
                f"FrameType=0x{int(effective_frame_type):02X} DLC={frame['dlc']} "
                f"data={data.hex(' ').upper()}",
                flush=True,
            )
        if self.mock:
            frame["ret"] = 1
            self.last_tx.append(frame)
            return 1

        if self.win is not None:
            ret = self.win.send(state.dev, can_id, data)
            frame["ret"] = ret
            frame["status"] = self.win.status_text(state.dev)
            self.last_tx.append(frame)
            return ret

        msg = CanFDMsg()
        msg.ID = can_id & 0x1FFFFFFF
        msg.DLC = len_to_dlc(len(data))
        # L30 Linux 默认使用 0x0C；O20 可从页面选择 0x04/0x0C。
        msg.FrameType = effective_frame_type
        msg.ExternFlag = 1
        msg.RemoteFlag = 0
        for index, value in enumerate(data):
            msg.Data[index] = value
        ret = self._transmit_canfd(state, msg)
        frame["ret"] = ret
        self.last_tx.append(frame)
        if self._transmit_failed(ret):
            raise RuntimeError(
                f"CANFD_Transmit dev={state.dev} failed: ret={ret}, "
                f"id=0x{can_id:08X}, dlc={msg.DLC}, frame_type=0x{int(msg.FrameType):02X}, "
                f"status={frame.get('status') or 'unavailable'}"
            )
        return ret

    def _transmit_canfd(self, state: DeviceState, msg: CanFDMsg) -> int:
        return int(
            self.lib.CANFD_Transmit(
                state.dev, state.ch, ctypes.byref(msg), 1, TRANSMIT_TIMEOUT_MS
            )
        )

    def _receive_canfd(self, state: DeviceState, max_count: int = 32, timeout_ms: int = 100) -> list[tuple[int, bytes]]:
        if self.mock:
            return []
        if self.win is not None:
            return self.win.receive(state.dev, max_count=max_count, timeout_ms=timeout_ms)
        if not hasattr(self.lib, "CANFD_Receive"):
            return []
        rx_buf = (CanFDMsg * max_count)()
        ret = int(
            self.lib.CANFD_Receive(
                state.dev,
                state.ch,
                rx_buf,
                max_count,
                timeout_ms,
            )
        )
        if ret <= 0:
            return []
        messages = []
        dlc2len = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]
        for index in range(min(ret, max_count)):
            frame = rx_buf[index]
            data_len = dlc2len[frame.DLC] if frame.DLC < len(dlc2len) else 64
            data = bytes(int(frame.Data[i]) & 0xFF for i in range(data_len))
            messages.append((int(frame.ID), data))
        return messages

    def _record_rx_frames(self, state: DeviceState, messages: list[tuple[int, bytes]], label: str) -> None:
        if label.startswith(("o20", "l30")):
            print(f"RX-PROBE DEV{state.dev} {label} count={len(messages)}", flush=True)
        for can_id, data in messages:
            rx_id = can_id & 0x1FFFFFFF
            register = (rx_id >> 13) & 0xFF
            if label.startswith(("o20", "l30")):
                print(
                    f"RX DEV{state.dev} {label} 0x{rx_id:08X} "
                    f"reg=0x{register:02X} len={len(data)} data={data.hex(' ').upper()}",
                    flush=True,
                )
            if label.startswith("l30"):
                parsed: dict[str, object] = {
                    "pcmd": f"0x{(rx_id >> 21) & 0x0F:02X}",
                    "scmd": f"0x{register:02X}",
                    "dst_id": (rx_id >> 8) & 0x1F,
                    "src_id": (rx_id >> 3) & 0x1F,
                }
                if label == "l30-info-rx":
                    info = parse_l30_device_info(data)
                    if info:
                        parsed["device_info"] = info
            else:
                parsed = {
                    "device_id": (rx_id >> 21) & 0xFF,
                    "register": f"0x{register:02X}",
                }
                if register == O20_REG_ERROR_STATUS:
                    parsed["errors"] = o20_parse_error_status(data)
                elif register in {0x05, O20_REG_TARGET_POS, O20_REG_TARGET_VEL}:
                    parsed["values"] = o20_parse_int16_values(data)
            self.last_tx.append(
                {
                    "dev": state.dev,
                    "id": f"0x{rx_id:08X}",
                    "label": label,
                    "data": data.hex(" ").upper(),
                    "dlc": len_to_dlc(len(data)),
                    "frame_type": "RX",
                    "extern_flag": 1,
                    "direction": "RX",
                    "ret": len(data),
                    "parsed": parsed,
                }
            )

    def _schedule_o20_rx_probe(self, dev_ids: Iterable[int], label: str, timeout_ms: int = 10) -> None:
        """后台短收 O20 写入后的设备返回，避免阻塞写接口。"""
        selected = sorted({int(dev) for dev in dev_ids})
        if not selected or self.mock:
            return

        def worker() -> None:
            time.sleep(0.005)
            try:
                with self.lock:
                    for state in self._selected_existing_open_devices(selected):
                        messages = self._receive_canfd(state, timeout_ms=timeout_ms)
                        self._record_rx_frames(state, messages, label=label)
            except Exception as exc:
                self.last_tx.append(
                    {
                        "dev": -1,
                        "id": "",
                        "label": label,
                        "data": str(exc),
                        "dlc": 0,
                        "frame_type": "RX",
                        "extern_flag": 1,
                        "direction": "RX",
                        "ret": 0,
                    }
                )

        threading.Thread(target=worker, name="o20-rx-probe", daemon=True).start()

    def _transmit_failed(self, ret: int) -> bool:
        return ret <= 0

    def _status_text(self, dev: int) -> str:
        return ""

    def _selected_open_devices(self, dev_ids: Iterable[int]) -> list[DeviceState]:
        states = []
        for dev in sorted({int(x) for x in dev_ids}):
            state = self.devices.setdefault(dev, DeviceState(dev=dev))
            if not state.opened:
                self._open_one(state)
            states.append(state)
        return states

    def _selected_existing_open_devices(self, dev_ids: Iterable[int]) -> list[DeviceState]:
        """只返回已经打开的设备，禁止调用方触发隐式 open。"""
        states = []
        for dev in sorted({int(x) for x in dev_ids}):
            state = self.devices.get(dev)
            if state is None or not state.opened:
                raise RuntimeError(f"dev={dev} is not opened; please connect it first")
            states.append(state)
        return states

    def _device_payload(self, state: DeviceState) -> dict:
        return {
            "dev": state.dev,
            "ch": state.ch,
            "opened": state.opened,
            "enabled": state.enabled,
            "info": state.info,
            "joints": state.joints,
        }
