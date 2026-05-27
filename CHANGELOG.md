# Changelog

## Unreleased

- 前端合并为统一 `Dashboard(l30-O20)` 页面，`/`、`/l30`、`/o20` 均进入同一个 Dashboard。
- 设备卡片增加 L30/O20 型号档案和 O20 左右手节点配置，滑块、RPS、Follow 按型号分发，J17 只发送给 L30。
- L30 Dance 和 O20 Dance 在同页分区显示并独立执行，只作用于对应型号的已勾选设备。
- 发送类接口改为要求设备已显式连接，避免发送、使能或序列执行时隐式打开 CANFD 设备。

- 项目命名迁移为 `l30_o20_dashboard` / `l30-o20-dashboard`。
- 拆分 `app.py`：启动入口、FastAPI 路由、请求模型、路径管理、dance 保存和 dance 执行线程分离。
- 新增 `--host`、`--port` 启动参数，运行脚本会透传命令行参数。
- L30 按新版 CANFD 扩展帧协议更新使能、失能、17 关节目标位置 payload，并解析使能/失能 ACK 状态码。
- 新增 L30 DeviceInFo 查询接口和前端“设备查询”按钮，查询时自动探测并缓存设备 NodeID。
- Dance 文件按 L30/O20 分目录保存，打包后首次运行自动生成运行时 dance 目录并复制内置文件。
- 连接时只打开前端勾选的 DEV，未勾选设备不再默认连接。
