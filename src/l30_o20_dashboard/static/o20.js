// O20 独立控制台：只加载 O20 页面需要的扫描、连接和寄存器写入逻辑。
(() => {
    const { O20_JOINT_COUNT, O20_JOINT_LABELS, O20_JOINT_RANGES, O20_VELOCITY_RAW_MAX } = window.O20Config;
    const state = {
        devices: [],
        selected: new Set(),
        knownDevices: new Set(),
        deviceProfiles: {},
        joints: defaultO20Joints(),
        poseRecords: [],
        selectedPoseIndex: -1,
        timer: null,
        danceFiles: [],
        selectedDanceFile: "",
        danceRunning: false,
        danceStatusTimer: 0,
        cameras: [],
        selectedCameraId: "",
        camera: null,
        frameRequest: 0,
        hands: null,
        gameMode: "rps",
        followMapper: createO20FollowMapper(),
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
    const RESPONSE_GESTURE = { "布": "剪刀", "石头": "布", "剪刀": "石头" };
    const RECOGNITION_INTERVAL_MS = 30;
    const FOLLOW_SEND_INTERVAL_MS = 30;
    const FOLLOW_CHANGE_THRESHOLD_PERCENT = 2;
    const els = {};

    function bindElements() {
        els.runtimeStatus = document.getElementById("runtimeStatus");
        els.deviceList = document.getElementById("deviceList");
        els.scanBtn = document.getElementById("scanBtn");
        els.openBtn = document.getElementById("openBtn");
        els.txLog = document.getElementById("txLog");
        els.o20Sliders = document.getElementById("o20Sliders");
        els.o20Velocity = document.getElementById("o20Velocity");
        els.o20InfoBtn = document.getElementById("o20InfoBtn");
        els.o20ErrorBtn = document.getElementById("o20ErrorBtn");
        els.o20ClearErrorBtn = document.getElementById("o20ClearErrorBtn");
        els.o20VelocityBtn = document.getElementById("o20VelocityBtn");
        els.o20ZeroBtn = document.getElementById("o20ZeroBtn");
        els.o20Status = document.getElementById("o20Status");
        els.poseRecordBtn = document.getElementById("poseRecordBtn");
        els.poseOverwriteBtn = document.getElementById("poseOverwriteBtn");
        els.poseRunBtn = document.getElementById("poseRunBtn");
        els.poseDeleteBtn = document.getElementById("poseDeleteBtn");
        els.poseSaveBtn = document.getElementById("poseSaveBtn");
        els.poseList = document.getElementById("poseList");
        els.poseFileName = document.getElementById("poseFileName");
        els.poseStatus = document.getElementById("poseStatus");
        els.refreshDanceBtn = document.getElementById("refreshDanceBtn");
        els.runDanceBtn = document.getElementById("runDanceBtn");
        els.stopDanceBtn = document.getElementById("stopDanceBtn");
        els.danceFileList = document.getElementById("danceFileList");
        els.danceLoopCount = document.getElementById("danceLoopCount");
        els.danceIntervalMs = document.getElementById("danceIntervalMs");
        els.danceStatus = document.getElementById("danceStatus");
        els.rpsModeBtn = document.getElementById("rpsModeBtn");
        els.followModeBtn = document.getElementById("followModeBtn");
        els.gameBtn = document.getElementById("gameBtn");
        els.stopGameBtn = document.getElementById("stopGameBtn");
        els.cameraList = document.getElementById("cameraList");
        els.inputVideo = document.getElementById("inputVideo");
        els.outputCanvas = document.getElementById("outputCanvas");
        els.gestureName = document.getElementById("gestureName");
        els.gestureConfidence = document.getElementById("gestureConfidence");
        els.debugLines = document.getElementById("debugLines");
        els.gameResult = document.getElementById("gameResult");
    }

    function defaultO20Joints() {
        return Array.from({ length: O20_JOINT_COUNT }, (_value, index) => {
            const [lower, upper] = O20_JOINT_RANGES[index];
            return lower < 0 && upper > 0 ? Math.round(((0 - lower) / (upper - lower)) * 100) : 0;
        });
    }

    function createO20FollowMapper() {
        return {
            previous: defaultO20Joints(),
            lastGood: defaultO20Joints(),
            lostCount: 0
        };
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, Number(value) || 0));
    }

    function delay(ms) {
        return new Promise((resolve) => window.setTimeout(resolve, ms));
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
        return clamp(((value - minValue) / (maxValue - minValue)) * 100, 0, 100);
    }

    function straightPercent(angle) {
        return mapPercent(angle, 65, 175);
    }

    function rootTipOpen(points, mcp, pip, dip, tip) {
        const rootOpen = straightPercent(angleAt(points, 0, mcp, pip));
        const pipOpen = straightPercent(angleAt(points, mcp, pip, dip));
        const dipOpen = straightPercent(angleAt(points, pip, dip, tip));
        return {
            root: clamp(rootOpen * 0.65 + (pipOpen * 0.7 + dipOpen * 0.3) * 0.35, 0, 100),
            tip: clamp(pipOpen * 0.7 + dipOpen * 0.3, 0, 100)
        };
    }

    function spread(points, mcp1, pip1, mcp2, pip2, minAngle, maxAngle) {
        return mapPercent(angleBetween(sub(points[pip1], points[mcp1]), sub(points[pip2], points[mcp2])), minAngle, maxAngle);
    }

    function sidePercent(percent, sign) {
        return sign > 0 ? 50 + percent / 2 : 50 - percent / 2;
    }

    function smoothFollow(mapper, next) {
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
            return {
                joints: mapper.lostCount <= 8 ? mapper.lastGood.slice() : defaultO20Joints(),
                debug: { lost: true }
            };
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
        const output = smoothFollow(mapper, joints.map((value) => Math.round(clamp(value, 0, 100))));
        return {
            joints: output,
            debug: {
                lost: false,
                curl: `I ${index.root.toFixed(0)}/${index.tip.toFixed(0)} M ${middle.root.toFixed(0)}/${middle.tip.toFixed(0)} R ${ring.root.toFixed(0)}/${ring.tip.toFixed(0)} P ${pinky.root.toFixed(0)}/${pinky.tip.toFixed(0)}`
            }
        };
    }

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

    function selectedDevices() {
        return Array.from(state.selected).sort((a, b) => a - b);
    }

    function selectedOpenDevices() {
        return state.devices
            .filter((device) => state.selected.has(device.dev) && device.opened)
            .map((device) => device.dev)
            .sort((a, b) => a - b);
    }

    function selectedDeviceIdMap() {
        return Object.fromEntries(selectedDevices().map((dev) => [String(dev), Number(profileFor(dev).deviceId) || 1]));
    }

    function setO20Status(message) {
        if (els.o20Status) {
            els.o20Status.textContent = message;
        } else {
            els.runtimeStatus.textContent = message;
        }
    }

    function setGameResult(message) {
        if (els.gameResult) {
            els.gameResult.textContent = message;
        } else {
            els.runtimeStatus.textContent = message;
        }
    }

    function profileFor(dev) {
        const key = String(dev);
        if (!state.deviceProfiles[key]) {
            state.deviceProfiles[key] = {
                deviceId: 1,
                detected: {},
                lastInfoText: "未探测"
            };
        }
        return state.deviceProfiles[key];
    }

    function handName(deviceId) {
        return Number(deviceId) === 2 ? "左手" : "右手";
    }

    function nodeText(deviceId) {
        return `0x${Number(deviceId).toString(16).padStart(2, "0").toUpperCase()}`;
    }

    function velocityPercentToRaw(value) {
        const maxRaw = Number(O20_VELOCITY_RAW_MAX) || 32767;
        const percent = Math.max(0, Math.min(100, Number(value) || 0));
        return Math.round((percent / 100) * maxRaw);
    }

    function infoText(info) {
        if (!info || !Object.keys(info).length) return "未收到设备信息";
        return `序列号 ${info.serial || "-"} · 软件 ${info.software || "-"} · 硬件 ${info.hardware || "-"}`;
    }

    function fullInfoText(info, queryId = "", replyId = "") {
        if (!info || !Object.keys(info).length) return "未收到设备信息";
        const parts = [
            queryId ? `查询 ${queryId}` : "",
            replyId ? `回复 ${replyId}` : "",
            `产品型号 ${info.model || "-"}`,
            infoText(info),
            `左右手标志 ${info.hand_flag || "-"}${info.hand ? `，${info.hand}` : ""}`
        ].filter(Boolean);
        return parts.join(" · ");
    }

    function errorText(errors) {
        const names = {
            1: "过温",
            2: "过流",
            3: "通讯异常",
            4: "未校准"
        };
        const values = Array.isArray(errors) ? errors : [];
        const active = values
            .map((value, index) => ({ value: Number(value) || 0, index }))
            .filter((item) => item.value !== 0)
            .map((item) => `J${String(item.index + 1).padStart(2, "0")}=${names[item.value] || item.value}`);
        return active.length ? active.join("，") : "无错误";
    }

    function parsedRxText(frame) {
        const parsed = frame.parsed || {};
        if (Array.isArray(parsed.errors)) {
            return `RX DEV${frame.dev} 错误状态 ${frame.id} · ${errorText(parsed.errors)}`;
        }
        if (Array.isArray(parsed.values)) {
            return `RX DEV${frame.dev} ${parsed.register || ""} ${frame.id}\n${parsed.values.join(" ")}`;
        }
        return "";
    }

    function parseO20InfoHex(hexText) {
        const bytes = String(hexText || "").match(/[0-9A-Fa-f]{2}/g)?.map((value) => Number.parseInt(value, 16)) || [];
        if (bytes.length < 51) return null;
        const ascii = new TextDecoder("ascii", { fatal: false }).decode(new Uint8Array(bytes)).replace(/\0/g, "");
        const serial = ascii.match(/LHO20-\d{3}-\d{3}-[LR]-[A-Z]-\d-[A-Z]/)?.[0] || "";
        const versions = ascii.match(/\d+\.\d+\.\d/g) || [];
        const text = (start, length) => {
            const slice = bytes.slice(start, start + length);
            const end = slice.indexOf(0);
            const used = end >= 0 ? slice.slice(0, end) : slice;
            return new TextDecoder("utf-8", { fatal: false }).decode(new Uint8Array(used)).trim();
        };
        const handFlag = bytes[50];
        return {
            model: text(0, 10),
            serial: serial || text(10, 20),
            software: versions[0] || text(30, 10),
            hardware: versions[1] || text(40, 10),
            hand: handFlag === 1 ? "右手" : handFlag === 2 ? "左手" : "",
            hand_flag: `0x${handFlag.toString(16).padStart(2, "0").toUpperCase()}`,
            uid: bytes.slice(51, 63).map((value) => value.toString(16).padStart(2, "0").toUpperCase()).join(" ")
        };
    }

    function renderDevices(payload) {
        state.devices = payload.devices || [];
        if (Object.prototype.hasOwnProperty.call(payload, "last_tx")) {
            mergeO20InfoFromFrames(payload.last_tx || []);
        }
        const shouldSelectFirst = state.selected.size === 0 && state.devices.length > 0;
        for (const [index, dev] of state.devices.entries()) {
            if (!state.knownDevices.has(dev.dev)) {
                if (shouldSelectFirst && index === 0) {
                    state.selected.add(dev.dev);
                }
                state.knownDevices.add(dev.dev);
            }
            profileFor(dev.dev);
        }
        els.deviceList.innerHTML = "";
        if (!state.devices.length) {
            els.deviceList.innerHTML = `<div class="device-meta">未发现设备</div>`;
        }
        for (const dev of state.devices) {
            const info = dev.info || {};
            const profile = profileFor(dev.dev);
            const item = document.createElement("div");
            item.className = "device-item";
            item.innerHTML = `
                <input type="checkbox" ${state.selected.has(dev.dev) ? "checked" : ""} data-dev="${dev.dev}">
                <div>
                    <div class="device-name">
                        DEV${dev.dev} CH${dev.ch} ${dev.opened ? "已连接" : "未连接"}
                        <select class="device-hand-select" data-dev="${dev.dev}">
                            <option value="1" ${Number(profile.deviceId) === 1 ? "selected" : ""}>右手 0x01</option>
                            <option value="2" ${Number(profile.deviceId) === 2 ? "selected" : ""}>左手 0x02</option>
                        </select>
                    </div>
                    <div class="device-meta">${profile.lastInfoText === "未探测" ? `${info.type || "USB-CANFD"} ${info.serial || ""} ${info.firmware || ""}` : profile.lastInfoText}</div>
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
            item.querySelector("select").addEventListener("change", (event) => {
                const next = Number(event.target.value) || 1;
                profileFor(event.target.dataset.dev).deviceId = next;
                setO20Status(`DEV${event.target.dataset.dev} 已设置为 ${handName(next)} ${nodeText(next)}`);
            });
            els.deviceList.appendChild(item);
        }
        els.runtimeStatus.textContent = `${payload.mock ? "Mock" : "硬件"} · ${payload.count || 0} 个设备`;
        if (Object.prototype.hasOwnProperty.call(payload, "last_tx")) {
            renderTx(payload.last_tx || []);
        }
    }

    function applyDeviceInfo(dev, deviceId, info) {
        const profile = profileFor(dev);
        profile.detected[String(deviceId)] = info;
        profile.lastInfoText = infoText(info);
        profile.deviceId = Number(deviceId) || profile.deviceId;
    }

    function mergeO20InfoFromFrames(frames) {
        for (const frame of frames) {
            if (frame.label !== "o20-info-rx") continue;
            const parsed = parseO20InfoHex(frame.data);
            if (!parsed) continue;
            const replyId = Number.parseInt(String(frame.id || "0").replace(/^0x/i, ""), 16);
            const deviceId = Number.isFinite(replyId) && replyId > 0 ? (replyId >> 21) & 0xFF : parsed.hand_flag === "0x02" ? 2 : 1;
            applyDeviceInfo(frame.dev, deviceId, parsed);
        }
    }

    function renderTx(frames) {
        const visibleFrames = frames.filter((frame) => !["o20-info-read", "o20-error-read"].includes(frame.label));
        if (!visibleFrames.length) {
            els.txLog.textContent = "暂无有效记录";
            return;
        }
        els.txLog.textContent = visibleFrames
            .slice()
            .reverse()
            .map((frame) => {
                if (frame.label === "o20-info-rx") {
                    const parsed = parseO20InfoHex(frame.data);
                    if (parsed) {
                        return `RX DEV${frame.dev} 设备信息 ${frame.id}\n${fullInfoText(parsed, "", frame.id)}`;
                    }
                }
                const parsedText = parsedRxText(frame);
                if (parsedText) return parsedText;
                const meta = frame.frame_type
                    ? `FrameType=${frame.frame_type} DLC=${frame.dlc} Ext=${frame.extern_flag}`
                    : "";
                const payload = frame.direction === "RX" ? `\n${frame.data}` : "";
                return `${frame.direction || "TX"} DEV${frame.dev} ${frame.label} ${frame.id} ret=${frame.ret} ${meta}${payload}`;
            })
            .join("\n\n");
    }

    function renderO20Sliders() {
        els.o20Sliders.innerHTML = "";
        state.joints.forEach((value, index) => {
            const [lower, upper] = O20_JOINT_RANGES[index];
            const row = document.createElement("div");
            row.className = "joint-row";
            row.innerHTML = `
                <label>J${String(index + 1).padStart(2, "0")} ${O20_JOINT_LABELS[index]}</label>
                <input type="range" min="0" max="100" step="1" value="${value}" data-index="${index}">
                <input type="number" min="0" max="100" step="1" value="${value}" data-index="${index}" title="${lower}..${upper}">
            `;
            const range = row.querySelector("input[type='range']");
            const number = row.querySelector("input[type='number']");
            range.addEventListener("input", () => {
                state.joints[index] = Number(range.value);
                number.value = range.value;
                throttledSendO20();
            });
            number.addEventListener("change", () => {
                const next = Math.max(0, Math.min(100, Number(number.value) || 0));
                state.joints[index] = next;
                number.value = String(next);
                range.value = String(next);
                sendO20();
            });
            els.o20Sliders.appendChild(row);
        });
    }

    function throttledSendO20() {
        clearTimeout(state.timer);
        state.timer = setTimeout(sendO20, 120);
    }

    function clearPendingO20Send() {
        clearTimeout(state.timer);
        state.timer = null;
    }

    async function sendO20() {
        const devices = selectedDevices();
        if (!devices.length) {
            setO20Status("请先勾选并连接 CAN 设备");
            return;
        }
        try {
            await api("/api/o20/joints", {
                devices,
                joints: state.joints,
                device_ids: selectedDeviceIdMap(),
                require_open: true
            });
            setO20Status(`O20 目标位置已发送：${devices.map((dev) => `DEV${dev}/${handName(profileFor(dev).deviceId)}`).join("，")}`);
            await delay(40);
            await refreshStatus();
        } catch (error) {
            setO20Status(error.message);
        }
    }

    async function sendO20Velocity() {
        const devices = selectedDevices();
        if (!devices.length) {
            setO20Status("请先勾选并连接 CAN 设备");
            return;
        }
        const velocityPercent = Math.max(0, Math.min(100, Number(els.o20Velocity.value) || 0));
        const rawVelocity = velocityPercentToRaw(velocityPercent);
        els.o20Velocity.value = String(velocityPercent);
        try {
            await api("/api/o20/velocity", {
                devices,
                velocity: rawVelocity,
                device_ids: selectedDeviceIdMap(),
                require_open: true
            });
            setO20Status(`O20 目标速度已发送：${velocityPercent}% -> raw ${rawVelocity}；${devices.map((dev) => `DEV${dev}/${handName(profileFor(dev).deviceId)}`).join("，")}`);
            await delay(40);
            await refreshStatus();
        } catch (error) {
            setO20Status(error.message);
        }
    }

    async function queryO20Info() {
        if (!selectedDevices().length) {
            setO20Status("请先勾选并连接 CAN 设备");
            return;
        }
        try {
            const result = await api("/api/o20/info", {
                devices: selectedDevices(),
                device_id: 0
            });
            const matchedByDev = new Set();
            const lines = (result.results || []).filter((item) => item.matched).map((item) => {
                const info = item.info || {};
                applyDeviceInfo(item.dev, item.device_id, info);
                if (!matchedByDev.has(item.dev)) {
                    matchedByDev.add(item.dev);
                }
                return `DEV${item.dev} ${info.hand || handName(item.device_id)} ${nodeText(item.device_id)}`;
            });
            if (!lines.length) {
                const probed = selectedDevices().map((dev) => `DEV${dev}`).join("，");
                setO20Status(`${probed} 未收到可解析的 O20 设备信息；已过滤无响应探测帧。`);
            } else {
                setO20Status(`探测完成：${lines.join("，")}；设备区已更新。`);
            }
            await refreshStatus();
        } catch (error) {
            setO20Status(error.message);
        }
    }

    async function queryO20Error() {
        if (!selectedDevices().length) {
            setO20Status("请先勾选并连接 CAN 设备");
            return;
        }
        try {
            const result = await api("/api/o20/error", {
                devices: selectedDevices(),
                device_ids: selectedDeviceIdMap()
            });
            const lines = (result.results || []).map((item) => {
                const label = `DEV${item.dev}/${handName(item.device_id)}`;
                return item.matched ? `${label} ${errorText(item.errors)}` : `${label} 未收到错误状态回传`;
            });
            setO20Status(lines.length ? `错误查询：${lines.join("；")}` : "未收到错误状态回传");
            await refreshStatus();
        } catch (error) {
            setO20Status(error.message);
        }
    }

    async function clearO20Error() {
        if (!selectedDevices().length) {
            setO20Status("请先勾选并连接 CAN 设备");
            return;
        }
        try {
            await api("/api/o20/error/clear", {
                devices: selectedDevices(),
                device_ids: selectedDeviceIdMap()
            });
            setO20Status("错误清除指令已发送");
            await delay(40);
            await refreshStatus();
        } catch (error) {
            setO20Status(error.message);
        }
    }

    async function refreshStatus() {
        try {
            renderDevices(await api("/api/status"));
        } catch (error) {
            els.runtimeStatus.textContent = error.message;
        }
    }

    async function scanDevices() {
        try {
            renderDevices(await api("/api/scan", {}));
        } catch (error) {
            setO20Status(error.message);
        }
    }

    async function openSelected() {
        if (!selectedDevices().length) {
            setO20Status("请先勾选设备");
            return;
        }
        try {
            await api("/api/open", { devices: selectedDevices() });
            await refreshStatus();
            setO20Status("设备已连接，正在查询设备信息");
            await queryO20Info();
        } catch (error) {
            setO20Status(error.message);
        }
    }

    async function loadDanceFiles() {
        try {
            renderDance(await api("/api/o20/dance"));
        } catch (error) {
            showError(error);
        }
    }

    function renderDance(payload) {
        state.danceFiles = payload.files || [];
        if (!state.selectedDanceFile || !state.danceFiles.includes(state.selectedDanceFile)) {
            state.selectedDanceFile = state.danceFiles[0] || "";
        }
        renderDanceFiles();
        renderDanceStatus(payload.status || {});
    }

    function renderDanceFiles() {
        els.danceFileList.innerHTML = "";
        if (!state.danceFiles.length) {
            els.danceFileList.innerHTML = `<div class="device-meta">O20 文件夹暂无序列</div>`;
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

    function renderDanceStatus(status) {
        state.danceRunning = Boolean(status.running);
        els.runDanceBtn.disabled = state.danceRunning;
        els.stopDanceBtn.disabled = !state.danceRunning;
        const file = status.file || state.selectedDanceFile || "未选择";
        els.danceStatus.textContent = state.danceRunning
            ? `${file} 执行中 · 已发送 ${Number(status.sent || 0)} 条`
            : status.message || "未执行";
        if (state.danceRunning && !state.danceStatusTimer) {
            state.danceStatusTimer = window.setInterval(loadDanceFiles, 500);
        }
        if (!state.danceRunning && state.danceStatusTimer) {
            clearInterval(state.danceStatusTimer);
            state.danceStatusTimer = 0;
        }
    }

    function numberInput(input, fallback) {
        const value = Number(input.value);
        const rounded = Number.isFinite(value) && value >= 0 ? Math.floor(value) : fallback;
        input.value = String(rounded);
        return rounded;
    }

    async function runDance() {
        if (!selectedDevices().length) {
            showError(new Error("请先勾选设备"));
            return;
        }
        if (!state.selectedDanceFile) {
            showError(new Error("请先选择 O20 序列"));
            return;
        }
        try {
            clearPendingO20Send();
            renderDance(await api("/api/o20/dance/run", {
                devices: selectedDevices(),
                device_ids: selectedDeviceIdMap(),
                file: state.selectedDanceFile,
                loop_count: numberInput(els.danceLoopCount, 1),
                interval_ms: numberInput(els.danceIntervalMs, 30)
            }));
            await refreshStatus();
        } catch (error) {
            showError(error);
        }
    }

    async function stopDance() {
        try {
            renderDance(await api("/api/o20/dance/stop", {}));
        } catch (error) {
            showError(error);
        }
    }

    async function requestCameraPermission() {
        if (!navigator.mediaDevices?.getUserMedia) {
            throw new Error("当前浏览器不支持摄像头访问");
        }
        const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        stream.getTracks().forEach((track) => track.stop());
    }

    async function loadCameras({ requestPermission = false } = {}) {
        if (!navigator.mediaDevices?.enumerateDevices) {
            els.cameraList.innerHTML = `<div class="device-meta">当前浏览器不支持摄像头枚举</div>`;
            return;
        }
        if (requestPermission) {
            await requestCameraPermission();
        }
        const devices = await navigator.mediaDevices.enumerateDevices();
        state.cameras = devices.filter((device) => device.kind === "videoinput");
        if (!state.cameras.some((camera) => camera.deviceId === state.selectedCameraId)) {
            state.selectedCameraId = "";
        }
        if (!state.selectedCameraId && state.cameras.length) {
            state.selectedCameraId = state.cameras[0].deviceId;
        }
        renderCameras();
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
                <span>${camera.label || `摄像头 ${index + 1}`}</span>
            `;
            item.querySelector("input").addEventListener("change", async (event) => {
                state.selectedCameraId = event.target.checked ? event.target.dataset.deviceId : "";
                renderCameras();
                if (state.gameRunning && state.selectedCameraId) {
                    await restartCamera();
                }
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

    function maxDelta(previous, next) {
        if (!previous) return 100;
        return next.reduce((max, value, index) => Math.max(max, Math.abs(value - previous[index])), 0);
    }

    function syncO20Sliders() {
        for (const input of els.o20Sliders.querySelectorAll("input[data-index]")) {
            input.value = String(state.joints[Number(input.dataset.index)]);
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
        syncO20Sliders();
        await sendO20();
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
            const result = await api("/api/o20/dance/save", { file, frames: state.poseRecords });
            els.poseStatus.textContent = `已保存 ${result.file} · ${result.count} 条`;
            await loadDanceFiles();
        } catch (error) {
            showError(error);
        }
    }

    async function sendFollowJoints(landmarks) {
        const now = Date.now();
        if (state.followSendBusy || now - state.lastFollowSendAt < FOLLOW_SEND_INTERVAL_MS) return;
        state.lastFollowSendAt = now;
        const { joints, debug } = buildO20FollowJoints(state.followMapper, landmarks);
        const delta = maxDelta(state.lastFollowSentJoints, joints);
        state.joints = joints;
        els.gestureName.textContent = "Follow";
        els.gestureConfidence.textContent = debug.lost ? "保持" : "运行中";
        els.debugLines.textContent = [`delta: ${delta.toFixed(0)}%`, debug.curl || "", `joints: ${joints.join(" ")}`].filter(Boolean).join("\n");
        const devices = selectedOpenDevices();
        if (!devices.length) {
            setGameResult("Follow 仅预览，请先连接已勾选设备");
            return;
        }
        if (delta < FOLLOW_CHANGE_THRESHOLD_PERCENT) {
            setGameResult(`Follow 已截流，变化 ${delta.toFixed(0)}%`);
            return;
        }
        try {
            state.followSendBusy = true;
            await api("/api/o20/joints", {
                devices,
                joints,
                device_ids: selectedDeviceIdMap(),
                require_open: true
            });
            state.lastFollowSentJoints = joints.slice();
            syncO20Sliders();
            setGameResult("O20 Follow 已发送");
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
        setGameResult(`识别 ${gestureName}，O20 准备出 ${responseGesture}`);
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
                await api("/api/o20/game", {
                    devices: selectedDevices(),
                    device_ids: selectedDeviceIdMap(),
                    gesture: responseGesture
                });
                setGameResult(`识别 ${sourceGesture}，O20 出 ${responseGesture}`);
                void refreshStatus();
            }
        } catch (error) {
            showError(error);
        } finally {
            state.actionInFlight = false;
        }
    }

    function setGameMode(mode) {
        if (state.gameRunning) return;
        state.gameMode = mode;
        state.followMapper = createO20FollowMapper();
        state.lastFollowSentJoints = null;
        state.lastAcceptedGesture = "";
        state.pendingResponseGesture = "";
        els.rpsModeBtn.classList.toggle("active", mode === "rps");
        els.followModeBtn.classList.toggle("active", mode === "follow");
        els.gestureName.textContent = mode === "follow" ? "Follow" : "未识别";
        els.gestureConfidence.textContent = mode === "follow" ? "待启动" : "0%";
        els.debugLines.textContent = "";
        setGameResult(mode === "follow" ? "O20 Follow 模式待启动" : "等待手势");
    }

    async function startGame() {
        try {
            await loadCameras({ requestPermission: !state.cameras.length || !state.selectedCameraId });
            if (!state.selectedCameraId) {
                setGameResult("请先选择摄像头");
                return;
            }
            if (!state.hands) {
                state.hands = new window.Hands({ locateFile: (file) => `/static/vendor/mediapipe/hands/${file}` });
                state.hands.setOptions({ maxNumHands: 1, modelComplexity: 1, minDetectionConfidence: 0.6, minTrackingConfidence: 0.6 });
                state.hands.onResults(onHandsResults);
            }
            state.gameRunning = true;
            state.followMapper = createO20FollowMapper();
            await restartCamera();
            els.gameBtn.textContent = state.gameMode === "follow" ? "跟随中" : "识别中";
            els.gameBtn.disabled = true;
            els.rpsModeBtn.disabled = true;
            els.followModeBtn.disabled = true;
            els.stopGameBtn.disabled = false;
            setGameResult(state.gameMode === "follow" ? "O20 Follow 运行中" : "等待手势");
        } catch (error) {
            showError(error);
        }
    }

    async function restartCamera() {
        const shouldRun = state.gameRunning;
        stopCamera();
        state.gameRunning = shouldRun;
        const videoConstraints = { width: { ideal: 960 }, height: { ideal: 720 } };
        if (state.selectedCameraId) videoConstraints.deviceId = { exact: state.selectedCameraId };
        const stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: false });
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
        setGameResult("摄像头已关闭");
        els.outputCanvas.getContext("2d").clearRect(0, 0, els.outputCanvas.width, els.outputCanvas.height);
    }

    function showError(error) {
        const message = error?.message || String(error);
        els.runtimeStatus.textContent = "操作失败";
        setO20Status(message);
        if (els.gameResult) setGameResult(message);
    }

    function init() {
        bindElements();
        renderO20Sliders();
        els.scanBtn.addEventListener("click", scanDevices);
        els.openBtn.addEventListener("click", openSelected);
        els.o20InfoBtn.addEventListener("click", queryO20Info);
        els.o20ErrorBtn.addEventListener("click", queryO20Error);
        els.o20ClearErrorBtn.addEventListener("click", clearO20Error);
        els.o20VelocityBtn.addEventListener("click", sendO20Velocity);
        els.o20ZeroBtn.addEventListener("click", () => {
            state.joints = defaultO20Joints();
            renderO20Sliders();
            sendO20();
        });
        els.refreshDanceBtn.addEventListener("click", loadDanceFiles);
        els.runDanceBtn.addEventListener("click", runDance);
        els.stopDanceBtn.addEventListener("click", stopDance);
        els.rpsModeBtn.addEventListener("click", () => setGameMode("rps"));
        els.poseRecordBtn.addEventListener("click", recordPose);
        els.poseOverwriteBtn.addEventListener("click", overwritePose);
        els.poseRunBtn.addEventListener("click", runPose);
        els.poseDeleteBtn.addEventListener("click", deletePose);
        els.poseSaveBtn.addEventListener("click", savePoseSequence);
        els.followModeBtn.addEventListener("click", () => setGameMode("follow"));
        els.gameBtn.addEventListener("click", startGame);
        els.stopGameBtn.addEventListener("click", stopGame);
        refreshStatus();
        renderPoseRecords();
        loadDanceFiles();
        loadCameras();
    }

    window.addEventListener("DOMContentLoaded", init);
})();
