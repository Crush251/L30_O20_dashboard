from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# 统一传感器矩阵规格：五指各 12x6，共 72 个触觉点。
TACTILE_ROWS = 12
TACTILE_COLS = 6
TACTILE_POINT_COUNT = TACTILE_ROWS * TACTILE_COLS
TACTILE_VALUE_MAX = 255


@dataclass(frozen=True)
class FingerSensorSpec:
    """单个手指触觉传感器在各型号协议里的位置。"""

    key: str
    label: str
    l30_scmd: int
    o20_data1_reg: int
    o20_data2_reg: int


FINGER_SENSOR_SPECS = [
    FingerSensorSpec("thumb", "拇指", 0x01, 0x09, 0x0A),
    FingerSensorSpec("index", "食指", 0x02, 0x0B, 0x0C),
    FingerSensorSpec("middle", "中指", 0x03, 0x0D, 0x0E),
    FingerSensorSpec("ring", "无名指", 0x04, 0x0F, 0x10),
    FingerSensorSpec("pinky", "小指", 0x05, 0x11, 0x12),
]


def clamp_tactile_value(value: int | float) -> int:
    """把触觉原始值约束到前端热力图使用的 uint8 范围。"""
    return max(0, min(TACTILE_VALUE_MAX, int(round(float(value)))))


def normalize_tactile_values(values: Iterable[int | float]) -> list[int]:
    """返回固定 72 点的触觉数组，不足补 0，超出截断。"""
    normalized = [clamp_tactile_value(value) for value in values]
    if len(normalized) < TACTILE_POINT_COUNT:
        normalized.extend([0] * (TACTILE_POINT_COUNT - len(normalized)))
    return normalized[:TACTILE_POINT_COUNT]


def parse_o20_tactile_block(data1: bytes, data2: bytes) -> dict[str, object]:
    """解析 O20 单指触觉块：73 字节 = 在线标志 + 72 点阵。"""
    raw = bytes(data1[:64]) + bytes(data2[:9])
    if len(raw) < TACTILE_POINT_COUNT + 1:
        raw = raw + bytes(TACTILE_POINT_COUNT + 1 - len(raw))
    online = raw[0] == 1
    values = normalize_tactile_values(raw[1 : TACTILE_POINT_COUNT + 1])
    return {
        "online": online,
        "values": values,
        "rows": TACTILE_ROWS,
        "cols": TACTILE_COLS,
        "max": max(values) if values else 0,
        "avg": round(sum(values) / len(values), 1) if values else 0,
    }


def parse_l30_tactile_frames(frames: Iterable[bytes]) -> dict[str, object]:
    """解析 L30 单指触觉多帧应答，返回 12x6 点阵。"""
    chunks: dict[int, bytes] = {}
    expected_last_seq: int | None = None
    status: int | None = None
    for frame in frames:
        data = bytes(frame)
        if len(data) < 3:
            continue
        data_length = int(data[0])
        transaction = int(data[1])
        frame_status = int(data[2])
        total_seq = (transaction >> 4) & 0x0F
        seq = transaction & 0x0F
        if status is None:
            status = frame_status
        if frame_status != 0:
            status = frame_status
            continue
        expected_last_seq = total_seq
        chunks[seq] = data[3 : 3 + data_length]

    complete = expected_last_seq is not None and all(
        index in chunks for index in range(expected_last_seq + 1)
    )
    payload = b"".join(chunks[index] for index in sorted(chunks))
    values = normalize_tactile_values(payload[:TACTILE_POINT_COUNT])
    return {
        "online": complete and status == 0,
        "values": values,
        "rows": TACTILE_ROWS,
        "cols": TACTILE_COLS,
        "max": max(values) if values else 0,
        "avg": round(sum(values) / len(values), 1) if values else 0,
        "status": status,
        "complete": complete,
        "received_frames": len(chunks),
        "expected_frames": (expected_last_seq + 1) if expected_last_seq is not None else 0,
    }


def make_mock_tactile_values(seed: int) -> list[int]:
    """生成稳定的 mock 触觉点阵，便于无硬件调试页面渲染。"""
    values = []
    for index in range(TACTILE_POINT_COUNT):
        row = index // TACTILE_COLS
        col = index % TACTILE_COLS
        wave = (row * 17 + col * 31 + seed * 23) % 256
        values.append(wave if (row + col + seed) % 5 == 0 else max(0, wave // 3))
    return values


def tactile_summary(fingers: list[dict[str, object]]) -> dict[str, object]:
    """汇总单个设备所有手指的触觉强度。"""
    all_values = [int(value) for finger in fingers for value in finger.get("values", [])]
    online_count = sum(1 for finger in fingers if finger.get("online"))
    return {
        "online_fingers": online_count,
        "max": max(all_values) if all_values else 0,
        "avg": round(sum(all_values) / len(all_values), 1) if all_values else 0,
    }
