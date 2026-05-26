from __future__ import annotations

import threading
from typing import Any

from .o20_protocol import O20_DEFAULT_DEVICE_ID


class DanceRunner:
    """后台执行 dance 文件，避免阻塞 FastAPI 请求线程。"""

    def __init__(self, controller: Any, product: str = "l30") -> None:
        self.controller = controller
        self.product = product
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.status: dict = {
            "running": False,
            "file": "",
            "loop_count": 0,
            "interval_ms": 0,
            "sent": 0,
            "message": "未执行",
        }

    def snapshot(self) -> dict:
        """返回当前 dance 执行状态快照。"""
        with self.lock:
            alive = self.thread is not None and self.thread.is_alive()
            return {**self.status, "running": alive}

    def start(self, payload: Any, frames: list[list[int]]) -> dict:
        """启动新的 dance 执行线程。"""
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError("已有 dance 序列正在执行，请先停止")

            self.stop_event.clear()
            self.status = {
                "running": True,
                "file": payload.file,
                "loop_count": payload.loop_count,
                "interval_ms": payload.interval_ms,
                "sent": 0,
                "message": "执行中",
            }
            self.thread = threading.Thread(
                target=self._run,
                args=(payload, frames),
                name=f"{self.product}-dance-runner",
                daemon=True,
            )
            self.thread.start()
            return self.snapshot()

    def stop(self) -> dict:
        """请求停止当前 dance 执行线程。"""
        self.stop_event.set()
        with self.lock:
            if self.status.get("running"):
                self.status["message"] = "停止中"
            return self.snapshot()

    def _run(self, payload: Any, frames: list[list[int]]) -> None:
        """在线程中循环发送 dance 帧。"""
        sent = 0
        message = "执行完成"
        try:
            loops_done = 0
            while not self.stop_event.is_set():
                if payload.loop_count and loops_done >= payload.loop_count:
                    break
                for joints in frames:
                    if self.stop_event.is_set():
                        message = "已停止"
                        break
                    if self.product == "o20":
                        device_ids = getattr(payload, "device_ids", {}) or {}
                        self.controller.set_o20_raw_joints_by_device_ids(
                            payload.devices,
                            joints,
                            device_ids=device_ids,
                            device_id=getattr(payload, "device_id", O20_DEFAULT_DEVICE_ID),
                            frame_type=getattr(payload, "frame_type", 0x04),
                            label=f"o20-dance:{payload.file}",
                            require_open=True,
                        )
                    else:
                        self.controller.set_joints(
                            payload.devices,
                            joints,
                            label=f"dance:{payload.file}",
                            require_open=True,
                        )
                    sent += 1
                    with self.lock:
                        self.status["sent"] = sent
                    if payload.interval_ms and self.stop_event.wait(payload.interval_ms / 1000):
                        message = "已停止"
                        break
                loops_done += 1
        except Exception as exc:
            message = self.controller.explain_error(str(exc))
        finally:
            with self.lock:
                self.status["running"] = False
                self.status["sent"] = sent
                self.status["message"] = message
