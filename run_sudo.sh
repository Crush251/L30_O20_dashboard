#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

UV_BIN="$(command -v uv)"

ENV_ARGS=(
    "PATH=$PATH"
)

# 可选：只在外部设置了 L30_CANBUS_LIB 时传入
if [[ -n "${L30_CANBUS_LIB:-}" ]]; then
    ENV_ARGS+=("L30_CANBUS_LIB=$L30_CANBUS_LIB")
fi

# 默认不启动 mock：只有显式设置 L30_O20_DASHBOARD_MOCK 时才传入
if [[ -n "${L30_O20_DASHBOARD_MOCK:-}" ]]; then
    ENV_ARGS+=("L30_O20_DASHBOARD_MOCK=$L30_O20_DASHBOARD_MOCK")
fi

sudo env "${ENV_ARGS[@]}" "$UV_BIN" run l30-o20-dashboard "$@"