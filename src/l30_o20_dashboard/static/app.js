// 主入口只负责页面状态和接口调用，常量与 Follow 映射已拆到独立文件。
const {
    JOINT_COUNT,
    MANUAL_JOINT_MIN,
    MANUAL_JOINT_MAX,
    JOINT_LABELS,
    JOINT_RANGES,
    RECOGNITION_INTERVAL_MS,
    FOLLOW_SEND_INTERVAL_MS,
    FOLLOW_CHANGE_THRESHOLD_PERCENT,
    RESPONSE_GESTURE
} = window.L30AppConfig;

const state = {
    devices: [],
    selected: new Set(),
    joints: defaultJoints(),
    poseRecords: [],
    selectedPoseIndex: -1,
    cameras: [],
    danceFiles: [],
    selectedDanceFile: "",
    danceRunning: false,
    danceStatusTimer: 0,
    selectedCameraId: "",
    camera: null,
    frameRequest: 0,
    hands: null,
    gameMode: "rps",
    followMapper: window.L30Follow.createFollowMapper(),
    lastFollowSendAt: 0,
    lastFollowSentJoints: null,
    followSendBusy: false,
    gameRunning: false,
    lastAcceptedGesture: "",
    lastRecognitionAt: 0,
    lastUiUpdateAt: 0,
    recognitionBusy: false,
    actionInFlight: false,
    pendingResponseGesture: "",
    pendingSourceGesture: ""
};

const els = {
    runtimeStatus: document.getElementById("runtimeStatus"),
    deviceList: document.getElementById("deviceList"),
    scanBtn: document.getElementById("scanBtn"),
    openBtn: document.getElementById("openBtn"),
    l30InfoBtn: document.getElementById("l30InfoBtn"),
    enableBtn: document.getElementById("enableBtn"),
    disableBtn: document.getElementById("disableBtn"),
    txLog: document.getElementById("txLog"),
    sliders: document.getElementById("sliders"),
    zeroBtn: document.getElementById("zeroBtn"),
    sendBtn: document.getElementById("sendBtn"),
    poseRecordBtn: document.getElementById("poseRecordBtn"),
    poseOverwriteBtn: document.getElementById("poseOverwriteBtn"),
    poseRunBtn: document.getElementById("poseRunBtn"),
    poseDeleteBtn: document.getElementById("poseDeleteBtn"),
    poseSaveBtn: document.getElementById("poseSaveBtn"),
    poseList: document.getElementById("poseList"),
    poseFileName: document.getElementById("poseFileName"),
    poseStatus: document.getElementById("poseStatus"),
    refreshDanceBtn: document.getElementById("refreshDanceBtn"),
    runDanceBtn: document.getElementById("runDanceBtn"),
    stopDanceBtn: document.getElementById("stopDanceBtn"),
    danceFileList: document.getElementById("danceFileList"),
    danceLoopCount: document.getElementById("danceLoopCount"),
    danceIntervalMs: document.getElementById("danceIntervalMs"),
    danceStatus: document.getElementById("danceStatus"),
    rpsModeBtn: document.getElementById("rpsModeBtn"),
    followModeBtn: document.getElementById("followModeBtn"),
    gameBtn: document.getElementById("gameBtn"),
    stopGameBtn: document.getElementById("stopGameBtn"),
    cameraList: document.getElementById("cameraList"),
    inputVideo: document.getElementById("inputVideo"),
    outputCanvas: document.getElementById("outputCanvas"),
    gestureName: document.getElementById("gestureName"),
    gestureConfidence: document.getElementById("gestureConfidence"),
    debugLines: document.getElementById("debugLines"),
    gameResult: document.getElementById("gameResult")
};

// 统一封装后端 API 调用，失败时抛出后端返回的中文错误。
async function api(path, body = null) {
    const options = body
        ? {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body)
          }
        : { method: "GET" };
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    }
    return data;
}

// 读取当前勾选的 CAN 设备编号。
function selectedDevices() {
    return Array.from(state.selected).sort((a, b) => a - b);
}

// Follow 只能复用已经连接并使能的设备，避免触发后端隐式打开。
function selectedReadyDevices() {
    return state.devices
        .filter((device) => state.selected.has(device.dev) && device.opened && device.enabled)
        .map((device) => device.dev)
        .sort((a, b) => a - b);
}

