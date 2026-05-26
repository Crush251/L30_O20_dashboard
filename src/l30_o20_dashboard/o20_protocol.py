from __future__ import annotations

import re
from typing import Iterable


O20_CONTROL_COUNT = 16
O20_FRAME_JOINT_COUNT = 17
O20_DEFAULT_DEVICE_ID = 0x01
O20_REG_DEVICE_INFO = 0x00
O20_REG_ERROR_STATUS = 0x02
O20_REG_CURRENT_POS = 0x03
O20_REG_CURRENT_VEL = 0x04
O20_REG_TARGET_POS = 0x06
O20_REG_TARGET_VEL = 0x07
O20_TARGET_VEL_RAW_MAX = 32767


# O20 16 自由度真实角度范围，顺序对应协议里的电机 ID 1-16。
O20_JOINT_DEFS = [
    (0, 120, "拇指指根"),
    (0, 150, "拇指指尖"),
    (0, 180, "拇指侧摆"),
    (0, 130, "拇指旋转"),
    (-30, 30, "食指侧摆"),
    (0, 180, "食指指根"),
    (0, 180, "食指指尖"),
    (-30, 30, "中指侧摆"),
    (0, 180, "中指指根"),
    (0, 180, "中指指尖"),
    (-20, 20, "无名指侧摆"),
    (0, 180, "无名指指根"),
    (0, 180, "无名指指尖"),
    (-20, 20, "小指侧摆"),
    (0, 180, "小指指根"),
    (0, 180, "小指指尖"),
]


def o20_build_can_id(device_id: int, register_addr: int, is_write: bool) -> int:
    """按 O20 寄存器协议组合 29 位扩展帧 ID。"""
    return (
        ((int(device_id) & 0xFF) << 21)
        | ((int(register_addr) & 0xFF) << 13)
        | ((1 if is_write else 0) << 12)
    )


def o20_percent_to_joint(index: int, percent: int | float) -> int:
    """把前端 0-100 映射到 O20 对应关节的真实角度。"""
    lower, upper, _name = O20_JOINT_DEFS[index]
    ratio = max(0, min(100, int(round(float(percent))))) / 100
    return int(round(lower + (upper - lower) * ratio))


def o20_map_normalized(values: Iterable[int | float]) -> list[int]:
    """批量映射 O20 16DOF 归一化控制值。"""
    normalized = list(values)
    if len(normalized) != O20_CONTROL_COUNT:
        raise ValueError(f"expected {O20_CONTROL_COUNT} O20 joint values, got {len(normalized)}")
    return [o20_percent_to_joint(index, value) for index, value in enumerate(normalized)]


def o20_encode_int16_frame(values: Iterable[int]) -> bytes:
    """编码 O20 int16[17] payload，小端，第 17 位保留为 0。"""
    joints = list(values)
    if len(joints) != O20_CONTROL_COUNT:
        raise ValueError(f"expected {O20_CONTROL_COUNT} O20 joint values, got {len(joints)}")
    frame_values = joints + [0]
    payload = bytearray()
    for value in frame_values:
        value = max(-32768, min(32767, int(value)))
        payload.extend(value.to_bytes(2, byteorder="little", signed=True))
    return bytes(payload)


def o20_encode_raw_int16_frame(values: Iterable[int]) -> bytes:
    """编码 O20 原始 int16 payload，支持 dance 文件中的 16 或 17 个值。"""
    joints = list(values)
    if len(joints) == O20_CONTROL_COUNT:
        joints = joints + [0]
    if len(joints) != O20_FRAME_JOINT_COUNT:
        raise ValueError(f"expected {O20_CONTROL_COUNT} or {O20_FRAME_JOINT_COUNT} O20 values, got {len(joints)}")
    payload = bytearray()
    for value in joints:
        value = max(-32768, min(32767, int(value)))
        payload.extend(value.to_bytes(2, byteorder="little", signed=True))
    return bytes(payload)


def o20_encode_target_positions(values: Iterable[int]) -> bytes:
    """编码 O20 SYS_TARGET_POS payload。"""
    return o20_encode_int16_frame(values)


def o20_encode_target_velocities(velocity: int | float) -> bytes:
    """编码 O20 SYS_TARGET_VEL payload，16 个自由度使用同一速度值。"""
    value = max(0, min(O20_TARGET_VEL_RAW_MAX, int(round(float(velocity)))))
    return o20_encode_int16_frame([value] * O20_CONTROL_COUNT)


def o20_parse_int16_values(data: bytes, count: int = O20_FRAME_JOINT_COUNT) -> list[int]:
    """解析 O20 小端 int16 数组回传。"""
    raw = bytes(data)
    usable = min(len(raw) // 2, count)
    return [
        int.from_bytes(raw[index * 2 : index * 2 + 2], byteorder="little", signed=True)
        for index in range(usable)
    ]


def o20_parse_error_status(data: bytes) -> list[int]:
    """解析 O20 SYS_ERROR_STATUS，最多 17 个电机错误字节。"""
    return [int(value) & 0xFF for value in bytes(data)[:O20_FRAME_JOINT_COUNT]]


def o20_parse_device_info(data: bytes) -> dict[str, str]:
    """解析 O20 SYS_DEVICE_INFO 返回数据。"""
    raw = bytes(data)

    def text(start: int, length: int) -> str:
        return raw[start : start + length].split(b"\0", 1)[0].decode("utf-8", errors="replace")

    ascii_text = raw.decode("ascii", errors="ignore").replace("\0", "")
    serial_match = re.search(r"LHO20-\d{3}-\d{3}-[LR]-[A-Z]-\d-[A-Z]", ascii_text)
    versions = re.findall(r"\d+\.\d+\.\d", ascii_text)
    hand_flag = raw[50] if len(raw) > 50 else None
    uid = raw[51:63].hex(" ").upper() if len(raw) > 51 else ""
    hand_text = ""
    if hand_flag == 1:
        hand_text = "右手"
    elif hand_flag == 2:
        hand_text = "左手"
    return {
        "model": text(0, 10),
        "serial": serial_match.group(0) if serial_match else text(10, 20),
        "software": versions[0] if len(versions) >= 1 else text(30, 10),
        "hardware": versions[1] if len(versions) >= 2 else text(40, 10),
        "hand": hand_text,
        "hand_flag": f"0x{hand_flag:02X}" if hand_flag is not None else "",
        "uid": uid,
        "raw": raw.hex(" ").upper(),
    }
