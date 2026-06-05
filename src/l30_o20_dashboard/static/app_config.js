// 前端统一常量：用 IIFE 避免普通 script 的顶层 const 与 app.js 重名。
(() => {
    // 手动滑块保持 0-100，真实关节范围由后端再次约束。
    const JOINT_COUNT = 17;
    const MANUAL_JOINT_MIN = 0;
    const MANUAL_JOINT_MAX = 100;

    // 关节中文名称，对应 J1-J17 的显示顺序。
    const JOINT_LABELS = [
        "拇指指根弯曲",
        "拇指指尖弯曲",
        "拇指侧摆",
        "拇指旋转",
        "无名指侧摆",
        "无名指指尖弯曲",
        "无名指指根弯曲",
        "中指指根弯曲",
        "中指指尖弯曲",
        "小指指根弯曲",
        "小指指尖弯曲",
        "小指侧摆",
        "中指侧摆",
        "食指侧摆",
        "食指指根弯曲",
        "食指指尖弯曲",
        "手腕弯曲"
    ];

    // 真实关节范围，用于 Follow 模式把真实值反算成前端百分比。
    const JOINT_RANGES = [
        [0, 880],
        [0, 1200],
        [0, 900],
        [0, 800],
        [-200, 200],
        [0, 1200],
        [0, 1200],
        [0, 1200],
        [0, 1200],
        [0, 1500],
        [0, 1200],
        [-200, 200],
        [-200, 200],
        [-200, 200],
        [0, 1200],
        [0, 1200],
        [-900, 900]
    ];

    // 摄像头识别和 Follow 发送节流间隔。
    const RECOGNITION_INTERVAL_MS = 30;
    const FOLLOW_SEND_INTERVAL_MS = 30;
    const FOLLOW_CHANGE_THRESHOLD_PERCENT = 2;

    // RPS 模式的克制关系。
    const RESPONSE_GESTURE = {
        "布": "剪刀",
        "石头": "布",
        "剪刀": "石头"
    };

    window.L30AppConfig = {
        JOINT_COUNT,
        MANUAL_JOINT_MIN,
        MANUAL_JOINT_MAX,
        JOINT_LABELS,
        JOINT_RANGES,
        RECOGNITION_INTERVAL_MS,
        FOLLOW_SEND_INTERVAL_MS,
        FOLLOW_CHANGE_THRESHOLD_PERCENT,
        RESPONSE_GESTURE
    };
})();