// 更新顶部运行状态。
function setStatus(text) {
    els.runtimeStatus.textContent = text;
}

// 转义设备信息文本，避免后端返回内容破坏页面结构。
function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

// 把 L30 DeviceInFo 和 USB-CAN 适配器信息整理成人类可读的一行。
function formatL30DeviceInfo(info = {}) {
    if (info.product || info.serial_no || info.hand) {
        const product = info.product || "L30";
        const serial = info.serial_no ? `SN ${info.serial_no}` : "SN -";
        const software = info.software || "-";
        const hardware = info.hardware || "-";
        const hand = info.hand || "左右手未知";
        return `${product} ${hand} · ${serial} · 软件 ${software} · 硬件 ${hardware}`;
    }
    return `${info.type || "L30 CANFD"} ${info.serial || ""} ${info.firmware || ""}`.trim();
}

// 渲染设备列表；首次扫描只默认勾选第一个设备，其余必须手动勾选。
function renderDevices(payload) {
    state.devices = payload.devices || [];
    const shouldSelectFirst = state.selected.size === 0 && state.devices.length > 0;
    for (const [index, dev] of state.devices.entries()) {
        if (shouldSelectFirst && index === 0) {
            state.selected.add(dev.dev);
        }
    }

    els.deviceList.innerHTML = "";
    if (!state.devices.length) {
        els.deviceList.innerHTML = `<div class="device-meta">未发现设备</div>`;
    }

    for (const dev of state.devices) {
        const info = dev.info || {};
        const item = document.createElement("label");
        item.className = "device-item";
        item.innerHTML = `
            <input type="checkbox" ${state.selected.has(dev.dev) ? "checked" : ""} data-dev="${dev.dev}">
            <div>
                <div class="device-name">DEV${dev.dev} CH${dev.ch} ${dev.opened ? "已连接" : "未连接"} ${dev.enabled ? "已使能" : ""}</div>
                <div class="device-meta">${escapeHtml(formatL30DeviceInfo(info))}</div>
            </div>
        `;
        item.querySelector("input").addEventListener("change", (event) => {
            const id = Number(event.target.dataset.dev);
            if (event.target.checked) {
                state.selected.add(id);
            } else {
                state.selected.delete(id);
            }
        });
        els.deviceList.appendChild(item);
    }

    const mode = payload.mock ? "Mock 模式" : "硬件模式";
    const detail = payload.count ? `${payload.count} 个设备` : "无设备";
    setStatus(`${mode} · ${detail}`);
    if (Object.prototype.hasOwnProperty.call(payload, "last_tx")) {
        renderTx(payload.last_tx || []);
    }
}

// 渲染最近发送的 CAN 帧，便于排查连接和使能问题。
function renderTx(frames) {
    if (!frames.length) {
        els.txLog.textContent = "暂无发送记录";
        return;
    }
    els.txLog.textContent = frames
        .slice()
        .reverse()
        .map((frame) => {
            const meta = frame.frame_type
                ? `FrameType=${frame.frame_type} DLC=${frame.dlc} Ext=${frame.extern_flag}`
                : "";
            const direction = frame.direction || "TX";
            const matched = frame.matched === true ? " matched" : "";
            return `${direction} DEV${frame.dev} ${frame.label}${matched} ${frame.id} ret=${frame.ret} ${meta}\n${frame.data}`;
        })
        .join("\n\n");
}

// 渲染 17 个归一化关节滑块。
function renderSliders() {
    els.sliders.innerHTML = "";
    state.joints.forEach((value, index) => {
        const row = document.createElement("div");
        row.className = "joint-row";
        row.innerHTML = `
            <label>J${String(index + 1).padStart(2, "0")} ${JOINT_LABELS[index]}</label>
            <input type="range" min="${MANUAL_JOINT_MIN}" max="${MANUAL_JOINT_MAX}" step="1" value="${value}" data-index="${index}">
            <input type="number" min="${MANUAL_JOINT_MIN}" max="${MANUAL_JOINT_MAX}" step="1" value="${value}" data-index="${index}">
        `;
        const range = row.querySelector("input[type='range']");
        const number = row.querySelector("input[type='number']");
        range.addEventListener("input", () => {
            state.joints[index] = Number(range.value);
            number.value = range.value;
            throttledSendJoints();
        });
        number.addEventListener("change", () => {
            const next = clampManualJoint(Number(number.value) || 0);
            state.joints[index] = next;
            number.value = String(next);
            range.value = String(next);
            sendJoints();
        });
        els.sliders.appendChild(row);
    });
}

