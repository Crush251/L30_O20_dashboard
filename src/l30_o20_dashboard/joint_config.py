from __future__ import annotations

from typing import Iterable

from .sequences import JOINT_COUNT


# 17 个关节的真实物理范围，前端仍统一使用 0-100 的归一化值。
JOINT_DEFS = [
    (0, 880, "拇指指根"),
    (0, 1200, "拇指指尖"),
    (0, 900, "拇指侧摆"),
    (0, 800, "拇指旋转"),
    (-200, 200, "无名指侧摆"),
    (0, 1200, "无名指指尖"),
    (0, 1200, "无名指根"),
    (0, 1200, "中指指根"),
    (0, 1200, "中指指尖"),
    (0, 1500, "小指指根"),
    (0, 1200, "小指指尖"),
    (-200, 200, "小指侧摆"),
    (-200, 200, "中指侧摆"),
    (-200, 200, "食指侧摆"),
    (0, 1200, "食指指根"),
    (0, 1200, "食指指尖"),
    (-900, 900, "手腕"),
]


def normalize_joint_percent(value: int | float) -> int:
    """把前端输入约束到 0-100 的百分比范围。"""
    return max(0, min(100, int(round(float(value)))))


def joint_from_percent(index: int, percent: int | float) -> int:
    """把某个关节的百分比映射成该关节真实范围内的数值。"""
    lower, upper, _name = JOINT_DEFS[index]
    ratio = normalize_joint_percent(percent) / 100
    return int(round(lower + (upper - lower) * ratio))


def clamp_joint_value(index: int, value: int | float) -> int:
    """把真实关节值约束到该关节允许的物理范围。"""
    lower, upper, _name = JOINT_DEFS[index]
    raw = int(round(float(value)))
    return max(lower, min(upper, raw))


def map_normalized_joints(values: Iterable[int | float]) -> list[int]:
    """批量把前端 0-100 关节值映射成真实关节值。"""
    normalized = list(values)
    if len(normalized) != JOINT_COUNT:
        raise ValueError(f"expected {JOINT_COUNT} joint values, got {len(normalized)}")
    return [joint_from_percent(index, value) for index, value in enumerate(normalized)]


def clamp_joint_values(values: Iterable[int | float]) -> list[int]:
    """批量约束真实关节值，dance 文件和后端序列走这个路径。"""
    raw_values = list(values)
    if len(raw_values) != JOINT_COUNT:
        raise ValueError(f"expected {JOINT_COUNT} joint values, got {len(raw_values)}")
    return [clamp_joint_value(index, value) for index, value in enumerate(raw_values)]
