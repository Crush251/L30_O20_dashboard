#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f "libcanbus/libcanbus.so" ]]; then
    echo "缺少 libcanbus/libcanbus.so，无法打包真实硬件版本。"
    exit 1
fi

uv sync --group dev
uv run pyinstaller --clean --noconfirm l30_o20_dashboard.spec

echo "Linux 可执行文件已生成: dist/l30-o20-dashboard"
