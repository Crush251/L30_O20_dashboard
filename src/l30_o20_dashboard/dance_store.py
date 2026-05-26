from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from .joint_config import map_normalized_joints
from .o20_protocol import O20_CONTROL_COUNT, O20_FRAME_JOINT_COUNT, o20_map_normalized
from .paths import L30_DANCE_DIR, O20_DANCE_DIR
from .protocol import joints_from_dance_row
from .schemas import SequenceSaveRequest


def dance_files(root: Path = L30_DANCE_DIR) -> list[str]:
    """列出可用 dance 文件名。"""
    if not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_file())


def dance_path(file_name: str, root: Path = L30_DANCE_DIR) -> Path:
    """校验并返回 dance 文件路径，禁止路径穿越。"""
    if Path(file_name).name != file_name:
        raise HTTPException(status_code=400, detail="invalid dance file name")
    path = root / file_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"dance file not found: {file_name}")
    return path


def save_path(file_name: str, root: Path) -> Path:
    """校验并返回可写入的 dance 文件路径，自动补 .txt 后缀。"""
    safe_name = Path(file_name.strip()).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="请输入文件名")
    if not safe_name.lower().endswith(".txt"):
        safe_name = f"{safe_name}.txt"
    if Path(safe_name).name != safe_name:
        raise HTTPException(status_code=400, detail="invalid dance file name")
    root.mkdir(parents=True, exist_ok=True)
    return root / safe_name


def load_dance_frames(file_name: str) -> list[list[int]]:
    """把 L30 dance 文本中的十六进制帧解析成真实关节值列表。"""
    path = dance_path(file_name)
    frames = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        try:
            frame = bytes.fromhex(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{file_name} 第 {line_no} 行不是有效十六进制序列",
            ) from exc
        if not 0 < len(frame) <= 64:
            raise HTTPException(
                status_code=400,
                detail=f"{file_name} 第 {line_no} 行长度为 {len(frame)} 字节，必须为 1..64 字节",
            )
        try:
            frames.append(joints_from_dance_row(frame))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{file_name} 第 {line_no} 行{exc}") from exc
    if not frames:
        raise HTTPException(status_code=400, detail=f"{file_name} 没有可发送序列")
    return frames


def load_o20_dance_frames(file_name: str) -> list[list[int]]:
    """解析 O20 dance 文件中的制表符/空格分隔原始关节值。

    O20 历史 dance 文件常见格式是 16 个关节值后追加一个 1000。该尾列不是 16DOF
    目标位置的一部分；执行时只保留前 16 个关节，发送层会把协议要求的第 17 位补 0。
    """
    path = dance_path(file_name, O20_DANCE_DIR)
    frames = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        parts = value.replace(",", " ").split()
        if len(parts) not in (O20_CONTROL_COUNT, O20_FRAME_JOINT_COUNT):
            raise HTTPException(
                status_code=400,
                detail=f"{file_name} 第 {line_no} 行应为 {O20_CONTROL_COUNT} 或 {O20_FRAME_JOINT_COUNT} 个数值",
            )
        try:
            frames.append([int(round(float(part))) for part in parts[:O20_CONTROL_COUNT]])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{file_name} 第 {line_no} 行存在非数字") from exc
    if not frames:
        raise HTTPException(status_code=400, detail=f"{file_name} 没有可发送序列")
    return frames


def o20_rps_sequence(gesture: str) -> list[dict]:
    """返回 O20 剪刀石头布原始序列。"""
    file_map = {"石头": "fist.txt", "剪刀": "yeal.txt"}
    if gesture == "布":
        return [{"joints": [0] * O20_CONTROL_COUNT, "hold_ms": 250}]
    file_name = file_map.get(gesture)
    if not file_name:
        raise HTTPException(status_code=400, detail=f"unknown gesture: {gesture}")
    return [{"joints": joints, "hold_ms": 120} for joints in load_o20_dance_frames(file_name)]


def save_l30_sequence(payload: SequenceSaveRequest) -> dict:
    """把前端 L30 归一化姿态保存成 L30 dance hex 文件。"""
    path = save_path(payload.file, L30_DANCE_DIR)
    lines = []
    for frame in payload.frames:
        values = map_normalized_joints(frame)
        raw = bytearray()
        for value in values:
            raw.extend(int(value).to_bytes(2, byteorder="big", signed=True))
        lines.append(raw.hex(" ").upper())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"file": path.name, "count": len(lines), "files": dance_files(L30_DANCE_DIR)}


def save_o20_sequence(payload: SequenceSaveRequest) -> dict:
    """把前端 O20 归一化姿态保存成 O20 16DOF 数值序列文件。"""
    path = save_path(payload.file, O20_DANCE_DIR)
    lines = []
    for frame in payload.frames:
        values = o20_map_normalized(frame)
        lines.append("\t".join(str(value) for value in values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"file": path.name, "count": len(lines), "files": dance_files(O20_DANCE_DIR)}