// 生成前端默认关节值：手腕 J17 默认 50, 侧摆中立值，其余为 0。
function defaultJoints() {
    const joints = Array.from({ length: JOINT_COUNT }, () => 0);
    joints[3]=64;
    joints[11] = 75;joints[12] = 44;joints[13] = 28;
    joints[16] = 50;
    return joints;
}

// 把 Follow 生成的真实关节值反算成前端 0-100 百分比。
function realJointToPercent(index, value) {
    const [lower, upper] = JOINT_RANGES[index];
    if (upper === lower) {
        return 0;
    }
    const percent = ((value - lower) / (upper - lower)) * 100;
    return clampManualJoint(Math.round(percent));
}

// 批量把真实关节值转换为前端百分比。
function realJointsToPercent(values) {
    return values.map((value, index) => realJointToPercent(index, value));
}

// Follow 自动更新关节值后，同步刷新滑块 UI。
function syncSliderValues() {
    for (const input of els.sliders.querySelectorAll("input[data-index]")) {
        const index = Number(input.dataset.index);
        input.value = String(state.joints[index]);
    }
}

function renderPoseRecords() {
    els.poseList.innerHTML = "";
    if (!state.poseRecords.length) {
        els.poseList.innerHTML = `<div class="device-meta">暂无记录</div>`;
    }
    state.poseRecords.forEach((record, index) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = `pose-item ${state.selectedPoseIndex === index ? "active" : ""}`;
        item.textContent = `#${index + 1}`;
        item.title = record.join(" ");
        item.addEventListener("click", () => {
            state.selectedPoseIndex = index;
            renderPoseRecords();
        });
        els.poseList.appendChild(item);
    });
    els.poseOverwriteBtn.disabled = state.selectedPoseIndex < 0;
    els.poseRunBtn.disabled = state.selectedPoseIndex < 0;
    els.poseDeleteBtn.disabled = state.selectedPoseIndex < 0;
    els.poseSaveBtn.disabled = !state.poseRecords.length;
    els.poseStatus.textContent = state.poseRecords.length ? `已记录 ${state.poseRecords.length} 个姿态` : "未记录";
}

function recordPose() {
    state.poseRecords.push(state.joints.slice());
    state.selectedPoseIndex = state.poseRecords.length - 1;
    renderPoseRecords();
}

function overwritePose() {
    if (state.selectedPoseIndex < 0) return;
    state.poseRecords[state.selectedPoseIndex] = state.joints.slice();
    renderPoseRecords();
}

async function runPose() {
    if (state.selectedPoseIndex < 0) return;
    state.joints = state.poseRecords[state.selectedPoseIndex].slice();
    syncSliderValues();
    await sendJoints();
}

function deletePose() {
    if (state.selectedPoseIndex < 0) return;
    state.poseRecords.splice(state.selectedPoseIndex, 1);
    state.selectedPoseIndex = Math.min(state.selectedPoseIndex, state.poseRecords.length - 1);
    renderPoseRecords();
}

async function savePoseSequence() {
    if (!state.poseRecords.length) {
        showError(new Error("请先记录至少一个姿态"));
        return;
    }
    const file = els.poseFileName.value.trim();
    if (!file) {
        showError(new Error("请输入文件名"));
        return;
    }
    try {
        const result = await api("/api/dance/save", { file, frames: state.poseRecords });
        els.poseStatus.textContent = `已保存 ${result.file} · ${result.count} 条`;
        await loadDanceFiles();
    } catch (error) {
        showError(error);
    }
}

// 限制手动输入始终处于 0-100。
function clampManualJoint(value) {
    return Math.max(MANUAL_JOINT_MIN, Math.min(MANUAL_JOINT_MAX, value));
}

// 主动请求摄像头授权，解决部分浏览器枚举设备前 label 为空的问题。
async function requestCameraPermission() {
    if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("当前浏览器不支持摄像头访问");
    }
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    stream.getTracks().forEach((track) => track.stop());
}

