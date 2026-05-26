# L30/O20 Dashboard

L30/O20 Dashboard 是用于 L30 和 O20 灵巧手的本地 CANFD 控制台。后端使用 FastAPI 管理 USB-CANFD 设备，前端提供 L30 与 O20 两个独立页面，用于设备连接、使能、关节滑块、Dance 序列、RPS 和手势跟随。

## 项目结构

```text
src/l30_o20_dashboard/
├── app.py              # 命令行启动入口
├── api.py              # FastAPI 应用和路由注册
├── schemas.py          # API 请求模型
├── paths.py            # 静态资源、模板、dance 运行目录
├── dance_store.py      # dance 文件读取、解析、保存
├── dance_runner.py     # dance 后台执行线程
├── canbus.py           # Linux/Windows CANFD 控制器
├── protocol.py         # L30 CANFD 扩展帧协议
├── o20_protocol.py     # O20 CANFD 寄存器协议
├── joint_config.py     # L30 关节范围与归一化映射
├── static/             # 前端 JS/CSS 与 MediaPipe 离线资源
├── templates/          # L30/O20 页面
└── dance/              # 内置 L30/O20 dance 文件
```

## 启动

开发环境：

```bash
uv sync
uv run l30-o20-dashboard --port 8098
```

Linux 真机通常需要 USB 权限：

```bash
./run_sudo.sh --port 8098
```

Windows：

```bat
run_windows.bat --port 8098
```

访问地址：

- L30: `http://127.0.0.1:8098/l30`
- O20: `http://127.0.0.1:8098/o20`

无硬件调试：

```bash
L30_O20_DASHBOARD_MOCK=1 uv run l30-o20-dashboard
```

## 设备区分

每个 USB-CANFD 适配器显示为 `DEV0`、`DEV1` 等。后端只打开前端勾选的设备，未勾选设备不会默认连接。连接一只 L30 和一只 O20 时：

- 单进程：在 `/l30` 页面只勾选 L30 所在 DEV，在 `/o20` 页面只勾选 O20 所在 DEV。
- 双进程：用不同端口启动两个实例，例如 `--port 8098` 和 `--port 8099`，每个实例只连接自己的 DEV，避免误选和总线占用。

L30 页面“设备查询”按 L30 新协议读取 DeviceInFo；O20 页面“设备查询”按 O20 协议读取型号、序列号、软件/硬件版本和左右手标志。

## Dance 文件

源码运行时，读取和保存路径一致：

```text
src/l30_o20_dashboard/dance/L30
src/l30_o20_dashboard/dance/O20
```

PyInstaller 打包后，首次运行会在可执行文件同级创建：

```text
dance/L30
dance/O20
```

并复制打包内置的 dance 文件。前端保存的动作序列也会落到同一个运行时目录。

## L30 新协议

L30 控制按 `src/L30灵巧手CANFD扩展帧通讯协议.md` 实现：

- 使能：父命令 `0x01`，子命令 `0x07`，写命令，payload `00 00`
- 失能：父命令 `0x01`，子命令 `0x08`，写命令，payload `00 00`
- 关节位置：父命令 `0x01`，子命令 `0x01`，payload `0x22 0x00 + int16[17]`，关节值大端
- DeviceInFo：父命令 `0x03`，子命令 `0x02`，读命令，解析型号、序列号、版本、节点、左右手等信息

前端滑块始终使用 `0-100`，后端根据关节范围映射成真实关节值并做边界约束。

## 打包

Linux：

```bash
./build_linux.sh
```

Windows：

```bat
build_windows.bat
```

输出：

```text
dist/l30-o20-dashboard
dist/l30-o20-dashboard.exe
```
