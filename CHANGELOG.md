# Changelog

## Unreleased

- 项目命名迁移为 `l30_o20_dashboard` / `l30-o20-dashboard`。
- 拆分 `app.py`：启动入口、FastAPI 路由、请求模型、路径管理、dance 保存和 dance 执行线程分离。
- 新增 `--host`、`--port` 启动参数，运行脚本会透传命令行参数。
- L30 按新版 CANFD 扩展帧协议更新使能、失能、17 关节目标位置 payload。
- 新增 L30 DeviceInFo 查询接口和前端“设备查询”按钮。
- Dance 文件按 L30/O20 分目录保存，打包后首次运行自动生成运行时 dance 目录并复制内置文件。
- 连接时只打开前端勾选的 DEV，未勾选设备不再默认连接。