// 枚举摄像头设备，首次授权后默认选择第一个设备。
async function loadCameras({ requestPermission = false } = {}) {
    if (!navigator.mediaDevices?.enumerateDevices) {
        els.cameraList.innerHTML = `<div class="device-meta">当前浏览器不支持摄像头枚举</div>`;
        return;
    }

    try {
        if (requestPermission) {
            await requestCameraPermission();
        }
        const devices = await navigator.mediaDevices.enumerateDevices();
        state.cameras = devices.filter((device) => device.kind === "videoinput");
        const selectedExists = state.cameras.some(
            (camera) => camera.deviceId === state.selectedCameraId
        );
        if (!selectedExists) {
            state.selectedCameraId = "";
        }
        if (!state.selectedCameraId && state.cameras.length) {
            state.selectedCameraId = state.cameras[0].deviceId;
        }
        renderCameras();
    } catch (error) {
        els.cameraList.innerHTML = `<div class="device-meta">${error.message}</div>`;
        if (requestPermission) {
            throw error;
        }
    }
}

// 渲染摄像头选择框，保持单选式勾选交互。
function renderCameras() {
    els.cameraList.innerHTML = "";
    if (!state.cameras.length) {
        els.cameraList.innerHTML = `<div class="device-meta">点击启动摄像头后授权访问</div>`;
        return;
    }

    for (const [index, camera] of state.cameras.entries()) {
        const label = camera.label || `摄像头 ${index + 1}`;
        const item = document.createElement("label");
        item.className = "camera-option";
        item.innerHTML = `
            <input type="checkbox" ${state.selectedCameraId === camera.deviceId ? "checked" : ""} data-device-id="${camera.deviceId}">
            <span>${label}</span>
        `;
        item.querySelector("input").addEventListener("change", async (event) => {
            state.selectedCameraId = event.target.checked ? event.target.dataset.deviceId : "";
            renderCameras();
            if (state.gameRunning && state.selectedCameraId) {
                await restartCamera();
            }
        });
        els.cameraList.appendChild(item);
    }
}

let jointTimer = null;
// 手动拖动滑块时节流发送，避免 CAN 总线被 UI 高频事件打满。
function throttledSendJoints() {
    clearTimeout(jointTimer);
    jointTimer = setTimeout(sendJoints, 180);
}

// 清掉尚未触发的手动滑块发送，避免 dance 执行时插入旧姿态。
function clearPendingJointSend() {
    clearTimeout(jointTimer);
    jointTimer = null;
}

// 拉取设备状态、发送日志和运行模式。
async function refreshStatus() {
    try {
        renderDevices(await api("/api/status"));
    } catch (error) {
        setStatus(error.message);
    }
}

// 加载后端 dance 文件列表。
async function loadDanceFiles() {
    try {
        renderDance(await api("/api/dance"));
    } catch (error) {
        showError(error);
    }
}

// 根据后端返回结果更新 dance 文件和状态。
function renderDance(payload) {
    state.danceFiles = payload.files || [];
    if (!state.selectedDanceFile || !state.danceFiles.includes(state.selectedDanceFile)) {
        state.selectedDanceFile = state.danceFiles[0] || "";
    }
    renderDanceFiles();
    renderDanceStatus(payload.status || {});
}

// 渲染 dance 文件单选列表。
function renderDanceFiles() {
    els.danceFileList.innerHTML = "";
    if (!state.danceFiles.length) {
        els.danceFileList.innerHTML = `<div class="device-meta">dance 文件夹暂无文件</div>`;
        return;
    }

    for (const file of state.danceFiles) {
        const item = document.createElement("label");
        item.className = "dance-file-item";
        const displayName = file.replace(/\.txt$/i, "");
        item.innerHTML = `
            <input type="checkbox" ${state.selectedDanceFile === file ? "checked" : ""} data-file="${file}">
            <span title="${file}">${displayName}</span>
        `;
        item.querySelector("input").addEventListener("change", (event) => {
            state.selectedDanceFile = event.target.checked ? event.target.dataset.file : "";
            renderDanceFiles();
        });
        els.danceFileList.appendChild(item);
    }
}

