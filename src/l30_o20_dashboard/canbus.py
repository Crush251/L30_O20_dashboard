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
    O20_FRAME_TYPE,
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
    PCMD_TACTILE_SENSOR,
    PROTOCOL_PRIORITY,
    SCMD_DEVICE_INFO,
    SCMD_GLOBAL_DISABLE,
    SCMD_GLOBAL_ENABLE,
    SCMD_JOINT_POS,
    SCMD_PRODUCT_CODE,
    TRANSMIT_TIMEOUT_MS,
    build_can_id,
    encode_empty_payload,
    encode_joint_payload,
    l30_status_text,
    len_to_dlc,
    parse_can_id,
    parse_l30_device_info,
    parse_l30_product_code,
    parse_l30_status,
)
from .sensor_protocol import (
    FINGER_SENSOR_SPECS,
    make_mock_tactile_values,
    parse_l30_tactile_frames,
    parse_o20_tactile_block,
    tactile_summary,
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
    info: dict[str, object] = field(default_factory=dict)
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

    def _linux_library_names(self) -> list[str]:
        """Return Linux CAN library names in the order preferred for this CPU."""
        machine = platform.machine().lower()
        if machine in {"aarch64", "arm64"}:
            return ["libcanbus_arm64.so", "libcanbus.so"]
        return ["libcanbus.so", "libcanbus_arm64.so"]

    def _default_library_path(self) -> Path:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            bundled_root = Path(sys._MEIPASS) / "libcanbus"
            if self.system == "windows":
                return bundled_root / "HCanbus.dll"
            for name in self._linux_library_names():
                candidate = bundled_root / name
                if candidate.exists():
                    return candidate
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
            root / "libcanbus" / name
            for root in (project_root, workspace_root)
            for name in self._linux_library_names()
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

    def open_devices(self, dev_ids: Iterable[int], force: bool = False) -> list[dict]:
        """打开指定设备，已打开的设备会直接复用；force=True 时显式尝试接管。"""
        opened = []
        with self.lock:
            for dev in sorted({int(x) for x in dev_ids}):
                state = self.devices.setdefault(dev, DeviceState(dev=dev))
                if state.opened:
                    opened.append(self._device_payload(state))
                    continue
                try:
                    self._open_one(state)
                except Exception as exc:
                    if not force or not self._can_force_takeover(exc):
                        raise
                    self._force_release_device(state)
                    try:
                        self._open_one(state)
                    except Exception as retry_exc:
                        raise RuntimeError(
                            f"force open dev={state.dev} failed after close/reopen: {retry_exc}"
                        ) from retry_exc
                opened.append(self._device_payload(state))
        return opened

    def _can_force_takeover(self, exc: Exception) -> bool:
        """判断打开失败是否属于可尝试强制接管的设备占用类错误。"""
        message = str(exc).lower()
        return "can_opendevice" in message or "busy" in message or "occupied" in message

    def _force_release_device(self, state: DeviceState) -> None:
        """显式尝试释放设备句柄；跨进程是否生效取决于厂商驱动。"""
        state.opened = False
        state.enabled = False
        if self.mock:
            return
        try:
            if self.win is not None:
                self.win.close(state.dev)
            else:
                self._close_device(state.dev, state.ch)
        finally:
            time.sleep(0.12)

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
        """向设备发送全局使能或失能命令，并校验 L30 应答状态码。"""
        results = []
        scmd = SCMD_GLOBAL_ENABLE if enabled else SCMD_GLOBAL_DISABLE
        label = "l30-enable" if enabled else "l30-disable"
        data = encode_empty_payload()
        with self.lock:
            for state in self._selected_existing_open_devices(dev_ids):
                ack = self._send_l30_command_with_ack(state, PCMD_JOINT_CTRL, scmd, data, label)
                if not ack.get("matched"):
                    info_result = self._discover_l30_device_info(state, timeout_ms=50)
                    if info_result.get("matched"):
                        ack = self._send_l30_command_with_ack(
                            state, PCMD_JOINT_CTRL, scmd, data, label
                        )
                status = ack.get("status")
                if status != 0:
                    status_text = ack.get("status_text") or l30_status_text(
                        int(status) if status is not None else None
                    )
                    raise RuntimeError(
                        f"L30 {label} dev={state.dev} failed: status={status_text}, "
                        f"rx_count={ack.get('rx_count', 0)}"
                    )
                state.enabled = enabled
                payload = self._device_payload(state)
                payload["ack"] = ack
                results.append(payload)
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

    def query_l30_device_info(self, dev_ids: Iterable[int], timeout_ms: int = 50) -> list[dict]:
        """按 L30 新协议读取 DeviceInFo，并自动探测设备当前 NodeID。"""
        results = []
        with self.lock:
            for state in self._selected_existing_open_devices(dev_ids):
                results.append(self._discover_l30_device_info(state, timeout_ms=timeout_ms))
        return results

    def query_tactile_sensors(
        self,
        dev_ids: Iterable[int],
        profiles: dict[int, object] | None = None,
        timeout_ms: int = 120,
    ) -> list[dict]:
        """主动读取已连接设备的触觉传感器数据，并按统一结构返回。"""
        profile_map = {int(dev): profile for dev, profile in (profiles or {}).items()}
        results = []
        with self.lock:
            for state in self._selected_existing_open_devices(dev_ids):
                model, device_id = self._sensor_model_for_state(state, profile_map.get(state.dev))
                if model == "l30":
                    results.append(self._query_l30_tactile_sensors(state, timeout_ms=timeout_ms))
                elif model == "o20":
                    results.append(
                        self._query_o20_tactile_sensors(
                            state,
                            device_id=device_id or O20_DEFAULT_DEVICE_ID,
                            timeout_ms=timeout_ms,
                        )
                    )
                else:
                    results.append(
                        {
                            "dev": state.dev,
                            "model": "unknown",
                            "supported": False,
                            "message": "设备型号未知，请先执行设备查询或在传感器页手动选择型号",
                            "fingers": [],
                            "summary": {"online_fingers": 0, "max": 0, "avg": 0},
                            "updated_at": time.time(),
                        }
                    )
        return results

    def _sensor_profile_value(self, profile: object, name: str, default: object = None) -> object:
        """兼容 Pydantic 模型和普通 dict 的 profile 读取。"""
        if profile is None:
            return default
        if isinstance(profile, dict):
            return profile.get(name, default)
        return getattr(profile, name, default)

    def _sensor_model_for_state(
        self, state: DeviceState, profile: object | None
    ) -> tuple[str, int | None]:
        """根据前端 profile 和后端已缓存信息判断设备型号。"""
        profile_model = str(self._sensor_profile_value(profile, "model", "") or "").lower()
        profile_device_id = self._sensor_profile_value(profile, "device_id", None)
        try:
            device_id = int(profile_device_id) if profile_device_id is not None else None
        except (TypeError, ValueError):
            device_id = None

        if profile_model in {"l30", "o20"}:
            return profile_model, device_id

        info = state.info or {}
        product = str(info.get("product") or info.get("model") or "").lower()
        if product == "l30" or info.get("product_id") == 0x13:
            return "l30", None
        if product == "o20" or info.get("o20_info"):
            try:
                return "o20", int(info.get("o20_device_id") or O20_DEFAULT_DEVICE_ID)
            except (TypeError, ValueError):
                return "o20", O20_DEFAULT_DEVICE_ID
        return "unknown", device_id

    def _query_o20_tactile_sensors(
        self,
        state: DeviceState,
        device_id: int,
        timeout_ms: int = 120,
    ) -> dict:
        """按 O20 0x09~0x12 主动读取五指触觉数据。"""
        if self.mock:
            return self._mock_tactile_result(state, model="o20", device_id=device_id)

        fingers = []
        for finger_index, spec in enumerate(FINGER_SENSOR_SPECS):
            data1, rx1 = self._read_o20_tactile_register(
                state, device_id, spec.o20_data1_reg, 64, timeout_ms
            )
            data2, rx2 = self._read_o20_tactile_register(
                state, device_id, spec.o20_data2_reg, 9, timeout_ms
            )
            parsed = parse_o20_tactile_block(data1 or b"", data2 or b"")
            fingers.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "online": bool(parsed["online"]),
                    "values": parsed["values"],
                    "rows": parsed["rows"],
                    "cols": parsed["cols"],
                    "max": parsed["max"],
                    "avg": parsed["avg"],
                    "rx_count": rx1 + rx2,
                    "query_registers": [
                        f"0x{spec.o20_data1_reg:02X}",
                        f"0x{spec.o20_data2_reg:02X}",
                    ],
                    "error": (
                        ""
                        if data1 is not None and data2 is not None
                        else "未收到完整触觉寄存器回传"
                    ),
                    "order": finger_index,
                }
            )
        return {
            "dev": state.dev,
            "model": "o20",
            "device_id": device_id,
            "supported": True,
            "fingers": fingers,
            "summary": tactile_summary(fingers),
            "updated_at": time.time(),
        }

    def _read_o20_tactile_register(
        self,
        state: DeviceState,
        device_id: int,
        register_addr: int,
        expected_length: int,
        timeout_ms: int,
    ) -> tuple[bytes | None, int]:
        """读取 O20 单个触觉寄存器，并裁剪 CANFD DLC 填充字节。"""
        read_id = o20_build_can_id(device_id, register_addr, False)
        self._send_can_frame(
            state,
            read_id,
            b"",
            label="sensor-o20-read",
            frame_type=O20_FRAME_TYPE,
        )
        messages = self._receive_canfd(state, timeout_ms=timeout_ms)
        self._record_rx_frames(state, messages, label="sensor-o20-rx")
        for can_id, data in messages:
            rx_id = can_id & 0x1FFFFFFF
            rx_device_id = (rx_id >> 21) & 0xFF
            rx_register = (rx_id >> 13) & 0xFF
            if rx_device_id == int(device_id) and rx_register == int(register_addr):
                return bytes(data[:expected_length]), len(messages)
        return None, len(messages)

    def _query_l30_tactile_sensors(self, state: DeviceState, timeout_ms: int = 120) -> dict:
        """按 L30 v2 父命令 0x2 主动读取五指 12x6 触觉矩阵。"""
        if self.mock:
            return self._mock_tactile_result(state, model="l30", node_id=self._l30_node_id(state))

        node_id = self._l30_node_id(state)
        fingers = []
        for finger_index, spec in enumerate(FINGER_SENSOR_SPECS):
            frames, rx_count, query_id = self._read_l30_tactile_finger(
                state, node_id, spec.l30_scmd, timeout_ms
            )
            parsed = parse_l30_tactile_frames(frames)
            fingers.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "online": bool(parsed["online"]),
                    "values": parsed["values"],
                    "rows": parsed["rows"],
                    "cols": parsed["cols"],
                    "max": parsed["max"],
                    "avg": parsed["avg"],
                    "status": parsed.get("status"),
                    "complete": parsed.get("complete"),
                    "received_frames": parsed.get("received_frames"),
                    "expected_frames": parsed.get("expected_frames"),
                    "rx_count": rx_count,
                    "query_id": query_id,
                    "error": "" if parsed.get("online") else "未收到完整 L30 触觉多帧回传",
                    "order": finger_index,
                }
            )
        return {
            "dev": state.dev,
            "model": "l30",
            "node_id": node_id,
            "supported": True,
            "fingers": fingers,
            "summary": tactile_summary(fingers),
            "updated_at": time.time(),
        }

    def _read_l30_tactile_finger(
        self,
        state: DeviceState,
        node_id: int,
        scmd: int,
        timeout_ms: int,
    ) -> tuple[list[bytes], int, str]:
        """读取 L30 单指触觉多帧，并在拼包完成前不发起下一指请求。"""
        read_id = self._build_l30_can_id(
            state, 0, PCMD_TACTILE_SENSOR, scmd, dst_id=node_id
        )
        self._send_can_frame(state, read_id, encode_empty_payload(), label="sensor-l30-read")
        deadline = time.monotonic() + max(0.01, timeout_ms / 1000)
        messages: list[tuple[int, bytes]] = []
        matched_frames: list[bytes] = []
        while time.monotonic() < deadline:
            remaining_ms = max(1, min(20, int((deadline - time.monotonic()) * 1000)))
            batch = self._receive_canfd(state, timeout_ms=remaining_ms)
            if batch:
                messages.extend(batch)
                matched_frames = self._matched_l30_tactile_frames(messages, node_id, scmd)
                parsed = parse_l30_tactile_frames(matched_frames)
                if parsed.get("complete"):
                    break
            else:
                time.sleep(0.001)
        self._record_rx_frames(state, messages, label="sensor-l30-rx")
        return matched_frames, len(messages), f"0x{read_id:08X}"

    def _matched_l30_tactile_frames(
        self,
        messages: Iterable[tuple[int, bytes]],
        node_id: int,
        scmd: int,
    ) -> list[bytes]:
        """从 RX 批次中过滤当前 L30 触觉请求的应答帧。"""
        frames = []
        for can_id, data in messages:
            rx_id = can_id & 0x1FFFFFFF
            fields = parse_can_id(rx_id)
            if (
                fields["access"] == 0
                and fields["pcmd"] == PCMD_TACTILE_SENSOR
                and fields["scmd"] == scmd
                and fields["dst_id"] == HOST_ID
                and fields["src_id"] == node_id
            ):
                frames.append(data)
        return frames

    def _mock_tactile_result(
        self,
        state: DeviceState,
        model: str,
        device_id: int | None = None,
        node_id: int | None = None,
    ) -> dict:
        """生成统一 mock 触觉数据，供无硬件演示传感器页面。"""
        fingers = []
        for index, spec in enumerate(FINGER_SENSOR_SPECS):
            values = make_mock_tactile_values(state.dev * 11 + index)
            fingers.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "online": True,
                    "values": values,
                    "rows": 12,
                    "cols": 6,
                    "max": max(values),
                    "avg": round(sum(values) / len(values), 1),
                    "order": index,
                    "rx_count": 0,
                    "error": "",
                }
            )
        result = {
            "dev": state.dev,
            "model": model,
            "supported": True,
            "fingers": fingers,
            "summary": tactile_summary(fingers),
            "updated_at": time.time(),
            "mock": True,
        }
        if device_id is not None:
            result["device_id"] = int(device_id)
        if node_id is not None:
            result["node_id"] = int(node_id)
        return result

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
        probe_after_write: bool = True,
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
        if probe_after_write:
            self._schedule_o20_rx_probe(dev_ids, label=f"{label}-rx", timeout_ms=50)
        return results

    def drain_rx_buffers(
        self,
        dev_ids: Iterable[int],
        max_rounds: int = 4,
        max_count: int = 64,
        timeout_ms: int = 1,
    ) -> dict[int, int]:
        """主动清理已连接设备的 RX 缓冲，供长时间 dance 执行时防止回传堆积。"""
        drained: dict[int, int] = {}
        with self.lock:
            for state in self._selected_existing_open_devices(dev_ids):
                total = 0
                for _round in range(max(1, int(max_rounds))):
                    messages = self._receive_canfd(
                        state,
                        max_count=max(1, int(max_count)),
                        timeout_ms=max(0, int(timeout_ms)),
                    )
                    if not messages:
                        break
                    total += len(messages)
                drained[state.dev] = total
        return drained

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
                    parsed_info = o20_parse_device_info(matched or b"") if matched is not None else {}
                    if matched is not None and parsed_info:
                        state.info = {
                            **state.info,
                            "product": "O20",
                            "model": "O20",
                            "o20_device_id": response_device_id or probe_id,
                            "o20_info": parsed_info,
                        }
                    results.append(
                        {
                            "dev": state.dev,
                            "query_device_id": probe_id,
                            "device_id": response_device_id or probe_id,
                            "query_id": f"0x{read_id:08X}",
                            "reply_id": f"0x{matched_can_id:08X}" if matched_can_id is not None else "",
                            "can_id": f"0x{read_id:08X}",
                            "matched": matched is not None,
                            "info": parsed_info,
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
            sent = self.set_joints(dev_ids, positions, require_open=True)
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
        if "is not opened" in message:
            return f"{message}. 请先在设备区勾选并连接该 CANFD 设备，发送路径不会自动打开设备。"
        if "force open" in message:
            return f"{message}. 已尝试强制接管，但驱动仍拒绝打开；请确认其他程序是否仍在占用该 USB-CANFD。"
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
        can_id = self._build_l30_can_id(state, 1, PCMD_JOINT_CTRL, scmd)
        return self._send_can_frame(state, can_id, data, label)

    def _build_l30_can_id(
        self, state: DeviceState, access: int, pcmd: int, scmd: int, dst_id: int | None = None
    ) -> int:
        """按设备缓存 NodeID 生成 L30 CAN ID。"""
        target_id = self._l30_node_id(state) if dst_id is None else int(dst_id)
        return build_can_id(PROTOCOL_PRIORITY, access, pcmd, scmd, target_id, HOST_ID)

    def _l30_node_id(self, state: DeviceState) -> int:
        """读取该 USB-CAN 设备下当前 L30 NodeID，未知时默认设备 1。"""
        for key in ("node_id", "l30_node_id"):
            value = state.info.get(key)
            try:
                node_id = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= node_id <= 31:
                return node_id
        return DEVICE_ID

    def _l30_probe_node_ids(self, state: DeviceState) -> list[int]:
        """优先探测已知 NodeID，再扫 1~31，兼容设备被改 ID 的情况。"""
        candidates = [self._l30_node_id(state), DEVICE_ID, *range(1, 32)]
        seen: set[int] = set()
        ordered = []
        for node_id in candidates:
            if 1 <= int(node_id) <= 31 and int(node_id) not in seen:
                seen.add(int(node_id))
                ordered.append(int(node_id))
        return ordered

    def _send_l30_command_with_ack(
        self,
        state: DeviceState,
        pcmd: int,
        scmd: int,
        data: bytes,
        label: str,
        timeout_ms: int = 100,
    ) -> dict[str, object]:
        """发送 L30 有应答命令，并返回匹配应答里的状态码。"""
        request_access = 1
        dst_id = self._l30_node_id(state)
        can_id = self._build_l30_can_id(state, request_access, pcmd, scmd, dst_id=dst_id)
        self._send_can_frame(state, can_id, data, label=label)
        if self.mock:
            return {
                "matched": True,
                "status": 0,
                "status_text": l30_status_text(0),
                "rx_count": 0,
                "reply_id": "",
            }
        messages = self._receive_canfd(state, timeout_ms=timeout_ms)
        self._record_rx_frames(state, messages, label=f"{label}-rx")
        matched_data = None
        matched_can_id = None
        for rx_can_id, rx_data in messages:
            rx_id = rx_can_id & 0x1FFFFFFF
            fields = parse_can_id(rx_id)
            if (
                fields["access"] == request_access
                and fields["pcmd"] == pcmd
                and fields["scmd"] == scmd
                and fields["dst_id"] == HOST_ID
                and fields["src_id"] == dst_id
            ):
                matched_data = rx_data
                matched_can_id = rx_id
                break
        status = parse_l30_status(matched_data or b"") if matched_data is not None else None
        return {
            "matched": matched_data is not None,
            "status": status,
            "status_text": l30_status_text(status),
            "rx_count": len(messages),
            "reply_id": f"0x{matched_can_id:08X}" if matched_can_id is not None else "",
        }

    def _read_l30_product_code(self, state: DeviceState, node_id: int, timeout_ms: int = 50) -> dict:
        """读取 L30 产品编码字符串，按最新生产贴标规则补充展示信息。"""
        request_access = 0
        read_id = self._build_l30_can_id(
            state, request_access, PCMD_CONFIG, SCMD_PRODUCT_CODE, dst_id=node_id
        )
        self._send_can_frame(state, read_id, encode_empty_payload(), label="l30-product-code-read")
        if self.mock:
            code = f"LHT30-06-{state.dev + 1:03d}-L-B-B-A"
            return {
                "dev": state.dev,
                "query_id": f"0x{read_id:08X}",
                "query_node_id": node_id,
                "reply_id": "",
                "matched": True,
                "info": parse_l30_product_code(code.encode("ascii")),
                "rx_count": 0,
            }

        messages = self._receive_canfd(state, timeout_ms=timeout_ms)
        self._record_rx_frames(state, messages, label="l30-product-code-rx")
        for can_id, data in messages:
            rx_id = can_id & 0x1FFFFFFF
            fields = parse_can_id(rx_id)
            if (
                fields["access"] == request_access
                and fields["pcmd"] == PCMD_CONFIG
                and fields["scmd"] == SCMD_PRODUCT_CODE
                and fields["dst_id"] == HOST_ID
                and fields["src_id"] == node_id
            ):
                info = parse_l30_product_code(data)
                return {
                    "dev": state.dev,
                    "query_id": f"0x{read_id:08X}",
                    "query_node_id": node_id,
                    "reply_id": f"0x{rx_id:08X}",
                    "matched": bool(info),
                    "info": info,
                    "rx_count": len(messages),
                }
        return {
            "dev": state.dev,
            "query_id": f"0x{read_id:08X}",
            "query_node_id": node_id,
            "reply_id": "",
            "matched": False,
            "info": {},
            "rx_count": len(messages),
        }


    def _discover_l30_device_info(self, state: DeviceState, timeout_ms: int = 50) -> dict:
        """轮询 NodeID 读取 L30 DeviceInFo，并把成功结果写回设备状态。"""
        probed_ids = []
        rx_total = 0
        last_query_id = ""
        for node_id in self._l30_probe_node_ids(state):
            probed_ids.append(node_id)
            request_access = 0
            read_id = self._build_l30_can_id(
                state, request_access, PCMD_CONFIG, SCMD_DEVICE_INFO, dst_id=node_id
            )
            last_query_id = f"0x{read_id:08X}"
            self._send_can_frame(state, read_id, encode_empty_payload(), label="l30-info-read")
            if self.mock:
                info = {
                    "product_id": 0x13,
                    "product": "L30",
                    "serial_no": state.dev + 1,
                    "software": "Vmock",
                    "hardware": "Vmock",
                    "structure": "Vmock",
                    "node_id": node_id,
                    "l30_node_id": node_id,
                    "hand_type": 0,
                    "hand": "左手",
                    "sensor_type": 2,
                    "sensor": "华威科",
                    "origin_code": 1,
                    "origin": "北京自装",
                    "product_code": f"LHT30-06-{state.dev + 1:03d}-L-B-B-A",
                    "serial_label": f"LHT30-06-{state.dev + 1:03d}",
                }
                state.info = {**state.info, **info}
                return {
                    "dev": state.dev,
                    "query_id": last_query_id,
                    "query_node_id": node_id,
                    "reply_id": "",
                    "matched": True,
                    "info": info,
                    "rx_count": 0,
                    "probed_ids": probed_ids,
                }
            messages = self._receive_canfd(state, timeout_ms=timeout_ms)
            rx_total += len(messages)
            self._record_rx_frames(state, messages, label="l30-info-rx")
            for can_id, data in messages:
                rx_id = can_id & 0x1FFFFFFF
                fields = parse_can_id(rx_id)
                if (
                    fields["access"] == request_access
                    and fields["pcmd"] == PCMD_CONFIG
                    and fields["scmd"] == SCMD_DEVICE_INFO
                    and fields["dst_id"] == HOST_ID
                    and fields["src_id"] == node_id
                ):
                    info = parse_l30_device_info(data)
                    product_result = {"matched": False, "info": {}, "rx_count": 0}
                    if info:
                        info = {**info, "l30_node_id": int(info.get("node_id", node_id))}
                        product_result = self._read_l30_product_code(
                            state, int(info.get("node_id", node_id)), timeout_ms=timeout_ms
                        )
                        if product_result.get("matched") and product_result.get("info"):
                            info = {**info, **(product_result.get("info") or {})}
                        state.info = {**state.info, **info}
                    return {
                        "dev": state.dev,
                        "query_id": last_query_id,
                        "query_node_id": node_id,
                        "reply_id": f"0x{rx_id:08X}",
                        "matched": True,
                        "info": info,
                        "rx_count": rx_total + int(product_result.get("rx_count", 0)),
                        "probed_ids": probed_ids,
                        "product_code": product_result,
                    }
        return {
            "dev": state.dev,
            "query_id": last_query_id,
            "query_node_id": probed_ids[-1] if probed_ids else 0,
            "reply_id": "",
            "matched": False,
            "info": {},
            "rx_count": rx_total,
            "probed_ids": probed_ids,
        }

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
            fields = parse_can_id(rx_id)
            register = fields["scmd"]
            if label.startswith(("o20", "l30")):
                print(
                    f"RX DEV{state.dev} {label} 0x{rx_id:08X} "
                    f"reg=0x{register:02X} len={len(data)} data={data.hex(' ').upper()}",
                    flush=True,
                )
            if label.startswith("l30"):
                parsed: dict[str, object] = {
                    "access": fields["access"],
                    "pcmd": f"0x{fields['pcmd']:02X}",
                    "scmd": f"0x{register:02X}",
                    "dst_id": fields["dst_id"],
                    "src_id": fields["src_id"],
                }
                if len(data) >= 3:
                    status = parse_l30_status(data)
                    parsed["status"] = f"0x{int(status):02X}" if status is not None else ""
                    parsed["status_text"] = l30_status_text(status)
                if label == "l30-info-rx":
                    info = parse_l30_device_info(data)
                    if info:
                        parsed["device_info"] = info
                elif label == "l30-product-code-rx":
                    info = parse_l30_product_code(data)
                    if info:
                        parsed["product_code"] = info
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
        """兼容旧调用名，但不再隐式打开设备。"""
        return self._selected_existing_open_devices(dev_ids)

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
