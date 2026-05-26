from __future__ import annotations

from typing import Iterable

from .sequences import JOINT_COUNT


# CAN 设备和主机编号，当前项目默认控制第一只 L30 手。
CH = 0
HOST_ID = 0
DEVICE_ID = 1

# L30 关节控制协议命令字。
PROTOCOL_PRIORITY = 0
PCMD_JOINT_CTRL = 0x01
PCMD_CONFIG = 0x03
SCMD_JOINT_POS = 0x01
SCMD_GLOBAL_ENABLE = 0x07
SCMD_GLOBAL_DISABLE = 0x08
SCMD_DEVICE_INFO = 0x02

# 发送超时时间，Linux so 使用该值；Windows DLL 内部封装使用自己的值。
TRANSMIT_TIMEOUT_MS = 200


def len_to_dlc(length: int) -> int:
    """把 CANFD 数据长度转换成 DLC 编码。"""
    if length <= 0:
        return 0
    if length <= 8:
        return length
    if length <= 12:
        return 9
    if length <= 16:
        return 10
    if length <= 20:
        return 11
    if length <= 24:
        return 12
    if length <= 32:
        return 13
    if length <= 48:
        return 14
    return 15


def build_can_id(priority: int, access: int, pcmd: int, scmd: int, dst_id: int, src_id: int) -> int:
    """按 L30 协议字段组合扩展帧 CAN ID。"""
    return (
        ((priority & 0x07) << 26)
        | ((access & 0x01) << 25)
        | ((pcmd & 0x0F) << 21)
        | ((scmd & 0xFF) << 13)
        | ((dst_id & 0x1F) << 8)
        | ((src_id & 0x1F) << 3)
    )


def encode_joint_payload(positions: Iterable[int]) -> bytes:
    """把 17 个真实关节值编码成 L30 关节位置 payload。"""
    values = list(positions)
    if len(values) != JOINT_COUNT:
        raise ValueError(f"expected {JOINT_COUNT} joint values, got {len(values)}")

    payload = bytearray(36)
    payload[0] = 0x22
    payload[1] = 0x00
    for index, value in enumerate(values):
        value = max(-32768, min(32767, int(value)))
        payload[2 + index * 2 : 2 + index * 2 + 2] = value.to_bytes(
            2, byteorder="big", signed=True
        )
    return bytes(payload)


def joints_from_dance_row(raw: bytes) -> list[int]:
    """从 dance 二进制行里解析 17 个真实关节值。"""
    joint_bytes = JOINT_COUNT * 2
    if len(raw) < joint_bytes:
        raise ValueError(f"expected at least {joint_bytes} bytes, got {len(raw)}")
    return [
        int.from_bytes(raw[index : index + 2], byteorder="big", signed=True)
        for index in range(0, joint_bytes, 2)
    ]


def encode_empty_payload() -> bytes:
    """编码 L30 0 数据命令，BYTE0 为数据长度，BYTE1 为事务控制。"""
    return bytes([0x00, 0x00])


def parse_l30_device_info(data: bytes) -> dict[str, str | int]:
    """解析 L30 DeviceInFo 应答中的 18 字节设备信息。"""
    raw = bytes(data)
    if len(raw) >= 21 and raw[2] == 0x00:
        raw = raw[3:21]
    elif len(raw) >= 18:
        raw = raw[:18]
    else:
        return {}

    def version(offset: int) -> str:
        return f"V{raw[offset]}.{raw[offset + 1]}.{raw[offset + 2]}"

    hand_map = {0: "左手", 1: "右手"}
    sensor_map = {1: "他山", 2: "华威科", 3: "晶智感", 4: "福莱新材"}
    origin_map = {1: "北京自装", 2: "大厂", 3: "固安"}
    product_id = raw[0]
    serial_no = int.from_bytes(raw[1:5], byteorder="big", signed=False)
    node_id = raw[14]
    hand_type = raw[15]
    sensor_type = raw[16]
    origin = raw[17]
    return {
        "product_id": product_id,
        "product": "L30" if product_id == 0x13 else f"0x{product_id:02X}",
        "serial_no": serial_no,
        "software": version(5),
        "hardware": version(8),
        "structure": version(11),
        "node_id": node_id,
        "hand_type": hand_type,
        "hand": hand_map.get(hand_type, f"未知({hand_type})"),
        "sensor_type": sensor_type,
        "sensor": sensor_map.get(sensor_type, f"未知({sensor_type})"),
        "origin_code": origin,
        "origin": origin_map.get(origin, f"未知({origin})"),
    }