// 渲染 dance 当前执行进度。
function renderDanceStatus(status) {
    state.danceRunning = Boolean(status.running);
    els.runDanceBtn.disabled = state.danceRunning;
    els.stopDanceBtn.disabled = !state.danceRunning;
    const file = status.file || state.selectedDanceFile || "未选择";
    const sent = Number(status.sent || 0);
    els.danceStatus.textContent = state.danceRunning
        ? `${file} 执行中 · 已发送 ${sent} 条`
        : status.message || "未执行";

    if (state.danceRunning && !state.danceStatusTimer) {
        state.danceStatusTimer = window.setInterval(loadDanceFiles, 500);
    }
    if (!state.danceRunning && state.danceStatusTimer) {
        clearInterval(state.danceStatusTimer);
        state.danceStatusTimer = 0;
    }
}

// 读取 dance 数字输入，非法值回退到默认值。
function danceNumber(input, fallback) {
    const value = Number(input.value);
    if (!Number.isFinite(value) || value < 0) {
        input.value = String(fallback);
        return fallback;
    }
    const rounded = Math.floor(value);
    input.value = String(rounded);
    return rounded;
}

// 启动所选 dance 文件。
async function runDance() {
    if (!selectedDevices().length) {
        showError(new Error("请先勾选设备"));
        return;
    }
    if (!state.selectedDanceFile) {
        showError(new Error("请先选择 dance 文件"));
        return;
    }
    try {
        clearPendingJointSend();
        const payload = {
            devices: selectedDevices(),
            file: state.selectedDanceFile,
            loop_count: danceNumber(els.danceLoopCount, 1),
            interval_ms: danceNumber(els.danceIntervalMs, 30)
        };
        renderDance(await api("/api/dance/run", payload));
    } catch (error) {
        showError(error);
    }
}

// 停止当前 dance 执行线程。
async function stopDance() {
    try {
        renderDance(await api("/api/dance/stop", {}));
    } catch (error) {
        showError(error);
    }
}

// 扫描 USB-CANFD 设备。
async function scanDevices() {
    renderDevices(await api("/api/scan", {}));
}

// 打开当前勾选的设备。
async function openSelected() {
    if (!selectedDevices().length) {
        showError(new Error("请先勾选设备"));
        return;
    }
    try {
        await api("/api/open", { devices: selectedDevices() });
        await refreshStatus();
        els.gameResult.textContent = "设备已连接";
    } catch (error) {
        showError(error);
    }
}

// 查询 L30 DeviceInFo，结果会写回设备区的已连接信息。
async function queryL30Info() {
    if (!selectedDevices().length) {
        showError(new Error("请先勾选设备"));
        return;
    }
    try {
        const result = await api("/api/l30/info", { devices: selectedDevices() });
        const lines = (result.results || []).map((item) => {
            if (!item.matched) {
                return `DEV${item.dev} 无应答 · RX ${item.rx_count || 0}`;
            }
            return `DEV${item.dev} ${formatL30DeviceInfo(item.info || {})}`;
        });
        els.gameResult.textContent = lines.length ? lines.join("；") : "未查询到设备信息";
        await refreshStatus();
    } catch (error) {
        showError(error);
    }
}

// 下发全局使能或失能命令。
async function setEnabled(enabled) {
    if (!selectedDevices().length) {
        showError(new Error("请先勾选设备"));
        return;
    }
    try {
        await api("/api/enable", { devices: selectedDevices(), enabled });
        await refreshStatus();
        els.gameResult.textContent = enabled ? "设备已使能" : "设备已失能";
    } catch (error) {
        showError(error);
    }
}

// 下发手动滑块中的 17 个归一化关节值。
async function sendJoints() {
    if (!selectedDevices().length) {
        return;
    }
    try {
        await api("/api/joints", { devices: selectedDevices(), joints: state.joints });
        setStatus("关节已发送");
    } catch (error) {
        showError(error);
    }
}

// 计算两组前端百分比关节之间的最大差值。
function maxJointDeltaPercent(previous, next) {
    if (!previous) {
        return 100;
    }
    return next.reduce((maxDelta, value, index) => {
        return Math.max(maxDelta, Math.abs(value - previous[index]));
    }, 0);
}

