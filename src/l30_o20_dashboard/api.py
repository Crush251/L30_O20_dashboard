from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .canbus import CanBusController
from .dance_runner import DanceRunner
from .dance_store import (
    dance_files,
    load_dance_frames,
    load_o20_dance_frames,
    o20_rps_sequence,
    save_l30_sequence,
    save_o20_sequence,
)
from .o20_protocol import O20_FRAME_TYPE
from .paths import O20_DANCE_DIR, STATIC_DIR, TEMPLATE_DIR
from .schemas import (
    DanceRequest,
    DeviceSelection,
    EnableRequest,
    GameRequest,
    JointRequest,
    O20DanceRequest,
    O20ErrorRequest,
    O20GameRequest,
    O20InfoRequest,
    O20JointRequest,
    O20VelocityRequest,
    SequenceSaveRequest,
    SensorReadRequest,
)
from .sequences import GESTURE_SEQUENCES, serialize_sequence


controller = CanBusController()
dance_runner = DanceRunner(controller, "l30")
o20_dance_runner = DanceRunner(controller, "o20")


def create_app() -> FastAPI:
    """创建 FastAPI 应用并注册页面、设备、游戏和 dance 路由。"""
    api = FastAPI(title="L30/O20 Dashboard")
    api.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @api.get("/", response_class=HTMLResponse)
    @api.get("/l30", response_class=HTMLResponse)
    @api.get("/o20", response_class=HTMLResponse)
    @api.get("/sensor", response_class=HTMLResponse)
    def dashboard_page() -> str:
        """返回统一 L30/O20 Dashboard。"""
        return (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")

    @api.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        """浏览器 favicon 请求直接返回空响应。"""
        return Response(status_code=204)

    @api.get("/api/status")
    def status() -> dict:
        """查询设备状态。"""
        return controller.status()

    @api.post("/api/scan")
    def scan() -> dict:
        """扫描 CAN 设备。"""
        return controller.scan()

    @api.post("/api/open")
    def open_devices(payload: DeviceSelection) -> dict:
        """打开前端勾选的设备；force=True 时显式尝试接管被占用设备。"""
        try:
            return {"devices": controller.open_devices(payload.devices, force=payload.force)}
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/enable")
    def set_enabled(payload: EnableRequest) -> dict:
        """使能或失能前端勾选的设备。"""
        try:
            return {"devices": controller.set_enabled(payload.devices, payload.enabled)}
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/devices/query")
    def query_devices(payload: DeviceSelection) -> dict:
        """统一查询设备型号：先按 L30 DeviceInFo 探测，未匹配时再轮询 O20。"""
        try:
            devices = [int(dev) for dev in payload.devices]
            l30_results = controller.query_l30_device_info(devices)
            l30_by_dev = {
                int(item["dev"]): item
                for item in l30_results
                if item.get("matched") and item.get("info")
            }

            o20_probe_devs = [dev for dev in devices if dev not in l30_by_dev]
            o20_results = (
                controller.query_o20_device_info(
                    o20_probe_devs,
                    device_id=0,
                    frame_type=O20_FRAME_TYPE,
                )
                if o20_probe_devs
                else []
            )
            o20_by_dev: dict[int, dict] = {}
            for item in o20_results:
                info = item.get("info") or {}
                if item.get("matched") and info:
                    o20_by_dev.setdefault(int(item["dev"]), item)

            profiles = []
            for dev in devices:
                if dev in l30_by_dev:
                    item = l30_by_dev[dev]
                    profiles.append(
                        {
                            "dev": dev,
                            "model": "l30",
                            "matched": True,
                            "node_id": (item.get("info") or {}).get("node_id"),
                            "info": item.get("info") or {},
                            "source": "l30",
                        }
                    )
                elif dev in o20_by_dev:
                    item = o20_by_dev[dev]
                    profiles.append(
                        {
                            "dev": dev,
                            "model": "o20",
                            "matched": True,
                            "device_id": item.get("device_id"),
                            "info": item.get("info") or {},
                            "source": "o20",
                        }
                    )
                else:
                    profiles.append(
                        {
                            "dev": dev,
                            "model": "unknown",
                            "matched": False,
                            "info": {},
                            "source": "",
                        }
                    )
            return {
                "profiles": profiles,
                "o20_results": o20_results,
                "l30_results": l30_results,
            }
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/l30/info")
    def query_l30_info(payload: DeviceSelection) -> dict:
        """按 L30 新协议查询 DeviceInFo，前端用于识别型号、左右手和版本。"""
        try:
            return {"results": controller.query_l30_device_info(payload.devices)}
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/joints")
    def set_joints(payload: JointRequest) -> dict:
        """发送前端 0-100 归一化关节值。"""
        try:
            return {
                "devices": controller.set_joints(
                    payload.devices,
                    payload.joints,
                    normalized=True,
                    require_open=True,
                )
            }
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/o20/joints")
    def set_o20_joints(payload: O20JointRequest) -> dict:
        """发送 O20 16DOF 归一化关节值。"""
        try:
            return {
                "devices": controller.set_o20_joints_by_device_ids(
                    payload.devices,
                    payload.joints,
                    device_ids=payload.device_ids,
                    device_id=payload.device_id,
                    frame_type=O20_FRAME_TYPE,
                    require_open=True,
                )
            }
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/o20/velocity")
    def set_o20_velocity(payload: O20VelocityRequest) -> dict:
        """发送 O20 目标速度。"""
        try:
            return {
                "devices": controller.set_o20_velocity_by_device_ids(
                    payload.devices,
                    payload.velocity,
                    device_ids=payload.device_ids,
                    device_id=payload.device_id,
                    frame_type=O20_FRAME_TYPE,
                    require_open=True,
                )
            }
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/o20/info")
    def query_o20_info(payload: O20InfoRequest) -> dict:
        """查询 O20 设备信息寄存器。"""
        try:
            return {
                "results": controller.query_o20_device_info(
                    payload.devices,
                    device_id=payload.device_id,
                    frame_type=O20_FRAME_TYPE,
                )
            }
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/o20/error")
    def query_o20_error(payload: O20ErrorRequest) -> dict:
        """查询 O20 错误状态寄存器。"""
        try:
            return {
                "results": controller.query_o20_error_status(
                    payload.devices,
                    device_ids=payload.device_ids,
                    device_id=payload.device_id,
                    frame_type=O20_FRAME_TYPE,
                )
            }
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/o20/error/clear")
    def clear_o20_error(payload: O20ErrorRequest) -> dict:
        """清除 O20 错误状态寄存器。"""
        try:
            return {
                "devices": controller.clear_o20_error_status(
                    payload.devices,
                    device_ids=payload.device_ids,
                    device_id=payload.device_id,
                    frame_type=O20_FRAME_TYPE,
                )
            }
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/sensors/read")
    def read_sensors(payload: SensorReadRequest) -> dict:
        """主动读取已连接 L30/O20 设备的触觉传感器点阵。"""
        try:
            return {
                "mock": controller.mock,
                "devices": controller.query_tactile_sensors(
                    payload.devices,
                    profiles=payload.profiles,
                    drain=payload.drain,
                )
            }
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/o20/game")
    def play_o20_game(payload: O20GameRequest) -> dict:
        """O20 RPS 模式发送剪刀、石头、布动作序列。"""
        sequence = o20_rps_sequence(payload.gesture)
        try:
            devices = controller.run_o20_sequence(
                payload.devices,
                sequence,
                device_id=payload.device_id,
                device_ids=payload.device_ids,
                frame_type=O20_FRAME_TYPE,
            )
            return {"gesture": payload.gesture, "sent": True, "devices": devices}
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.post("/api/game")
    def play_game(payload: GameRequest) -> dict:
        """RPS 模式发送剪刀、石头、布动作序列。"""
        if payload.gesture not in GESTURE_SEQUENCES:
            raise HTTPException(status_code=400, detail=f"unknown gesture: {payload.gesture}")
        sequence = serialize_sequence(GESTURE_SEQUENCES[payload.gesture])
        if not sequence:
            return {
                "gesture": payload.gesture,
                "sent": False,
                "message": "动作序列暂未填写",
                "devices": controller.status()["devices"],
            }
        try:
            return {
                "gesture": payload.gesture,
                "sent": True,
                "devices": controller.run_sequence(payload.devices, sequence),
            }
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=controller.explain_error(str(exc))) from exc

    @api.get("/api/dance")
    def list_dance() -> dict:
        """返回 L30 dance 文件列表和执行状态。"""
        return {"files": dance_files(), "status": dance_runner.snapshot()}

    @api.post("/api/dance/run")
    def run_dance(payload: DanceRequest) -> dict:
        """启动指定 L30 dance 文件。"""
        if not payload.devices:
            raise HTTPException(status_code=400, detail="请先勾选设备")
        frames = load_dance_frames(payload.file)
        try:
            return {"files": dance_files(), "status": dance_runner.start(payload, frames)}
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/api/dance/stop")
    def stop_dance() -> dict:
        """停止当前 L30 dance。"""
        return {"files": dance_files(), "status": dance_runner.stop()}

    @api.post("/api/dance/save")
    def save_dance(payload: SequenceSaveRequest) -> dict:
        """保存 L30 姿态序列到 L30 dance 文件夹。"""
        try:
            return save_l30_sequence(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/api/o20/dance")
    def list_o20_dance() -> dict:
        """返回 O20 dance 文件列表和执行状态。"""
        return {"files": dance_files(O20_DANCE_DIR), "status": o20_dance_runner.snapshot()}

    @api.post("/api/o20/dance/run")
    def run_o20_dance(payload: O20DanceRequest) -> dict:
        """启动指定 O20 dance 文件。"""
        if not payload.devices:
            raise HTTPException(status_code=400, detail="请先勾选设备")
        frames = load_o20_dance_frames(payload.file)
        try:
            return {
                "files": dance_files(O20_DANCE_DIR),
                "status": o20_dance_runner.start(payload, frames),
            }
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/api/o20/dance/stop")
    def stop_o20_dance() -> dict:
        """停止当前 O20 dance。"""
        return {"files": dance_files(O20_DANCE_DIR), "status": o20_dance_runner.stop()}

    @api.post("/api/o20/dance/save")
    def save_o20_dance(payload: SequenceSaveRequest) -> dict:
        """保存 O20 姿态序列到 O20 dance 文件夹。"""
        try:
            return save_o20_sequence(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.on_event("shutdown")
    def shutdown() -> None:
        """服务退出时关闭 dance 线程和 CAN 设备。"""
        dance_runner.stop()
        o20_dance_runner.stop()
        controller.close_all()

    return api


app = create_app()
