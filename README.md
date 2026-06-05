# L30/O20 Dashboard

L30/O20 Dashboard 是用于 L30 和 O20 灵巧手的本地 CANFD 控制台。后端使用 FastAPI 管理 USB-CANFD 设备，前端提供统一的 `Dashboard(l30-O20)` 单页界面，用于设备连接、型号查询、使能、关节滑块、L30/O20 Dance 序列、RPS 和手势跟随。

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
├── templates/          # Dashboard 页面
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

- Dashboard: `http://127.0.0.1:8098/`
- 兼容路径：`/l30`、`/o20` 也会进入同一个 Dashboard。

无硬件调试：

```bash
L30_O20_DASHBOARD_MOCK=1 uv run l30-o20-dashboard
```

## 设备区分

每个 USB-CANFD 适配器显示为 `DEV0`、`DEV1` 等。后端只打开前端勾选的设备，未勾选设备不会默认连接；发送、使能、Dance、Game、Follow 都要求设备已显式连接，不会在发送路径里自动打开或抢占设备。

统一 Dashboard 中，每个设备卡片都有型号档案：

- `设备查询` 会先按 L30 新协议读取 DeviceInFo，成功后补充读取 LHT30 产品编码；未识别为 L30 的设备再按 O20 协议轮询左右手节点，识别到 O20 后记录该 DEV 对应的左右手节点。
- 查询成功后该 DEV 自动标记为 `L30` 或 `O20`；也可以在设备卡片中手动指定型号。
- 设备属性区按型号动态渲染：只有 O20 设备显示左右手节点下拉框，L30 不显示该属性。

统一滑块发送时，L30 接收 J01-J17；O20 只接收 J01-J16，J17 会被过滤；O20 CANFD FrameType 固定使用 0x04。RPS 和 Follow 会同时分发给已勾选、已连接且型号匹配的 L30/O20 设备。Dance 区域分为 L30 Dance 和 O20 Dance，只会发送到对应型号的已勾选设备。Game 区域提供摄像头分辨率选择：240p、480p、720p、1080p。

## 传感器监控

顶栏可切换到 `传感器` 页面，也可直接访问 `/sensor`。传感器页只读取已连接的 DEV，不会重新打开或抢占 CANFD 设备。页面支持勾选多个设备，并按设备型号渲染独立卡片：

- O20：按寄存器 `0x09~0x12` 主动查询五指触觉数据，每指解析为在线标志 + 72 点阵。
- L30：按 v2 协议父命令 `0x2`、子命令 `0x01~0x05` 主动查询五指 12x6 触觉矩阵，并完成多帧拼包。

前端统一展示为五指 12x6 热力点阵，数值越大红色越深。

多实例运行时，用不同端口启动，例如 `--port 8098` 和 `--port 8099`。同一个后端进程内每个 DEV 只创建并缓存一个设备状态对象，后续发送、查询、Dance、Game、Follow 都复用该对象，不会再次打开设备。普通“连接所选”不会抢占其他进程已经打开的 CANFD；如果需要接管，前端提供“强制连接”，会显式尝试 `CAN_CloseDevice` 后重新打开。该行为是否能跨进程生效取决于厂商驱动，如果驱动拒绝释放，仍会返回失败。

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

并复制打包内置的 dance 文件。前端保存的动作序列也会落到同一个运行时目录。Dance 执行线程会定期清理 CANFD RX 缓冲，O20 dance 不再每帧创建后台 RX probe，降低长时间循环后缓冲堆积或线程堆积风险。

## L30 新协议

L30 控制按 `src/L30灵巧手CANFD扩展帧通讯协议-v2.md` 实现：

- 使能：父命令 `0x01`，子命令 `0x07`，写命令，payload `00 00`
- 失能：父命令 `0x01`，子命令 `0x08`，写命令，payload `00 00`
- 关节位置：父命令 `0x01`，子命令 `0x01`，payload `0x22 0x00 + int16[17]`，关节值大端；J1 拇指指根范围 `0~880`
- 有应答命令：应答 CANFD ID 的 bit25 Access 必须与请求一致，写请求应答仍为写 Access
- DeviceInFo：父命令 `0x03`，子命令 `0x02`，读命令，解析型号、序列号、版本、节点、左右手等信息；随后读取 `0x03/0x03` 产品编码用于展示最新 LHT30 生产编码
- 周期上报：父命令 `0x04`，配置 Period 合法范围 `20ms~600000ms`

前端滑块始终使用 `0-100`，后端根据关节范围映射成真实关节值并做边界约束。

## 打包

Linux：

```bash
./build_linux.sh amd64
./build_linux.sh arm64
```

`amd64` 使用 `libcanbus/libcanbus.so`，`arm64` 使用 `libcanbus/libcanbus_arm64.so`。PyInstaller 需要在对应 CPU 架构的 Linux 机器上打包，不能在 amd64 机器上直接生成 arm64 可执行文件。

Windows：

```bat
build_windows.bat
```

输出：

```text
dist/l30-o20-dashboard-linux-amd64
dist/l30-o20-dashboard-linux-arm64
dist/l30-o20-dashboard.exe
```