// Follow 模式根据关键点计算目标关节并发送。
async function sendFollowJoints(landmarks) {
    const now = Date.now();
    if (state.followSendBusy || now - state.lastFollowSendAt < FOLLOW_SEND_INTERVAL_MS) {
        return;
    }
    state.lastFollowSendAt = now;
    const { positions, debug } = window.L30Follow.buildFollowPositions(state.followMapper, landmarks);
    const nextJoints = realJointsToPercent(positions);
    const maxDelta = maxJointDeltaPercent(state.lastFollowSentJoints, nextJoints);
    state.joints = nextJoints;
    const debugLines = [
        `Follow ${debug.lostHold ? "hold" : "active"}`,
        `thumb: ${debug.thumbTarget || "none"}`,
        `delta: ${maxDelta.toFixed(0)}%`,
        debug.thumbCurl || "",
        debug.thumbRatios || "",
        debug.curlPct || "",
        debug.sidePct || "",
        debug.filter || "",
        `joints: ${state.joints.join(" ")}`
    ].filter(Boolean);
    els.gestureName.textContent = "Follow";
    els.gestureConfidence.textContent = debug.lostHold ? "保持" : "运行中";
    els.debugLines.textContent = debugLines.join("\n");
    const readyDevices = selectedReadyDevices();
    if (!readyDevices.length) {
        els.gameResult.textContent = "Follow 仅预览，请先连接并使能已勾选设备";
        return;
    }
    if (maxDelta < FOLLOW_CHANGE_THRESHOLD_PERCENT) {
        els.gameResult.textContent = `Follow 已截流，变化 ${maxDelta.toFixed(0)}%`;
        return;
    }
    try {
        state.followSendBusy = true;
        await api("/api/joints", {
            devices: readyDevices,
            joints: state.joints,
            require_open: true
        });
        state.lastFollowSentJoints = state.joints.slice();
        syncSliderValues();
        els.gameResult.textContent = "Follow 已发送";
    } catch (error) {
        showError(error);
    } finally {
        state.followSendBusy = false;
    }
}

// 把摄像头画面和 MediaPipe 关键点绘制到 canvas。
function drawResults(results) {
    const canvas = els.outputCanvas;
    const ctx = canvas.getContext("2d");
    canvas.width = els.inputVideo.videoWidth || 640;
    canvas.height = els.inputVideo.videoHeight || 480;
    ctx.save();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(results.image, 0, 0, canvas.width, canvas.height);

    if (results.multiHandLandmarks && window.drawConnectors && window.drawLandmarks) {
        for (const landmarks of results.multiHandLandmarks) {
            window.drawConnectors(ctx, landmarks, window.HAND_CONNECTIONS, { color: "#D19A2F", lineWidth: 3 });
            window.drawLandmarks(ctx, landmarks, { color: "#17765A", lineWidth: 1, radius: 3 });
        }
    }
    ctx.restore();
}

// 处理每一帧手部识别结果，按模式分发到 RPS 或 Follow。
async function onHandsResults(results) {
    state.recognitionBusy = false;
    drawResults(results);
    const now = Date.now();
    if (now - state.lastRecognitionAt < RECOGNITION_INTERVAL_MS) {
        return;
    }
    state.lastRecognitionAt = now;

    const landmarks = results.multiHandLandmarks?.[0];
    if (state.gameMode === "follow") {
        await sendFollowJoints(landmarks);
        return;
    }

    if (!landmarks) {
        els.gestureName.textContent = "未识别";
        els.gestureConfidence.textContent = "0%";
        els.debugLines.textContent = "";
        return;
    }

    const gesture = window.RPSGestureRecognition.recognizeGesture(landmarks);
    if (now - state.lastUiUpdateAt >= 90) {
        state.lastUiUpdateAt = now;
        els.gestureName.textContent = gesture.name;
        els.gestureConfidence.textContent = `${Math.round(gesture.confidence * 100)}%`;
        els.debugLines.textContent = window.RPSGestureRecognition.getDebugLines(gesture).join("\n");
    }

    const accepted = window.RPSGestureRecognition.isAcceptedGesture(gesture);
    if (accepted && gesture.name !== state.lastAcceptedGesture) {
        state.lastAcceptedGesture = gesture.name;
        queueGameAction(gesture.name);
    }
}

