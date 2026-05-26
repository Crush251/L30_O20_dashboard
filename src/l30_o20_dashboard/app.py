from __future__ import annotations

import argparse

import uvicorn

from .api import app


def main() -> None:
    """本地启动 FastAPI 服务。"""
    parser = argparse.ArgumentParser(description="L30/O20 CANFD 控制台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    args = parser.parse_args()
    uvicorn.run("l30_o20_dashboard.api:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
