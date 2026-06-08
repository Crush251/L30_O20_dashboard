from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from .o20_protocol import (
    O20_CONTROL_COUNT,
    O20_DEFAULT_DEVICE_ID,
    O20_TARGET_VEL_RAW_MAX,
)
from .sequences import JOINT_COUNT


# 前端传入的关节值统一是 0-100，由后端映射到真实关节范围。
JointValue = Annotated[int, Field(ge=0, le=100)]


class DeviceSelection(BaseModel):
    """前端勾选的 USB-CANFD 设备编号列表。"""

    devices: list[int] = Field(default_factory=list)
    force: bool = False


class EnableRequest(DeviceSelection):
    """L30 全局使能/失能请求。"""

    enabled: bool


class JointRequest(DeviceSelection):
    """L30 17 关节归一化目标值。"""

    joints: list[JointValue] = Field(min_length=JOINT_COUNT, max_length=JOINT_COUNT)
    require_open: bool = False


class O20JointRequest(DeviceSelection):
    """O20 16DOF 归一化目标位置请求。"""

    joints: list[JointValue] = Field(min_length=O20_CONTROL_COUNT, max_length=O20_CONTROL_COUNT)
    device_id: int = Field(default=O20_DEFAULT_DEVICE_ID, ge=1, le=255)
    device_ids: dict[int, int] = Field(default_factory=dict)
    require_open: bool = False


class O20VelocityRequest(DeviceSelection):
    """O20 目标速度请求，前端 0-100 已映射到原始速度值。"""

    velocity: int = Field(default=5000, ge=0, le=O20_TARGET_VEL_RAW_MAX)
    device_id: int = Field(default=O20_DEFAULT_DEVICE_ID, ge=1, le=255)
    device_ids: dict[int, int] = Field(default_factory=dict)
    require_open: bool = False


class O20InfoRequest(DeviceSelection):
    """O20 设备信息查询请求，device_id=0 表示轮询左右手节点。"""

    device_id: int = Field(default=0, ge=0, le=255)


class O20ErrorRequest(DeviceSelection):
    """O20 错误查询/清除请求。"""

    device_id: int = Field(default=O20_DEFAULT_DEVICE_ID, ge=1, le=255)
    device_ids: dict[int, int] = Field(default_factory=dict)




class SensorDeviceProfile(BaseModel):
    """传感器页面提交的单个设备型号信息。"""

    model: str = "unknown"
    device_id: int = Field(default=O20_DEFAULT_DEVICE_ID, ge=1, le=255)
    confirmed: bool = False


class SensorReadRequest(DeviceSelection):
    """传感器主动查询请求，profiles 用于区分 L30/O20 和 O20 左右手节点。"""

    profiles: dict[int, SensorDeviceProfile] = Field(default_factory=dict)
    drain: bool = False


class GameRequest(DeviceSelection):
    """RPS 手势回应请求。"""

    gesture: str


class DanceRequest(DeviceSelection):
    """Dance 文件执行请求。"""

    file: str
    loop_count: int = Field(ge=0)
    interval_ms: int = Field(ge=0)


class O20DanceRequest(DanceRequest):
    """O20 Dance 文件执行请求，包含每个 DEV 的节点 ID 映射。"""

    device_id: int = Field(default=O20_DEFAULT_DEVICE_ID, ge=1, le=255)
    device_ids: dict[int, int] = Field(default_factory=dict)


class SequenceSaveRequest(BaseModel):
    """前端记录的多个姿态，用于保存为 dance 文件。"""

    file: str
    frames: list[list[JointValue]] = Field(min_length=1)


class O20GameRequest(GameRequest):
    """O20 RPS 请求，包含每个 DEV 的节点 ID 映射。"""

    device_id: int = Field(default=O20_DEFAULT_DEVICE_ID, ge=1, le=255)
    device_ids: dict[int, int] = Field(default_factory=dict)
