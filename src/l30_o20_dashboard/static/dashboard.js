// 统一 Dashboard：一套页面管理 L30 和 O20，按设备型号分发协议命令。
(() => {
    const L30 = window.L30AppConfig;
    const O20 = window.O20Config;
    const RESPONSE_GESTURE = L30.RESPONSE_GESTURE || { "布": "剪刀", "石头": "布", "剪刀": "石头" };
    const RECOGNITION_INTERVAL_MS = L30.RECOGNITION_INTERVAL_MS || 30;
    const FOLLOW_SEND_INTERVAL_MS = L30.FOLLOW_SEND_INTERVAL_MS || 30;
    const FOLLOW_CHANGE_THRESHOLD_PERCENT = L30.FOLLOW_CHANGE_THRESHOLD_PERCENT || 2;
    const CAMERA_RESOLUTIONS = {
        "240p": { width: 320, height: 240 },
        "480p": { width: 640, height: 480 },
        "720p": { width: 1280, height: 720 },
        "1080p": { width: 1920, height: 1080 }
    };

    const state = {
        devices: [],
        selected: new Set(),
        knownDevices: new Set(),
        profiles: {},
        joints: defaultJoints(),
        poseRecords: [],
        selectedPoseIndex: -1,
        jointTimer: 0,
        dance: {
            l30: { files: [], selected: "", running: false, timer: 0 },
            o20: { files: [], selected: "", running: false, timer: 0 }
        },
        cameras: [],
        selectedCameraId: "",
        cameraResolution: "720p",
        camera: null,
        frameRequest: 0,
        hands: null,
        gameMode: "rps",
        l30FollowMapper: window.L30Follow.createFollowMapper(),
        o20FollowMapper: createO20FollowMapper(),
        lastFollowSendAt: 0,
        lastFollowSent: { l30: null, o20: null },
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

    const els = {};

    function bindElements() {
        for (const id of [
            "runtimeStatus", "deviceList", "scanBtn", "openBtn", "forceOpenBtn", "deviceQueryBtn",
            "enableBtn", "disableBtn", "o20Velocity", "o20VelocityBtn",
            "o20ErrorBtn", "o20ClearErrorBtn", "txLog", "sliders", "zeroBtn", "sendBtn",
            "poseRecordBtn", "poseOverwriteBtn", "poseRunBtn", "poseDeleteBtn", "poseSaveL30Btn",
            "poseSaveO20Btn", "poseList", "poseFileName", "poseStatus", "refreshL30DanceBtn",
            "runL30DanceBtn", "stopL30DanceBtn", "l30DanceFileList", "l30DanceLoopCount",
            "l30DanceIntervalMs", "l30DanceStatus", "refreshO20DanceBtn", "runO20DanceBtn",
            "stopO20DanceBtn", "o20DanceFileList", "o20DanceLoopCount", "o20DanceIntervalMs",
            "o20DanceStatus", "rpsModeBtn", "followModeBtn", "cameraResolution", "gameBtn", "stopGameBtn",
            "cameraList", "inputVideo", "outputCanvas", "gestureName", "gestureConfidence",
            "debugLines", "gameResult"
        ]) {
            els[id] = document.getElementById(id);
        }
    }

    async function api(path, body = null) {
        const options = body
            ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
            : { method: "GET" };
        const response = await fetch(path, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || data.message || `HTTP ${response.status}`);
        }
        return data;
    }

    function defaultJoints() {
        const joints = Array.from({ length: L30.JOINT_COUNT }, () => 0);
        joints[16] = 50;
        return joints;
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, Number(value) || 0));
    }

    function clampPercent(value) {
        return clamp(value, 0, 100);
    }

    function delay(ms) {
        return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    function setStatus(text) {
        els.runtimeStatus.textContent = text;
    }

    function setResult(text) {
        els.gameResult.textContent = text;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function selectedDevices() {
        return Array.from(state.selected).sort((a, b) => a - b);
    }

    function profileFor(dev) {
        const key = String(dev);
        if (!state.profiles[key]) {
            state.profiles[key] = {
                model: "unknown",
                o20DeviceId: 1,
                l30InfoText: "L30 未查询",
                o20InfoText: "O20 未查询",
                detected: {}
            };
        }
        return state.profiles[key];
    }

    function selectedModelDevices(model, { opened = true, enabled = false } = {}) {
        return state.devices
            .filter((device) => state.selected.has(device.dev))
            .filter((device) => profileFor(device.dev).model === model)
            .filter((device) => !opened || device.opened)
            .filter((device) => !enabled || device.enabled)
            .map((device) => device.dev)
            .sort((a, b) => a - b);
    }

    function selectedO20DeviceIdMap(devices = selectedModelDevices("o20")) {
        return Object.fromEntries(devices.map((dev) => [String(dev), Number(profileFor(dev).o20DeviceId) || 1]));
    }

    function handName(deviceId) {
        return Number(deviceId) === 2 ? "左手" : "右手";
    }

    function nodeText(deviceId) {
        return `0x${Number(deviceId).toString(16).padStart(2, "0").toUpperCase()}`;
    }

    function l30DeviceInfoText(info = {}) {
        if (info.product || info.product_code || info.serial_no || info.hand) {
            const product = info.product_code || info.product || "L30";
            const serial = info.serial_label
                ? `序列号 ${info.serial_label}`
                : info.serial_no
                  ? `SN ${info.serial_no}`
                  : "SN -";
            const software = info.software || "-";
            const hardware = info.hardware || "-";
            const hand = info.hand || "左右手未知";
            return `${product} ${hand} · ${serial} · 软件 ${software} · 硬件 ${hardware}`;
        }
        return `${info.type || "USB-CANFD"} ${info.serial || ""} ${info.firmware || ""}`.trim();
    }

    function o20InfoText(info = {}) {
        if (!info || !Object.keys(info).length) return "O20 未收到设备信息";
        return `O20 ${info.hand || "左右手未知"} · 序列号 ${info.serial || "-"} · 软件 ${info.software || "-"} · 硬件 ${info.hardware || "-"}`;
    }

    function applyL30Info(dev, info) {
        const profile = profileFor(dev);
        profile.model = "l30";
        profile.l30InfoText = l30DeviceInfoText(info);
        profile.l30Info = info || {};
    }

    function applyO20Info(dev, deviceId, info) {
        const profile = profileFor(dev);
        profile.model = "o20";
        profile.o20DeviceId = Number(deviceId) || profile.o20DeviceId || 1;
        profile.o20InfoText = o20InfoText(info);
        profile.detected[String(deviceId)] = info || {};
    }

    function parseO20InfoHex(hexText) {
        const bytes = String(hexText || "").match(/[0-9A-Fa-f]{2}/g)?.map((value) => Number.parseInt(value, 16)) || [];
        if (bytes.length < 51) return null;
        const decoder = new TextDecoder("utf-8", { fatal: false });
        const ascii = new TextDecoder("ascii", { fatal: false }).decode(new Uint8Array(bytes)).replace(/\0/g, "");
        const serial = ascii.match(/LHO20-\d{3}-\d{3}-[LR]-[A-Z]-\d-[A-Z]/)?.[0] || "";
        const versions = ascii.match(/\d+\.\d+\.\d/g) || [];
        const text = (start, length) => {
            const slice = bytes.slice(start, start + length);
            const end = slice.indexOf(0);
            return decoder.decode(new Uint8Array(end >= 0 ? slice.slice(0, end) : slice)).trim();
        };
        const handFlag = bytes[50];
        return {
            model: text(0, 10),
            serial: serial || text(10, 20),
            software: versions[0] || text(30, 10),
            hardware: versions[1] || text(40, 10),
            hand: handFlag === 1 ? "右手" : handFlag === 2 ? "左手" : "",
            hand_flag: `0x${handFlag.toString(16).padStart(2, "0").toUpperCase()}`
        };
    }

    function mergeO20InfoFromFrames(frames) {
        for (const frame of frames) {
            if (frame.label !== "o20-info-rx") continue;
            const parsed = parseO20InfoHex(frame.data);
            if (!parsed) continue;
            const replyId = Number.parseInt(String(frame.id || "0").replace(/^0x/i, ""), 16);
            const deviceId = Number.isFinite(replyId) && replyId > 0 ? (replyId >> 21) & 0xff : parsed.hand_flag === "0x02" ? 2 : 1;
            applyO20Info(frame.dev, deviceId, parsed);
        }
    }

    function renderDevices(payload) {
        state.devices = payload.devices || [];
        if (Object.prototype.hasOwnProperty.call(payload, "last_tx")) {
            mergeO20InfoFromFrames(payload.last_tx || []);
        }
        const shouldSelectFirst = state.selected.size === 0 && state.devices.length > 0;
        for (const [index, device] of state.devices.entries()) {
            if (!state.knownDevices.has(device.dev)) {
                if (shouldSelectFirst && index === 0) state.selected.add(device.dev);
                state.knownDevices.add(device.dev);
            }
            const profile = profileFor(device.dev);
            const info = device.info || {};
            if (info.product === "L30" || info.product_id === 0x13) {
                applyL30Info(device.dev, info);
            } else if (profile.l30InfoText === "L30 未查询" && Object.keys(info).length) {
                profile.adapterInfoText = l30DeviceInfoText(info);
            }
        }

        els.deviceList.innerHTML = "";
        if (!state.devices.length) {
            els.deviceList.innerHTML = `<div class="device-meta">未发现设备</div>`;
        }

        for (const device of state.devices) {
            const profile = profileFor(device.dev);
            const item = document.createElement("div");
            item.className = `device-item dashboard-device model-${profile.model}`;
            const modelText = profile.model === "l30" ? "L30" : profile.model === "o20" ? "O20" : "未定";
            const meta = profile.model === "l30"
                ? profile.l30InfoText
                : profile.model === "o20"
                  ? profile.o20InfoText
                  : profile.adapterInfoText || "请执行设备查询，或手动指定型号";
            const o20HandControl = profile.model === "o20" ? `
                        <label class="compact-field o20-hand-field">
                            <span>O20</span>
                            <select class="device-hand-select" data-dev="${device.dev}">
                                <option value="1" ${Number(profile.o20DeviceId) === 1 ? "selected" : ""}>右手 0x01</option>
                                <option value="2" ${Number(profile.o20DeviceId) === 2 ? "selected" : ""}>左手 0x02</option>
                            </select>
                        </label>
            ` : "";
            item.innerHTML = `
                <input type="checkbox" ${state.selected.has(device.dev) ? "checked" : ""} data-dev="${device.dev}">
                <div class="device-body">
                    <div class="device-name">
                        <span>DEV${device.dev} CH${device.ch}</span>
                        <span class="device-badge">${device.opened ? "已连接" : "未连接"}</span>
                        ${device.enabled ? `<span class="device-badge enabled">已使能</span>` : ""}
                    </div>
                    <div class="device-controls">
                        <label class="compact-field">
                            <span>型号</span>
                            <select class="device-model-select" data-dev="${device.dev}">
                                <option value="unknown" ${profile.model === "unknown" ? "selected" : ""}>未定</option>
                                <option value="l30" ${profile.model === "l30" ? "selected" : ""}>L30</option>
                                <option value="o20" ${profile.model === "o20" ? "selected" : ""}>O20</option>
                            </select>
                        </label>
                        ${o20HandControl}
                    </div>
                    <div class="device-meta"><strong>${modelText}</strong> · ${escapeHtml(meta)}</div>
                </div>
            `;
            item.querySelector("input[type='checkbox']").addEventListener("change", (event) => {
                const id = Number(event.target.dataset.dev);
                if (event.target.checked) state.selected.add(id);
                else state.selected.delete(id);
            });
            item.querySelector(".device-model-select").addEventListener("change", (event) => {
                const profile = profileFor(event.target.dataset.dev);
                profile.model = event.target.value;
                setStatus(`DEV${event.target.dataset.dev} 型号设为 ${event.target.value.toUpperCase()}`);
                renderDevices({ devices: state.devices, count: state.devices.length, mock: payload.mock });
            });
            const handSelect = item.querySelector(".device-hand-select");
            if (handSelect) {
                handSelect.addEventListener("change", (event) => {
                    const next = Number(event.target.value) || 1;
                    profileFor(event.target.dataset.dev).o20DeviceId = next;
                    setStatus(`DEV${event.target.dataset.dev} O20 节点设为 ${handName(next)} ${nodeText(next)}`);
                });
            }
            els.deviceList.appendChild(item);
        }

        setStatus(`${payload.mock ? "Mock" : "硬件"} · ${payload.count || 0} 个设备`);
        if (Object.prototype.hasOwnProperty.call(payload, "last_tx")) {
            renderTx(payload.last_tx || []);
        }
    }

    function parsedErrorText(errors) {
        const names = { 1: "过温", 2: "过流", 3: "通讯异常", 4: "未校准" };
        const active = (Array.isArray(errors) ? errors : [])
            .map((value, index) => ({ value: Number(value) || 0, index }))
            .filter((item) => item.value !== 0)
            .map((item) => `J${String(item.index + 1).padStart(2, "0")}=${names[item.value] || item.value}`);
        return active.length ? active.join("，") : "无错误";
    }

    function renderTx(frames) {
        const visibleFrames = frames.filter((frame) => !String(frame.label || "").startsWith("sensor-") && !["o20-info-read", "o20-error-read", "l30-info-read"].includes(frame.label));
        if (!visibleFrames.length) {
            els.txLog.textContent = "暂无有效记录";
            return;
        }
        els.txLog.textContent = visibleFrames
            .slice()
            .reverse()
            .map((frame) => {
                const parsed = frame.parsed || {};
                if (frame.label === "o20-info-rx") {
                    const info = parseO20InfoHex(frame.data);
                    return info ? `RX DEV${frame.dev} O20设备信息 ${frame.id}\n${o20InfoText(info)}` : `RX DEV${frame.dev} O20设备信息 ${frame.id}`;
                }
                if (frame.label === "l30-info-rx" && parsed.device_info) {
                    return `RX DEV${frame.dev} L30设备信息 ${frame.id}\n${l30DeviceInfoText(parsed.device_info)}`;
                }
                if (Array.isArray(parsed.errors)) {
                    return `RX DEV${frame.dev} O20错误状态 ${frame.id}\n${parsedErrorText(parsed.errors)}`;
                }
                if (parsed.status_text) {
                    return `RX DEV${frame.dev} ${frame.label} ${frame.id}\n状态 ${parsed.status || ""} ${parsed.status_text}`;
                }
                const meta = frame.frame_type ? `FrameType=${frame.frame_type} DLC=${frame.dlc} Ext=${frame.extern_flag}` : "";
                const payload = frame.direction === "RX" ? `\n${frame.data}` : "";
                return `${frame.direction || "TX"} DEV${frame.dev} ${frame.label} ${frame.id} ret=${frame.ret} ${meta}${payload}`;
            })
            .join("\n\n");
    }

    function renderSliders() {
        els.sliders.innerHTML = "";
        state.joints.forEach((value, index) => {
            const row = document.createElement("div");
            row.className = `joint-row ${index === 16 ? "l30-only-joint" : ""}`;
            const label = `J${String(index + 1).padStart(2, "0")}`;
            const jointName = L30.JOINT_LABELS[index] || label;
            row.innerHTML = `
                <label title="${escapeHtml(label + " " + jointName)}"><span>${label}</span><strong>${escapeHtml(jointName)}</strong></label>
                <input type="range" min="0" max="100" step="1" value="${value}" data-index="${index}">
                <input type="number" min="0" max="100" step="1" value="${value}" data-index="${index}">
            `;
            const range = row.querySelector("input[type='range']");
            const number = row.querySelector("input[type='number']");
            range.addEventListener("input", () => {
                state.joints[index] = Number(range.value);
                number.value = range.value;
                throttledSendJoints();
            });
            number.addEventListener("change", () => {
                const next = clampPercent(number.value);
                state.joints[index] = next;
                number.value = String(next);
                range.value = String(next);
                void sendDashboardJoints();
            });
            els.sliders.appendChild(row);
        });
    }

    function syncSliderValues() {
        for (const input of els.sliders.querySelectorAll("input[data-index]")) {
            input.value = String(state.joints[Number(input.dataset.index)]);
        }
    }

    function throttledSendJoints() {
        clearTimeout(state.jointTimer);
        state.jointTimer = window.setTimeout(() => void sendDashboardJoints(), 150);
    }

    function clearPendingJointSend() {
        clearTimeout(state.jointTimer);
        state.jointTimer = 0;
    }

    async function refreshStatus() {
        try {
            renderDevices(await api("/api/status"));
        } catch (error) {
            setStatus(error.message);
        }
    }

    async function scanDevices() {
        try {
            renderDevices(await api("/api/scan", {}));
        } catch (error) {
            showError(error);
        }
    }

    async function openSelected(force = false) {
        const devices = selectedDevices();
        if (!devices.length) return showError(new Error("请先勾选设备"));
        try {
            await api("/api/open", { devices, force });
            await refreshStatus();
            setResult(force ? "强制连接已完成，请执行设备查询确认型号" : "设备已连接，请执行设备查询确认型号");
        } catch (error) {
            showError(error);
        }
    }

    async function queryDevices() {
        const devices = selectedDevices();
        if (!devices.length) return showError(new Error("请先勾选设备"));
        try {
            const result = await api("/api/devices/query", { devices });
            const lines = [];
            for (const profile of result.profiles || []) {
                if (profile.model === "o20") {
                    applyO20Info(profile.dev, profile.device_id, profile.info || {});
                    lines.push(`DEV${profile.dev} O20 ${handName(profile.device_id)} ${nodeText(profile.device_id)}`);
                } else if (profile.model === "l30") {
                    applyL30Info(profile.dev, profile.info || {});
                    lines.push(`DEV${profile.dev} L30 ${l30DeviceInfoText(profile.info || {})}`);
                } else {
                    profileFor(profile.dev).model = "unknown";
                    lines.push(`DEV${profile.dev} 未识别`);
                }
            }
            await refreshStatus();
            setResult(lines.length ? `设备查询完成：${lines.join("；")}` : "未查询到设备信息");
        } catch (error) {
            showError(error);
        }
    }

    async function setL30Enabled(enabled) {
        const devices = selectedModelDevices("l30", { opened: true });
        if (!devices.length) return showError(new Error("没有已连接且型号为 L30 的勾选设备"));
        try {
            await api("/api/enable", { devices, enabled });
            await refreshStatus();
            setResult(enabled ? "L30 已使能" : "L30 已失能");
        } catch (error) {
            showError(error);
        }
    }

    async function sendO20Velocity() {
        const devices = selectedModelDevices("o20", { opened: true });
        if (!devices.length) return showError(new Error("没有已连接且型号为 O20 的勾选设备"));
        const percent = clampPercent(els.o20Velocity.value);
        const rawVelocity = Math.round((percent / 100) * (Number(O20.O20_VELOCITY_RAW_MAX) || 32767));
        els.o20Velocity.value = String(percent);
        try {
            await api("/api/o20/velocity", {
                devices,
                velocity: rawVelocity,
                device_ids: selectedO20DeviceIdMap(devices),
                require_open: true
            });
            setResult(`O20 速度已发送：${percent}% -> ${rawVelocity}`);
            await delay(40);
            await refreshStatus();
        } catch (error) {
            showError(error);
        }
    }

    async function queryO20Error() {
        const devices = selectedModelDevices("o20", { opened: true });
        if (!devices.length) return showError(new Error("没有已连接且型号为 O20 的勾选设备"));
        try {
            const result = await api("/api/o20/error", {
                devices,
                device_ids: selectedO20DeviceIdMap(devices)
            });
            const lines = (result.results || []).map((item) => {
                const label = `DEV${item.dev}/${handName(item.device_id)}`;
                return item.matched ? `${label} ${parsedErrorText(item.errors)}` : `${label} 未收到错误状态回传`;
            });
            setResult(lines.join("；") || "未收到错误状态回传");
            await refreshStatus();
        } catch (error) {
            showError(error);
        }
    }

    async function clearO20Error() {
        const devices = selectedModelDevices("o20", { opened: true });
        if (!devices.length) return showError(new Error("没有已连接且型号为 O20 的勾选设备"));
        try {
            await api("/api/o20/error/clear", {
                devices,
                device_ids: selectedO20DeviceIdMap(devices)
            });
            setResult("O20 清除错误指令已发送");
            await delay(40);
            await refreshStatus();
        } catch (error) {
            showError(error);
        }
    }

    async function sendDashboardJoints(custom = null) {
        const joints = custom || state.joints;
        const l30Devices = selectedModelDevices("l30", { opened: true });
        const o20Devices = selectedModelDevices("o20", { opened: true });
        if (!l30Devices.length && !o20Devices.length) {
            setStatus("没有已连接且已指定型号的勾选设备");
            return;
        }
        const tasks = [];
        if (l30Devices.length) {
            tasks.push(api("/api/joints", { devices: l30Devices, joints, require_open: true }));
        }
        if (o20Devices.length) {
            tasks.push(api("/api/o20/joints", {
                devices: o20Devices,
                joints: joints.slice(0, O20.O20_JOINT_COUNT),
                device_ids: selectedO20DeviceIdMap(o20Devices),
                require_open: true
            }));
        }
        try {
            await Promise.all(tasks);
            setStatus(`关节已发送：L30 ${l30Devices.length} · O20 ${o20Devices.length}`);
        } catch (error) {
            showError(error);
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
        els.poseSaveL30Btn.disabled = !state.poseRecords.length;
        els.poseSaveO20Btn.disabled = !state.poseRecords.length;
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
        await sendDashboardJoints();
    }

    function deletePose() {
        if (state.selectedPoseIndex < 0) return;
        state.poseRecords.splice(state.selectedPoseIndex, 1);
        state.selectedPoseIndex = Math.min(state.selectedPoseIndex, state.poseRecords.length - 1);
        renderPoseRecords();
    }

    async function savePoseSequence(product) {
        if (!state.poseRecords.length) return showError(new Error("请先记录至少一个姿态"));
        const file = els.poseFileName.value.trim();
        if (!file) return showError(new Error("请输入文件名"));
        try {
            const frames = product === "o20"
                ? state.poseRecords.map((frame) => frame.slice(0, O20.O20_JOINT_COUNT))
                : state.poseRecords;
            const path = product === "o20" ? "/api/o20/dance/save" : "/api/dance/save";
            const result = await api(path, { file, frames });
            els.poseStatus.textContent = `已保存 ${product.toUpperCase()} ${result.file} · ${result.count} 条`;
            await loadDance(product);
        } catch (error) {
            showError(error);
        }
    }

    function danceElements(product) {
        const prefix = product === "o20" ? "o20" : "l30";
        const pascal = product === "o20" ? "O20" : "L30";
        return {
            list: els[`${prefix}DanceFileList`],
            run: els[`run${pascal}DanceBtn`],
            stop: els[`stop${pascal}DanceBtn`],
            loop: els[`${prefix}DanceLoopCount`],
            interval: els[`${prefix}DanceIntervalMs`],
            status: els[`${prefix}DanceStatus`]
        };
    }

    async function loadDance(product) {
        try {
            const payload = await api(product === "o20" ? "/api/o20/dance" : "/api/dance");
            renderDance(product, payload);
        } catch (error) {
            showError(error);
        }
    }

    async function loadAllDance() {
        await Promise.all([loadDance("l30"), loadDance("o20")]);
    }

    function renderDance(product, payload) {
        const bucket = state.dance[product];
        bucket.files = payload.files || [];
        if (!bucket.selected || !bucket.files.includes(bucket.selected)) {
            bucket.selected = bucket.files[0] || "";
        }
        renderDanceFiles(product);
        renderDanceStatus(product, payload.status || {});
    }

    function renderDanceFiles(product) {
        const bucket = state.dance[product];
        const elements = danceElements(product);
        elements.list.innerHTML = "";
        if (!bucket.files.length) {
            elements.list.innerHTML = `<div class="device-meta">${product.toUpperCase()} 文件夹暂无序列</div>`;
            return;
        }
        for (const file of bucket.files) {
            const item = document.createElement("label");
            item.className = "dance-file-item";
            const displayName = file.replace(/\.txt$/i, "");
            item.innerHTML = `
                <input type="checkbox" ${bucket.selected === file ? "checked" : ""} data-file="${escapeHtml(file)}">
                <span title="${escapeHtml(file)}">${escapeHtml(displayName)}</span>
            `;
            item.querySelector("input").addEventListener("change", (event) => {
                bucket.selected = event.target.checked ? event.target.dataset.file : "";
                renderDanceFiles(product);
            });
            elements.list.appendChild(item);
        }
    }

    function renderDanceStatus(product, status) {
        const bucket = state.dance[product];
        const elements = danceElements(product);
        bucket.running = Boolean(status.running);
        elements.run.disabled = bucket.running;
        elements.stop.disabled = !bucket.running;
        const file = status.file || bucket.selected || "未选择";
        const sent = Number(status.sent || 0);
        const drained = Number(status.rx_drained || 0);
        const drainText = drained ? ` · RX清理 ${drained}` : "";
        elements.status.textContent = bucket.running ? `${file} 执行中 · 已发送 ${sent} 条${drainText}` : status.message || "未执行";
        if (bucket.running && !bucket.timer) {
            bucket.timer = window.setInterval(() => void loadDance(product), 500);
        }
        if (!bucket.running && bucket.timer) {
            clearInterval(bucket.timer);
            bucket.timer = 0;
        }
    }

    function numberInput(input, fallback) {
        const value = Number(input.value);
        const rounded = Number.isFinite(value) && value >= 0 ? Math.floor(value) : fallback;
        input.value = String(rounded);
        return rounded;
    }

    async function runDance(product) {
        const devices = selectedModelDevices(product, { opened: true, enabled: product === "l30" });
        const bucket = state.dance[product];
        const elements = danceElements(product);
        if (!devices.length) return showError(new Error(`没有可执行 ${product.toUpperCase()} dance 的已连接设备`));
        if (!bucket.selected) return showError(new Error(`请先选择 ${product.toUpperCase()} dance 文件`));
        clearPendingJointSend();
        try {
            const payload = {
                devices,
                file: bucket.selected,
                loop_count: numberInput(elements.loop, 1),
                interval_ms: numberInput(elements.interval, 30)
            };
            if (product === "o20") {
                payload.device_ids = selectedO20DeviceIdMap(devices);
            }
            const endpoint = product === "o20" ? "/api/o20/dance/run" : "/api/dance/run";
            renderDance(product, await api(endpoint, payload));
        } catch (error) {
            showError(error);
        }
    }

    async function stopDance(product) {
        try {
            renderDance(product, await api(product === "o20" ? "/api/o20/dance/stop" : "/api/dance/stop", {}));
        } catch (error) {
            showError(error);
        }
    }

    function realJointToPercent(index, value) {
        const [lower, upper] = L30.JOINT_RANGES[index];
        if (upper === lower) return 0;
        return clampPercent(Math.round(((value - lower) / (upper - lower)) * 100));
    }

    function realJointsToPercent(values) {
        return values.map((value, index) => realJointToPercent(index, value));
    }

    function point(landmark) {
        return [landmark?.x || 0, landmark?.y || 0, landmark?.z || 0];
    }

    function sub(a, b) {
        return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
    }

    function dot(a, b) {
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    }

    function norm(v) {
        return Math.hypot(v[0], v[1], v[2]);
    }

    function angleBetween(a, b) {
        const den = norm(a) * norm(b);
        if (!den) return 180;
        return (Math.acos(clamp(dot(a, b) / den, -1, 1)) * 180) / Math.PI;
    }

    function angleAt(points, a, b, c) {
        return angleBetween(sub(points[a], points[b]), sub(points[c], points[b]));
    }

    function mapPercent(value, minValue, maxValue) {
        if (Math.abs(maxValue - minValue) < 1e-8) return 0;
        return clampPercent(((value - minValue) / (maxValue - minValue)) * 100);
    }

    function straightPercent(angle) {
        return mapPercent(angle, 65, 175);
    }

    function rootTipOpen(points, mcp, pip, dip, tip) {
        const rootOpen = straightPercent(angleAt(points, 0, mcp, pip));
        const pipOpen = straightPercent(angleAt(points, mcp, pip, dip));
        const dipOpen = straightPercent(angleAt(points, pip, dip, tip));
        const tipOpen = clampPercent(pipOpen * 0.7 + dipOpen * 0.3);
        return { root: clampPercent(rootOpen * 0.65 + tipOpen * 0.35), tip: tipOpen };
    }

    function spread(points, mcp1, pip1, mcp2, pip2, minAngle, maxAngle) {
        return mapPercent(angleBetween(sub(points[pip1], points[mcp1]), sub(points[pip2], points[mcp2])), minAngle, maxAngle);
    }

    function sidePercent(percent, sign) {
        return sign > 0 ? 50 + percent / 2 : 50 - percent / 2;
    }

    function createO20FollowMapper() {
        return { previous: defaultO20Joints(), lastGood: defaultO20Joints(), lostCount: 0 };
    }

    function defaultO20Joints() {
        return Array.from({ length: O20.O20_JOINT_COUNT }, (_value, index) => {
            const [lower, upper] = O20.O20_JOINT_RANGES[index];
            return lower < 0 && upper > 0 ? Math.round(((0 - lower) / (upper - lower)) * 100) : 0;
        });
    }

    function smoothO20Follow(mapper, next) {
        const smoothed = next.map((value, index) => {
            const previous = mapper.previous[index] ?? value;
            const alpha = Math.abs(value - previous) > 20 ? 0.82 : 0.48;
            const out = Math.round(alpha * value + (1 - alpha) * previous);
            mapper.previous[index] = out;
            return out;
        });
        mapper.lastGood = smoothed.slice();
        return smoothed;
    }

    function buildO20FollowJoints(mapper, landmarks) {
        if (!landmarks) {
            mapper.lostCount += 1;
            return { joints: mapper.lostCount <= 8 ? mapper.lastGood.slice() : defaultO20Joints(), debug: { lost: true } };
        }
        mapper.lostCount = 0;
        const points = landmarks.map(point);
        const joints = defaultO20Joints();
        const index = rootTipOpen(points, 5, 6, 7, 8);
        const middle = rootTipOpen(points, 9, 10, 11, 12);
        const ring = rootTipOpen(points, 13, 14, 15, 16);
        const pinky = rootTipOpen(points, 17, 18, 19, 20);
        const thumb = rootTipOpen(points, 2, 2, 3, 4);
        joints[0] = thumb.root;
        joints[1] = thumb.tip;
        joints[2] = 50;
        joints[3] = 50;
        joints[4] = sidePercent(spread(points, 5, 6, 9, 10, 2, 18), 1);
        joints[5] = index.root;
        joints[6] = index.tip;
        joints[7] = 50;
        joints[8] = middle.root;
        joints[9] = middle.tip;
        joints[10] = sidePercent(spread(points, 13, 14, 9, 10, 2, 15), -1);
        joints[11] = ring.root;
        joints[12] = ring.tip;
        joints[13] = sidePercent(spread(points, 17, 18, 13, 14, 2, 20), -1);
        joints[14] = pinky.root;
        joints[15] = pinky.tip;
        const output = smoothO20Follow(mapper, joints.map((value) => Math.round(clampPercent(value))));
        return {
            joints: output,
            debug: {
                lost: false,
                curl: `O20 I ${index.root.toFixed(0)}/${index.tip.toFixed(0)} M ${middle.root.toFixed(0)}/${middle.tip.toFixed(0)} R ${ring.root.toFixed(0)}/${ring.tip.toFixed(0)} P ${pinky.root.toFixed(0)}/${pinky.tip.toFixed(0)}`
            }
        };
    }

    function maxJointDelta(previous, next) {
        if (!previous) return 100;
        return next.reduce((max, value, index) => Math.max(max, Math.abs(value - previous[index])), 0);
    }

    async function requestCameraPermission() {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("当前浏览器不支持摄像头访问");
        const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        stream.getTracks().forEach((track) => track.stop());
    }

    async function loadCameras({ requestPermission = false } = {}) {
        if (!navigator.mediaDevices?.enumerateDevices) {
            els.cameraList.innerHTML = `<div class="device-meta">当前浏览器不支持摄像头枚举</div>`;
            return;
        }
        try {
            if (requestPermission) await requestCameraPermission();
            const devices = await navigator.mediaDevices.enumerateDevices();
            state.cameras = devices.filter((device) => device.kind === "videoinput");
            if (!state.cameras.some((camera) => camera.deviceId === state.selectedCameraId)) state.selectedCameraId = "";
            if (!state.selectedCameraId && state.cameras.length) state.selectedCameraId = state.cameras[0].deviceId;
            renderCameras();
        } catch (error) {
            els.cameraList.innerHTML = `<div class="device-meta">${escapeHtml(error.message)}</div>`;
            if (requestPermission) throw error;
        }
    }

    function renderCameras() {
        els.cameraList.innerHTML = "";
        if (!state.cameras.length) {
            els.cameraList.innerHTML = `<div class="device-meta">点击启动摄像头后授权访问</div>`;
            return;
        }
        state.cameras.forEach((camera, index) => {
            const item = document.createElement("label");
            item.className = "camera-option";
            item.innerHTML = `
                <input type="checkbox" ${state.selectedCameraId === camera.deviceId ? "checked" : ""} data-device-id="${camera.deviceId}">
                <span>${escapeHtml(camera.label || `摄像头 ${index + 1}`)}</span>
            `;
            item.querySelector("input").addEventListener("change", async (event) => {
                state.selectedCameraId = event.target.checked ? event.target.dataset.deviceId : "";
                renderCameras();
                if (state.gameRunning && state.selectedCameraId) await restartCamera();
            });
            els.cameraList.appendChild(item);
        });
    }

    function drawResults(results) {
        const canvas = els.outputCanvas;
        const ctx = canvas.getContext("2d");
        canvas.width = els.inputVideo.videoWidth || 640;
        canvas.height = els.inputVideo.videoHeight || 480;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(results.image, 0, 0, canvas.width, canvas.height);
        if (results.multiHandLandmarks && window.drawConnectors && window.drawLandmarks) {
            for (const landmarks of results.multiHandLandmarks) {
                window.drawConnectors(ctx, landmarks, window.HAND_CONNECTIONS, { color: "#D19A2F", lineWidth: 3 });
                window.drawLandmarks(ctx, landmarks, { color: "#17765A", lineWidth: 1, radius: 3 });
            }
        }
    }

    async function sendFollowJoints(landmarks) {
        const now = Date.now();
        if (state.followSendBusy || now - state.lastFollowSendAt < FOLLOW_SEND_INTERVAL_MS) return;
        state.lastFollowSendAt = now;

        const l30Follow = window.L30Follow.buildFollowPositions(state.l30FollowMapper, landmarks);
        const l30Joints = realJointsToPercent(l30Follow.positions);
        const o20Follow = buildO20FollowJoints(state.o20FollowMapper, landmarks);
        const o20Joints = o20Follow.joints;
        const l30Delta = maxJointDelta(state.lastFollowSent.l30, l30Joints);
        const o20Delta = maxJointDelta(state.lastFollowSent.o20, o20Joints);

        const l30Ready = selectedModelDevices("l30", { opened: true, enabled: true });
        const o20Ready = selectedModelDevices("o20", { opened: true });
        state.joints = l30Ready.length || !o20Ready.length ? l30Joints : [...o20Joints, state.joints[16] ?? 50];
        syncSliderValues();

        els.gestureName.textContent = "Follow";
        els.gestureConfidence.textContent = landmarks ? "运行中" : "保持";
        els.debugLines.textContent = [
            `L30 delta ${l30Delta.toFixed(0)}% · O20 delta ${o20Delta.toFixed(0)}%`,
            `L30 thumb ${l30Follow.debug.thumbTarget || "none"}`,
            o20Follow.debug.curl || "",
            l30Follow.debug.filter || ""
        ].filter(Boolean).join("\n");

        const tasks = [];
        if (l30Ready.length && l30Delta >= FOLLOW_CHANGE_THRESHOLD_PERCENT) {
            tasks.push(api("/api/joints", { devices: l30Ready, joints: l30Joints, require_open: true }).then(() => { state.lastFollowSent.l30 = l30Joints.slice(); return "L30"; }));
        }
        if (o20Ready.length && o20Delta >= FOLLOW_CHANGE_THRESHOLD_PERCENT) {
            tasks.push(api("/api/o20/joints", {
                devices: o20Ready,
                joints: o20Joints,
                device_ids: selectedO20DeviceIdMap(o20Ready),
                require_open: true
            }).then(() => { state.lastFollowSent.o20 = o20Joints.slice(); return "O20"; }));
        }
        if (!l30Ready.length && !o20Ready.length) {
            setResult("Follow 仅预览，请连接并指定 L30/O20 型号；L30 需要使能");
            return;
        }
        if (!tasks.length) {
            setResult(`Follow 已截流：L30 ${l30Delta.toFixed(0)}% · O20 ${o20Delta.toFixed(0)}%`);
            return;
        }
        try {
            state.followSendBusy = true;
            const sent = await Promise.all(tasks);
            setResult(`Follow 已发送：${sent.join(" + ")}`);
        } catch (error) {
            showError(error);
        } finally {
            state.followSendBusy = false;
        }
    }

    async function onHandsResults(results) {
        state.recognitionBusy = false;
        drawResults(results);
        const now = Date.now();
        if (now - state.lastRecognitionAt < RECOGNITION_INTERVAL_MS) return;
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
        if (window.RPSGestureRecognition.isAcceptedGesture(gesture) && gesture.name !== state.lastAcceptedGesture) {
            state.lastAcceptedGesture = gesture.name;
            queueGameAction(gesture.name);
        }
    }

    function queueGameAction(gestureName) {
        const responseGesture = RESPONSE_GESTURE[gestureName];
        if (!responseGesture) return;
        state.pendingResponseGesture = responseGesture;
        state.pendingSourceGesture = gestureName;
        setResult(`识别 ${gestureName}，准备出 ${responseGesture}`);
        void drainGameActionQueue();
    }

    async function drainGameActionQueue() {
        if (state.actionInFlight) return;
        state.actionInFlight = true;
        try {
            while (state.pendingResponseGesture) {
                const responseGesture = state.pendingResponseGesture;
                const sourceGesture = state.pendingSourceGesture;
                state.pendingResponseGesture = "";
                state.pendingSourceGesture = "";
                const l30Devices = selectedModelDevices("l30", { opened: true, enabled: true });
                const o20Devices = selectedModelDevices("o20", { opened: true });
                const tasks = [];
                if (l30Devices.length) tasks.push(api("/api/game", { devices: l30Devices, gesture: responseGesture }).then(() => "L30"));
                if (o20Devices.length) {
                    tasks.push(api("/api/o20/game", {
                        devices: o20Devices,
                        device_ids: selectedO20DeviceIdMap(o20Devices),
                        gesture: responseGesture
                    }).then(() => "O20"));
                }
                if (!tasks.length) {
                    setResult("没有可执行 RPS 的设备：L30 需要连接并使能，O20 需要连接并指定型号");
                    continue;
                }
                const sent = await Promise.all(tasks);
                setResult(`识别 ${sourceGesture}，${sent.join(" + ")} 出 ${responseGesture}`);
                void refreshStatus();
            }
        } catch (error) {
            showError(error);
        } finally {
            state.actionInFlight = false;
            if (state.pendingResponseGesture) void drainGameActionQueue();
        }
    }

    function setGameMode(mode) {
        if (state.gameRunning) return;
        state.gameMode = mode;
        state.l30FollowMapper = window.L30Follow.createFollowMapper();
        state.o20FollowMapper = createO20FollowMapper();
        state.lastFollowSent = { l30: null, o20: null };
        state.lastAcceptedGesture = "";
        state.pendingResponseGesture = "";
        state.pendingSourceGesture = "";
        els.rpsModeBtn.classList.toggle("active", mode === "rps");
        els.followModeBtn.classList.toggle("active", mode === "follow");
        els.gestureName.textContent = mode === "follow" ? "Follow" : "未识别";
        els.gestureConfidence.textContent = mode === "follow" ? "待启动" : "0%";
        els.debugLines.textContent = "";
        setResult(mode === "follow" ? "Follow 模式待启动" : "等待手势");
    }

    function selectedCameraConstraints() {
        const preset = CAMERA_RESOLUTIONS[state.cameraResolution] || CAMERA_RESOLUTIONS["720p"];
        const constraints = {
            width: { ideal: preset.width },
            height: { ideal: preset.height }
        };
        if (state.selectedCameraId) {
            constraints.deviceId = { exact: state.selectedCameraId };
        }
        return constraints;
    }

    async function startGame() {
        try {
            await loadCameras({ requestPermission: !state.cameras.length || !state.selectedCameraId });
            if (!state.selectedCameraId) return setResult("请先选择摄像头");
            if (!window.Hands) return setResult("MediaPipe 资源未加载");
            if (!state.hands) {
                state.hands = new window.Hands({ locateFile: (file) => `/static/vendor/mediapipe/hands/${file}` });
                state.hands.setOptions({ maxNumHands: 1, modelComplexity: 1, minDetectionConfidence: 0.6, minTrackingConfidence: 0.6 });
                state.hands.onResults(onHandsResults);
            }
            state.gameRunning = true;
            if (state.gameMode === "follow") {
                state.l30FollowMapper = window.L30Follow.createFollowMapper();
                state.o20FollowMapper = createO20FollowMapper();
                state.lastFollowSent = { l30: null, o20: null };
                state.followSendBusy = false;
            }
            await restartCamera();
            els.gameBtn.textContent = state.gameMode === "follow" ? "跟随中" : "识别中";
            els.gameBtn.disabled = true;
            els.rpsModeBtn.disabled = true;
            els.followModeBtn.disabled = true;
            els.stopGameBtn.disabled = false;
            setResult(state.gameMode === "follow" ? "Follow 运行中" : "等待手势");
        } catch (error) {
            showError(error);
        }
    }

    async function restartCamera() {
        const shouldRun = state.gameRunning;
        stopCamera();
        state.gameRunning = shouldRun;
        state.lastAcceptedGesture = "";
        state.lastRecognitionAt = 0;
        state.lastUiUpdateAt = 0;
        state.recognitionBusy = false;
        const stream = await navigator.mediaDevices.getUserMedia({
            video: selectedCameraConstraints(),
            audio: false
        });
        els.inputVideo.srcObject = stream;
        await els.inputVideo.play();
        const deviceId = stream.getVideoTracks()[0]?.getSettings?.().deviceId;
        if (deviceId) state.selectedCameraId = deviceId;
        state.camera = { stream, stop: () => stream.getTracks().forEach((track) => track.stop()) };
        await loadCameras();
        queueRecognitionFrame();
    }

    function stopCamera() {
        state.gameRunning = false;
        if (state.frameRequest) cancelAnimationFrame(state.frameRequest);
        state.frameRequest = 0;
        if (state.camera?.stop) state.camera.stop();
        state.camera = null;
        els.inputVideo.srcObject = null;
    }

    function queueRecognitionFrame() {
        state.frameRequest = requestAnimationFrame(async () => {
            if (!state.gameRunning || !state.camera) return;
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
        setResult("摄像头已关闭");
        els.outputCanvas.getContext("2d").clearRect(0, 0, els.outputCanvas.width, els.outputCanvas.height);
    }

    function showError(error) {
        const message = error?.message || String(error);
        setStatus("操作失败");
        setResult(message);
    }

    function bindEvents() {
        els.scanBtn.addEventListener("click", scanDevices);
        els.openBtn.addEventListener("click", () => void openSelected(false));
        els.forceOpenBtn.addEventListener("click", () => void openSelected(true));
        els.deviceQueryBtn.addEventListener("click", queryDevices);
        els.enableBtn.addEventListener("click", () => void setL30Enabled(true));
        els.disableBtn.addEventListener("click", () => void setL30Enabled(false));
        els.o20VelocityBtn.addEventListener("click", sendO20Velocity);
        els.o20ErrorBtn.addEventListener("click", queryO20Error);
        els.o20ClearErrorBtn.addEventListener("click", clearO20Error);
        els.zeroBtn.addEventListener("click", () => {
            state.joints = defaultJoints();
            renderSliders();
            void sendDashboardJoints();
        });
        els.sendBtn.addEventListener("click", () => void sendDashboardJoints());
        els.poseRecordBtn.addEventListener("click", recordPose);
        els.poseOverwriteBtn.addEventListener("click", overwritePose);
        els.poseRunBtn.addEventListener("click", runPose);
        els.poseDeleteBtn.addEventListener("click", deletePose);
        els.poseSaveL30Btn.addEventListener("click", () => void savePoseSequence("l30"));
        els.poseSaveO20Btn.addEventListener("click", () => void savePoseSequence("o20"));
        els.refreshL30DanceBtn.addEventListener("click", () => void loadDance("l30"));
        els.runL30DanceBtn.addEventListener("click", () => void runDance("l30"));
        els.stopL30DanceBtn.addEventListener("click", () => void stopDance("l30"));
        els.refreshO20DanceBtn.addEventListener("click", () => void loadDance("o20"));
        els.runO20DanceBtn.addEventListener("click", () => void runDance("o20"));
        els.stopO20DanceBtn.addEventListener("click", () => void stopDance("o20"));
        els.rpsModeBtn.addEventListener("click", () => setGameMode("rps"));
        els.followModeBtn.addEventListener("click", () => setGameMode("follow"));
        els.gameBtn.addEventListener("click", startGame);
        els.cameraResolution.addEventListener("change", async (event) => {
            state.cameraResolution = event.target.value || "720p";
            if (state.gameRunning && state.selectedCameraId) {
                await restartCamera();
            }
        });
        els.stopGameBtn.addEventListener("click", stopGame);
    }

    function init() {
        bindElements();
        bindEvents();
        renderSliders();
        renderPoseRecords();
        void refreshStatus();
        void loadAllDance();
        void loadCameras();
    }

    window.addEventListener("DOMContentLoaded", init);
})();
