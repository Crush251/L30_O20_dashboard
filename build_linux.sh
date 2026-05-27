#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

usage() {
    cat <<'EOF'
用法: ./build_linux.sh [amd64|arm64|all]

说明:
  amd64  打包 x86_64 Linux 版本，使用 libcanbus/libcanbus.so
  arm64  打包 ARM64/aarch64 Linux 版本，使用 libcanbus/libcanbus_arm64.so
  all    依次尝试打包两个版本

注意: PyInstaller 不能交叉编译 Python 可执行文件。amd64 版本需要在 x86_64 环境构建，arm64 版本需要在 ARM64/aarch64 环境构建。
EOF
}

detect_arch() {
    case "$(uname -m)" in
        x86_64|amd64) echo "amd64" ;;
        aarch64|arm64) echo "arm64" ;;
        *)
            echo "无法识别当前架构: $(uname -m)。请显式指定 amd64 或 arm64。" >&2
            exit 1
            ;;
    esac
}

build_target() {
    local target_arch="$1"
    local source_so=""
    local current_machine="$(uname -m)"

    case "$target_arch" in
        amd64)
            source_so="libcanbus/libcanbus.so"
            ;;
        arm64)
            source_so="libcanbus/libcanbus_arm64.so"
            ;;
        *)
            usage >&2
            exit 1
            ;;
    esac

    if [[ ! -f "$source_so" ]]; then
        echo "缺少 $source_so，无法打包 ${target_arch} 真实硬件版本。" >&2
        exit 1
    fi

    case "$target_arch:$current_machine" in
        amd64:x86_64|amd64:amd64|arm64:aarch64|arm64:arm64) ;;
        *)
            echo "警告: 当前机器架构是 ${current_machine}，正在打包 ${target_arch}。PyInstaller 不能交叉编译，产物可能不能在目标架构运行。" >&2
            ;;
    esac

    echo "开始打包 Linux ${target_arch} 版本，使用 ${source_so}。"
    L30_CANBUS_BUNDLE_SO="$source_so" uv run pyinstaller --clean --noconfirm l30_o20_dashboard.spec

    local target_exe="dist/l30-o20-dashboard-${target_arch}"
    mv dist/l30-o20-dashboard "$target_exe"
    echo "Linux ${target_arch} 可执行文件已生成: ${target_exe}"
}

target="${1:-$(detect_arch)}"

case "$target" in
    -h|--help)
        usage
        exit 0
        ;;
    amd64|arm64)
        uv sync --group dev
        build_target "$target"
        ;;
    all)
        uv sync --group dev
        build_target amd64
        build_target arm64
        ;;
    *)
        usage >&2
        exit 1
        ;;
esac