// RPS 模式根据玩家手势排队机械手回应动作。
function queueGameAction(gestureName) {
    const responseGesture = RESPONSE_GESTURE[gestureName];
    if (!responseGesture) {
        return;
    }
    state.pendingResponseGesture = responseGesture;
    state.pendingSourceGesture = gestureName;
    els.gameResult.textContent = `识别 ${gestureName}，L30 准备出 ${responseGesture}`;
    void drainGameActionQueue();
}

// 串行执行 RPS 回应，避免连续识别导致动作重入。
async function drainGameActionQueue() {
    if (state.actionInFlight) {
        return;
    }
    state.actionInFlight = true;
    try {
        while (state.pendingResponseGesture) {
            const responseGesture = state.pendingResponseGesture;
            const sourceGesture = state.pendingSourceGesture;
            state.pendingResponseGesture = "";
            state.pendingSourceGesture = "";
            const result = await api("/api/game", { devices: selectedDevices(), gesture: responseGesture });
            els.gameResult.textContent = result.sent
                ? `识别 ${sourceGesture}，L30 出 ${responseGesture}`
                : `识别 ${sourceGesture}，L30 准备出 ${responseGesture}，${result.message}`;
            void refreshStatus();
        }
    } catch (error) {
        showError(error);
    } finally {
        state.actionInFlight = false;
        if (state.pendingResponseGesture) {
            void drainGameActionQueue();
        }
    }
}

// 切换 RPS / Follow 模式，并重置识别状态。
function setGameMode(mode) {
    if (state.gameRunning) {
        return;
    }
    state.gameMode = mode;
    state.followMapper = window.L30Follow.createFollowMapper();
    state.lastFollowSendAt = 0;
    state.lastFollowSentJoints = null;
    state.followSendBusy = false;
    state.lastAcceptedGesture = "";
    state.pendingResponseGesture = "";
    state.pendingSourceGesture = "";
    els.rpsModeBtn.classList.toggle("active", mode === "rps");
    els.followModeBtn.classList.toggle("active", mode === "follow");
    els.gestureName.textContent = mode === "follow" ? "Follow" : "未识别";
    els.gestureConfidence.textContent = mode === "follow" ? "待启动" : "0%";
    els.debugLines.textContent = "";
    els.gameResult.textContent = mode === "follow" ? "Follow 模式待启动" : "等待手势";
}

// 启动摄像头和 MediaPipe Hands。
async function startGame() {
    try {
        await loadCameras({ requestPermission: !state.cameras.length || !state.selectedCameraId });
    } catch (error) {
        showError(error);
        return;
    }
    if (!state.selectedCameraId) {
        els.gameResult.textContent = "请先选择摄像头";
        return;
    }
    if (!window.Hands) {
        els.gameResult.textContent = "MediaPipe 资源未加载";
        return;
    }
    if (!state.hands) {
        state.hands = new window.Hands({
            locateFile: (file) => `/static/vendor/mediapipe/hands/${file}`
        });
        state.hands.setOptions({
            maxNumHands: 1,
            modelComplexity: 1,
            minDetectionConfidence: 0.6,
            minTrackingConfidence: 0.6
        });
        state.hands.onResults(onHandsResults);
    }
    if (state.gameMode === "follow") {
        state.followMapper = window.L30Follow.createFollowMapper();
        state.lastFollowSendAt = 0;
        state.lastFollowSentJoints = null;
        state.followSendBusy = false;
    }
    state.gameRunning = true;
    await restartCamera();
    els.gameBtn.textContent = state.gameMode === "follow" ? "跟随中" : "识别中";
    els.gameBtn.disabled = true;
    els.rpsModeBtn.disabled = true;
    els.followModeBtn.disabled = true;
    els.stopGameBtn.disabled = false;
    els.gameResult.textContent = state.gameMode === "follow" ? "Follow 运行中" : "等待手势";
}

