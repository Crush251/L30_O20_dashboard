# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH)


def add_tree(source: str, target: str):
    path = ROOT / source
    if path.exists():
        return [(str(path), target)]
    return []


datas = []
datas += add_tree("src/l30_o20_dashboard/static", "l30_o20_dashboard/static")
datas += add_tree("src/l30_o20_dashboard/templates", "l30_o20_dashboard/templates")
datas += add_tree("src/l30_o20_dashboard/dance", "l30_o20_dashboard/dance")
datas += add_tree(os.environ.get("L30_CANBUS_BUNDLE_SO", "libcanbus/libcanbus.so"), "libcanbus")
datas += add_tree("libcanbus/HCanbus.dll", "libcanbus")

hiddenimports = collect_submodules("uvicorn")


a = Analysis(
    ["pyinstaller_entry.py"],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="l30-o20-dashboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
