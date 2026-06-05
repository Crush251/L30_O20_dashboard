#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

usage() {
    cat <<'EOF'
用法: ./build_linux.sh [amd64|arm64|all]

说明:
  amd64  使用 libcanbus/libcanbus.so 打包 x86_64 Linux 版本
  arm64  使用 libcanbus/libcanbus_arm64.so 打包 aarch64 Linux 版本
  all    在当前机器架构可执行范围内打包；PyInstaller 不支持跨架构生成可执行文件
EOF
}

target="${1:-$(uname -m)}"
case "$target" in
    x86_64|amd64) target="amd64" ;;
    aarch64|arm64) target="arm64" ;;
    all) target="all" ;;
    -h|--help) usage; exit 0 ;;
    *)
        usage
        echo "未知目标架构: $target"
        exit 1
        ;;
esac

host_arch="$(uname -m)"
case "$host_arch" in
    x86_64) host_target="amd64" ;;
    aarch64) host_target="arm64" ;;
    *)
        echo "不支持的当前机器架构: $host_arch"
        exit 1
        ;;
esac

build_one() {
    local arch="$1"
    local source_so
    local suffix

    case "$arch" in
        amd64)
            source_so="libcanbus/libcanbus.so"
            suffix="linux-amd64"
            ;;
        arm64)
            source_so="libcanbus/libcanbus_arm64.so"
            suffix="linux-arm64"
            ;;
        *)
            echo "未知打包架构: $arch"
            exit 1
            ;;
    esac

    if [[ "$arch" != "$host_target" ]]; then
        echo "当前机器是 $host_target，不能用 PyInstaller 直接生成 $arch 可执行文件。"
        echo "请在 ${arch} Linux 机器上执行: ./build_linux.sh $arch"
        exit 1
    fi

    if [[ ! -f "$source_so" ]]; then
        echo "缺少 $source_so，无法打包 $arch 真实硬件版本。"
        exit 1
    fi

    echo "[1/3] 同步开发依赖..."
    uv sync --group dev

    echo "[2/3] 打包 $arch，CAN 库: $source_so"
    L30_CANBUS_BUNDLE_LIB="$source_so" \
    L30_DASHBOARD_DIST_SUFFIX="$suffix" \
        uv run pyinstaller --clean --noconfirm l30_o20_dashboard.spec

    echo "[3/3] Linux $arch 可执行文件已生成: dist/l30-o20-dashboard-$suffix"
}

if [[ "$target" == "all" ]]; then
    build_one "$host_target"
    other="amd64"
    [[ "$host_target" == "amd64" ]] && other="arm64"
    echo
    echo "已完成当前架构 $host_target。$other 需要在对应架构 Linux 机器上执行 ./build_linux.sh $other。"
else
    build_one "$target"
fi
