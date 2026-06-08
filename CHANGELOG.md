# Changelog

## Unreleased

- L30 应答匹配改为严格校验 CANFD ID bit25 Access，写请求只接受写 Access 应答，避免误匹配其它返回帧。
- L30 J1 拇指指根目标位置范围更新为 0~880，并同步前端 Follow 的真实范围反算。
- L30 设备查询在 DeviceInFo 成功后补充读取产品编码，并按最新 LHT30 生产编码解析展示。
- L30 周期上报配置 Period 范围更新为 20ms~600000ms。
- 前端合并为统一 `Dashboard(l30-O20)` 页面，`/`、`/l30`、`/o20` 均进入同一个 Dashboard。
- 设备卡片增加 L30/O20 型号档案和 O20 左右手节点配置，统一“设备查询”会自动识别型号；只有 O20 动态显示左右手下拉框。
- L30 Dance 和 O20 Dance 在同页分区显示并独立执行，只作用于对应型号的已勾选设备。
- 发送类接口改为要求设备已显式连接，避免发送、使能或序列执行时隐式打开 CANFD 设备。
- 新增显式“强制连接”按钮，会在普通打开失败后尝试 CloseDevice 再重新打开，是否能接管取决于驱动。
- Dance 执行期间定期清理 CANFD RX 缓冲，并关闭 O20 dance 的逐帧后台 RX probe，降低长时间运行后缓冲堆积风险。
- Game 摄像头增加 240p / 480p / 720p / 1080p 分辨率选择。
- Python `mediapipe` 和 `opencv-python` 从默认依赖移到可选 `legacy-vision`，ARM Linux 打包默认只使用前端离线 MediaPipe JS/WASM 资源，避免 Python wheel 架构兼容问题。

- 项目命名迁移为 `l30_o20_dashboard` / `l30-o20-dashboard`。
- 拆分 `app.py`：启动入口、FastAPI 路由、请求模型、路径管理、dance 保存和 dance 执行线程分离。
- 新增 `--host`、`--port` 启动参数，运行脚本会透传命令行参数。
- L30 按新版 CANFD 扩展帧协议更新使能、失能、17 关节目标位置 payload，并解析使能/失能 ACK 状态码。
- 新增 L30 DeviceInFo 查询接口和前端“设备查询”按钮，查询时自动探测并缓存设备 NodeID。
- Dance 文件按 L30/O20 分目录保存，打包后首次运行自动生成运行时 dance 目录并复制内置文件。
- 连接时只打开前端勾选的 DEV，未勾选设备不再默认连接。
