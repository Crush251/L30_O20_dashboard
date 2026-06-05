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
PCMD_TACTILE_SENSOR = 0x02
PCMD_CONFIG = 0x03
PCMD_PERIODIC_REPORT = 0x04
SCMD_JOINT_POS = 0x01
SCMD_GLOBAL_ENABLE = 0x07
SCMD_GLOBAL_DISABLE = 0x08
SCMD_CONFIG_UNLOCK = 0x01
SCMD_DEVICE_INFO = 0x02
SCMD_PRODUCT_CODE = 0x03

# 周期上报配置的 Period 合法范围，Enable=0 时 Period 可为 0。
L30_PERIOD_MIN_MS = 20
L30_PERIOD_MAX_MS = 600_000

# 通用状态码，适用于父命令 0x1~0x6。
L30_STATUS_TEXT = {
    0x00: "成功",
    0x10: "DLC 或数据长度不匹配",
    0x11: "参数越界或非法值",
    0x12: "不支持的命令码",
    0x13: "无效子命令",
    0x14: "数据格式错误",
    0x20: "权限不足或未解锁",
    0x21: "未标定零点",
    0x22: "电机未使能",
    0x23: "设备当前状态不允许操作",
    0x24: "设备当前未设置",
    0x30: "读取电机数据异常",
    0x31: "写入电机数据异常",
    0x32: "多帧传输超时",
    0x33: "多帧数据不完整",
    0x34: "周期上报配置失败",
    0x35: "上报周期越界，合法范围 20ms~600000ms",
    0x36: "事件掩码无效",
    0x40: "通信超时",
    0x41: "通信丢失",
    0xF0: "系统忙，稍后重试",
}

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


def parse_can_id(can_id: int) -> dict[str, int]:
    """解析 L30 29bit 扩展帧 CAN ID，便于严格匹配应答字段。"""
    raw_id = int(can_id) & 0x1FFFFFFF
    return {
        "priority": (raw_id >> 26) & 0x07,
        "access": (raw_id >> 25) & 0x01,
        "pcmd": (raw_id >> 21) & 0x0F,
        "scmd": (raw_id >> 13) & 0xFF,
        "dst_id": (raw_id >> 8) & 0x1F,
        "src_id": (raw_id >> 3) & 0x1F,
    }


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


def encode_periodic_report_config(enabled: bool, period_ms: int, joint_mask: int = 0) -> bytes:
    """编码 L30 周期上报配置 payload：Enable + uint32 Period + uint32 关节掩码。"""
    period = int(period_ms)
    if enabled and not (L30_PERIOD_MIN_MS <= period <= L30_PERIOD_MAX_MS):
        raise ValueError(
            f"L30 report period must be {L30_PERIOD_MIN_MS}..{L30_PERIOD_MAX_MS}ms, got {period}"
        )
    if not enabled:
        period = max(0, min(L30_PERIOD_MAX_MS, period))
    mask = max(0, min(0x1FFFF, int(joint_mask)))
    return bytes([0x09, 0x00, 0x01 if enabled else 0x00]) + period.to_bytes(
        4, byteorder="big", signed=False
    ) + mask.to_bytes(4, byteorder="big", signed=False)


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


def l30_status_text(status: int | None) -> str:
    """把 L30 状态码转换为中文描述。"""
    if status is None:
        return "无状态码"
    return L30_STATUS_TEXT.get(status, f"未知状态 0x{status:02X}")


def parse_l30_response(data: bytes) -> dict[str, object]:
    """解析 L30 通用应答头：BYTE0 长度、BYTE1 事务、BYTE2 状态。"""
    raw = bytes(data)
    if len(raw) < 3:
        return {
            "data_length": raw[0] if raw else 0,
            "transaction": raw[1] if len(raw) > 1 else 0,
            "status": None,
            "status_text": "应答长度不足",
            "payload": b"",
        }
    data_length = int(raw[0])
    transaction = int(raw[1])
    status = int(raw[2])
    payload = raw[3 : 3 + data_length]
    return {
        "data_length": data_length,
        "transaction": transaction,
        "status": status,
        "status_text": l30_status_text(status),
        "payload": payload,
    }


def parse_l30_status(data: bytes) -> int | None:
    """提取 L30 应答状态码。"""
    response = parse_l30_response(data)
    status = response.get("status")
    return int(status) if status is not None else None


def parse_l30_device_info(data: bytes) -> dict[str, str | int]:
    """解析 L30 DeviceInFo 应答中的 18 字节设备信息。"""
    raw = bytes(data)
    if len(raw) >= 21 and raw[2] in L30_STATUS_TEXT:
        response = parse_l30_response(raw)
        if response.get("status") != 0:
            return {}
        raw = bytes(response.get("payload", b""))[:18]
    elif len(raw) >= 18:
        raw = raw[:18]
    else:
        return {}

    if len(raw) < 18:
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


def parse_l30_product_code(data: bytes) -> dict[str, str | int]:
    """解析 L30 产品编码应答，兼容直接传入 ASCII payload 的调试路径。"""
    raw = bytes(data)
    if not raw:
        return {}
    if len(raw) >= 3 and raw[2] in L30_STATUS_TEXT:
        response = parse_l30_response(raw)
        if response.get("status") != 0:
            return {}
        raw = bytes(response.get("payload", b""))
    code = raw.split(b"\0", 1)[0].decode("ascii", errors="ignore").strip()
    if not code:
        return {}

    info: dict[str, str | int] = {"product_code": code, "serial_label": code}
    parts = code.split("-")
    if len(parts) >= 7:
        series, version, serial, hand_code, sensor_code, comm_code, origin_code = parts[:7]
        hand_map = {"L": "左手", "R": "右手"}
        sensor_map = {"A": "他山", "B": "华威科", "J": "晶智感", "F": "福莱新材", "Z": "无传感器"}
        origin_map = {"A": "北京自装", "B": "大厂", "C": "固安"}
        comm_map = {"1": "CAN/CANFD", "B": "CANFD", "2": "Modbus-485"}
        info.update(
            {
                "product_series": series,
                "product_version": version,
                "production_no": serial,
                "serial_label": "-".join(parts[:3]),
                "hand_code": hand_code,
                "hand": hand_map.get(hand_code, hand_code),
                "sensor_code": sensor_code,
                "sensor": sensor_map.get(sensor_code, sensor_code),
                "comm_code": comm_code,
                "communication": comm_map.get(comm_code, comm_code),
                "origin_code_label": origin_code,
                "origin": origin_map.get(origin_code, origin_code),
            }
        )
    return info