// 摄像头选择变化后重启视频流。
async function restartCamera() {
    const shouldRun = state.gameRunning;
    stopCamera();
    state.gameRunning = shouldRun;
    state.lastAcceptedGesture = "";
    state.lastRecognitionAt = 0;
    state.lastUiUpdateAt = 0;
    state.recognitionBusy = false;

    const videoConstraints = {
        width: { ideal: 960 },
        height: { ideal: 720 }
    };
    if (state.selectedCameraId) {
        videoConstraints.deviceId = { exact: state.selectedCameraId };
    }
    const stream = await navigator.mediaDevices.getUserMedia({
        video: videoConstraints,
        audio: false
    });
    els.inputVideo.srcObject = stream;
    await els.inputVideo.play();
    const [track] = stream.getVideoTracks();
    const deviceId = track?.getSettings?.().deviceId;
    if (deviceId) {
        state.selectedCameraId = deviceId;
    }
    state.camera = {
        stream,
        stop() {
            stream.getTracks().forEach((track) => track.stop());
        }
    };
    await loadCameras();
    queueRecognitionFrame();
}

// 停止当前浏览器摄像头流并清理动画帧。
function stopCamera() {
    state.gameRunning = false;
    if (state.frameRequest) {
        cancelAnimationFrame(state.frameRequest);
        state.frameRequest = 0;
    }
    if (state.camera?.stop) {
        state.camera.stop();
    }
    state.camera = null;
    els.inputVideo.srcObject = null;
}

// 用 requestAnimationFrame 驱动 MediaPipe 帧识别。
function queueRecognitionFrame() {
    state.frameRequest = requestAnimationFrame(async () => {
        if (!state.gameRunning || !state.camera) {
            return;
        }
        const now = Date.now();
        if (!state.recognitionBusy && now - state.lastRecognitionAt >= RECOGNITION_INTERVAL_MS) {
            state.recognitionBusy = true;
            try {
                await state.hands.send({ image: els.inputVideo });
            } catch (error) {
                state.recognitionBusy = false;
                showError(error);
            }
        }
        queueRecognitionFrame();
    });
}

// 停止 Game 区域的摄像头识别。
function stopGame() {
    stopCamera();
    state.recognitionBusy = false;
    state.lastAcceptedGesture = "";
    state.pendingResponseGesture = "";
    state.pendingSourceGesture = "";
    els.gameBtn.textContent = "启动摄像头";
    els.gameBtn.disabled = false;
    els.rpsModeBtn.disabled = false;
    els.followModeBtn.disabled = false;
    els.stopGameBtn.disabled = true;
    els.gestureName.textContent = state.gameMode === "follow" ? "Follow" : "未识别";
    els.gestureConfidence.textContent = state.gameMode === "follow" ? "待启动" : "0%";
    els.debugLines.textContent = "";
    els.gameResult.textContent = "摄像头已关闭";
    const ctx = els.outputCanvas.getContext("2d");
    ctx.clearRect(0, 0, els.outputCanvas.width, els.outputCanvas.height);
}

// 将异常展示到页面状态和结果区域。
function showError(error) {
    const message = error?.message || String(error);
    setStatus("操作失败");
    els.gameResult.textContent = message;
}

els.scanBtn.addEventListener("click", scanDevices);
els.openBtn.addEventListener("click", openSelected);
els.l30InfoBtn.addEventListener("click", queryL30Info);
els.enableBtn.addEventListener("click", () => setEnabled(true));
els.disableBtn.addEventListener("click", () => setEnabled(false));
els.zeroBtn.addEventListener("click", () => {
    state.joints = defaultJoints();
    renderSliders();
    sendJoints();
});
els.sendBtn.addEventListener("click", sendJoints);
els.poseRecordBtn.addEventListener("click", recordPose);
els.poseOverwriteBtn.addEventListener("click", overwritePose);
els.poseRunBtn.addEventListener("click", runPose);
els.poseDeleteBtn.addEventListener("click", deletePose);
els.poseSaveBtn.addEventListener("click", savePoseSequence);
els.refreshDanceBtn.addEventListener("click", loadDanceFiles);
els.runDanceBtn.addEventListener("click", runDance);
els.stopDanceBtn.addEventListener("click", stopDance);
els.rpsModeBtn.addEventListener("click", () => setGameMode("rps"));
els.followModeBtn.addEventListener("click", () => setGameMode("follow"));
els.gameBtn.addEventListener("click", startGame);
els.stopGameBtn.addEventListener("click", stopGame);

renderSliders();
renderPoseRecords();
refreshStatus();
loadDanceFiles();
loadCameras();
