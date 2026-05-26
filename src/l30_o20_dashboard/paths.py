from __future__ import annotations

import shutil
import sys
from pathlib import Path


def resource_root() -> Path:
    """返回开发环境或 PyInstaller 打包环境下的只读资源根目录。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "l30_o20_dashboard"
    return Path(__file__).resolve().parent


def runtime_dance_root() -> Path:
    """返回可读写 dance 目录；打包后放在 exe 同级。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "dance"
    return resource_root() / "dance"


def ensure_runtime_dance_dir() -> Path:
    """首次运行创建 dance/L30 和 dance/O20，并复制打包内置序列。"""
    target_root = runtime_dance_root()
    bundled_root = resource_root() / "dance"
    for product in ("L30", "O20"):
        target_dir = target_root / product
        source_dir = bundled_root / product
        target_dir.mkdir(parents=True, exist_ok=True)
        if not source_dir.exists():
            continue
        try:
            same_dir = source_dir.resolve() == target_dir.resolve()
        except OSError:
            same_dir = False
        if same_dir:
            continue
        for source_file in source_dir.iterdir():
            if not source_file.is_file():
                continue
            target_file = target_dir / source_file.name
            if not target_file.exists():
                shutil.copy2(source_file, target_file)
    return target_root


# 静态资源、模板和 dance 文件目录。
BASE_DIR = resource_root()
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"
DANCE_DIR = ensure_runtime_dance_dir()
L30_DANCE_DIR = DANCE_DIR / "L30"
O20_DANCE_DIR = DANCE_DIR / "O20"
