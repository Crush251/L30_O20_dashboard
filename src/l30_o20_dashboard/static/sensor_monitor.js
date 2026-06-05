// 传感器监控页：按设备型号主动查询触觉点阵，并渲染为手掌热力图。
(() => {
    const FINGERS = ["thumb", "index", "middle", "ring", "pinky"];
    const FINGER_LABELS = {
        thumb: "拇指",
        index: "食指",
        middle: "中指",
        ring: "无名指",
        pinky: "小指"
    };
    const MODEL_LABELS = { l30: "L30", o20: "O20", unknown: "未定" };

    const state = {
        devices: [],
        selected: new Set(),
        knownDevices: new Set(),
        profiles: {},
        results: {},
        running: false,
        busy: false,
        timer: 0,
        intervalMs: 200
    };

    const els = {};

    function bindElements() {
        for (const id of [
            "sensorView", "dashboardView", "sensorRefreshBtn", "sensorDeviceList", "sensorReadOnceBtn",
            "sensorStartBtn", "sensorStopBtn", "sensorIntervalMs", "sensorStatus", "sensorCards"
        ]) {
            els[id] = document.getElementById(id);
        }
        els.viewButtons = Array.from(document.querySelectorAll(".view-switch-button"));
    }

    async function api(path, body = null) {
        const options = body
            ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
            : { method: "GET" };
        const response = await fetch(path, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
        return data;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, Number(value) || 0));
    }

    function setSensorStatus(text) {
        if (els.sensorStatus) els.sensorStatus.textContent = text;
    }

    function setGlobalStatus(text) {
        const runtime = document.getElementById("runtimeStatus");
        if (runtime) runtime.textContent = text;
    }

    function selectedDevices() {
        return Array.from(state.selected).sort((a, b) => a - b);
    }

    function profileFor(dev) {
        const key = String(dev);
        if (!state.profiles[key]) {
            state.profiles[key] = { model: "unknown", deviceId: 1 };
        }
        return state.profiles[key];
    }

    function inferProfile(device) {
        const info = device.info || {};
        const profile = profileFor(device.dev);
        if (profile.model !== "unknown") return profile;
        if (String(info.product || info.model || "").toLowerCase() === "o20" || info.o20_info) {
            profile.model = "o20";
            profile.deviceId = Number(info.o20_device_id) || 1;
        } else if (info.product === "L30" || info.product_id === 0x13) {
            profile.model = "l30";
        }
        return profile;
    }

    function deviceInfoText(device) {
        const info = device.info || {};
        if (info.o20_info) {
            const o20 = info.o20_info;
            return `序列号 ${o20.serial || "-"} · ${o20.hand || "左右手未知"}`;
        }
        if (info.product === "L30" || info.product_id === 0x13) {
            return `${info.product_code || info.product || "L30"} · ${info.hand || "左右手未知"} · ${info.sensor || "传感器未知"}`;
        }
        return info.serial ? `适配器 ${info.serial}` : "请先在控制台执行设备查询";
    }

    function viewFromLocation() {
        if (window.location.pathname === "/sensor" || window.location.hash === "#sensors") return "sensorView";
        return "dashboardView";
    }

    function switchView(targetId, updateHash = true) {
        for (const panel of document.querySelectorAll(".view-panel")) {
            panel.hidden = panel.id !== targetId;
        }
        for (const button of els.viewButtons) {
            button.classList.toggle("active", button.dataset.viewTarget === targetId);
        }
        if (updateHash) {
            if (targetId === "sensorView") history.replaceState(null, "", "#sensors");
            else history.replaceState(null, "", window.location.pathname === "/sensor" ? "/" : window.location.pathname);
        }
        if (targetId === "sensorView") {
            void refreshSensorDevices(false);
        } else {
            stopMonitor();
        }
    }

    async function refreshSensorDevices(scan = false) {
        try {
            const payload = scan ? await api("/api/scan", {}) : await api("/api/status");
            state.devices = payload.devices || [];
            const shouldSelectFirstOpen = state.selected.size === 0;
            for (const [index, device] of state.devices.entries()) {
                inferProfile(device);
                if (!state.knownDevices.has(device.dev)) {
                    if (device.opened && (shouldSelectFirstOpen || index === 0)) state.selected.add(device.dev);
                    state.knownDevices.add(device.dev);
                }
            }
            renderSensorDeviceList();
            setSensorStatus(`${payload.mock ? "Mock" : "硬件"} · ${state.devices.length} 个设备`);
        } catch (error) {
            setSensorStatus(error.message);
        }
    }

    function renderSensorDeviceList() {
        els.sensorDeviceList.innerHTML = "";
        if (!state.devices.length) {
            els.sensorDeviceList.innerHTML = '<div class="device-meta">未发现设备</div>';
            return;
        }
        for (const device of state.devices) {
            const profile = inferProfile(device);
            const item = document.createElement("div");
            item.className = `sensor-device-item model-${profile.model}`;
            const disabled = !device.opened;
            const handSelect = profile.model === "o20" ? `
                <label class="compact-field sensor-node-field">
                    <span>节点</span>
                    <select class="sensor-device-id" data-dev="${device.dev}">
                        <option value="1" ${Number(profile.deviceId) === 1 ? "selected" : ""}>右手 0x01</option>
                        <option value="2" ${Number(profile.deviceId) === 2 ? "selected" : ""}>左手 0x02</option>
                    </select>
                </label>
            ` : "";
            item.innerHTML = `
                <input class="sensor-device-check" type="checkbox" data-dev="${device.dev}" ${state.selected.has(device.dev) ? "checked" : ""} ${disabled ? "disabled" : ""}>
                <div class="sensor-device-body">
                    <div class="device-name">
                        <span>DEV${device.dev}</span>
                        <span class="device-badge ${device.opened ? "enabled" : ""}">${device.opened ? "已连接" : "未连接"}</span>
                    </div>
                    <div class="sensor-device-controls">
                        <label class="compact-field">
                            <span>型号</span>
                            <select class="sensor-model-select" data-dev="${device.dev}">
                                <option value="unknown" ${profile.model === "unknown" ? "selected" : ""}>未定</option>
                                <option value="l30" ${profile.model === "l30" ? "selected" : ""}>L30</option>
                                <option value="o20" ${profile.model === "o20" ? "selected" : ""}>O20</option>
                            </select>
                        </label>
                        ${handSelect}
                    </div>
                    <div class="device-meta"><strong>${MODEL_LABELS[profile.model] || "未定"}</strong> · ${escapeHtml(deviceInfoText(device))}</div>
                </div>
            `;
            item.querySelector(".sensor-device-check").addEventListener("change", (event) => {
                const dev = Number(event.target.dataset.dev);
                if (event.target.checked) state.selected.add(dev);
                else state.selected.delete(dev);
            });
            item.querySelector(".sensor-model-select").addEventListener("change", (event) => {
                const next = event.target.value;
                const dev = Number(event.target.dataset.dev);
                profileFor(dev).model = next;
                renderSensorDeviceList();
            });
            const nodeSelect = item.querySelector(".sensor-device-id");
            if (nodeSelect) {
                nodeSelect.addEventListener("change", (event) => {
                    profileFor(Number(event.target.dataset.dev)).deviceId = Number(event.target.value) || 1;
                });
            }
            els.sensorDeviceList.appendChild(item);
        }
    }

    function requestProfiles(devices) {
        return Object.fromEntries(devices.map((dev) => {
            const profile = profileFor(dev);
            return [String(dev), { model: profile.model, device_id: Number(profile.deviceId) || 1 }];
        }));
    }

    async function readSensors({ scheduleNext = false } = {}) {
        if (state.busy) return;
        const devices = selectedDevices().filter((dev) => state.devices.some((item) => item.dev === dev && item.opened));
        if (!devices.length) {
            setSensorStatus("请先勾选已连接设备");
            if (scheduleNext) schedulePoll();
            return;
        }
        state.busy = true;
        try {
            const payload = await api("/api/sensors/read", { devices, profiles: requestProfiles(devices) });
            for (const result of payload.devices || []) {
                state.results[String(result.dev)] = result;
            }
            renderSensorCards(payload.devices || []);
            const maxValue = Math.max(0, ...(payload.devices || []).map((item) => Number(item.summary?.max) || 0));
            setSensorStatus(`已更新 ${devices.length} 个设备 · 峰值 ${maxValue}`);
            setGlobalStatus(`传感器 · ${devices.length} 个设备`);
        } catch (error) {
            setSensorStatus(error.message);
            stopMonitor();
        } finally {
            state.busy = false;
            if (scheduleNext && state.running) schedulePoll();
        }
    }

    function schedulePoll() {
        clearTimeout(state.timer);
        state.timer = window.setTimeout(() => void readSensors({ scheduleNext: true }), state.intervalMs);
    }

    function startMonitor() {
        state.running = true;
        els.sensorStartBtn.disabled = true;
        els.sensorStopBtn.disabled = false;
        void readSensors({ scheduleNext: true });
    }

    function stopMonitor() {
        state.running = false;
        clearTimeout(state.timer);
        state.timer = 0;
        if (els.sensorStartBtn) els.sensorStartBtn.disabled = false;
        if (els.sensorStopBtn) els.sensorStopBtn.disabled = true;
    }

    function renderSensorCards(results) {
        if (!results.length) {
            els.sensorCards.innerHTML = '<div class="sensor-empty">暂无传感器数据</div>';
            return;
        }
        els.sensorCards.innerHTML = "";
        for (const result of results) {
            const card = document.createElement("article");
            card.className = `sensor-card model-${result.model || "unknown"}`;
            const summary = result.summary || {};
            const nodeText = result.model === "o20"
                ? `节点 0x${Number(result.device_id || 1).toString(16).padStart(2, "0").toUpperCase()}`
                : result.node_id
                  ? `Node ${result.node_id}`
                  : "";
            card.innerHTML = `
                <div class="sensor-card-head">
                    <div>
                        <h2>DEV${result.dev} ${MODEL_LABELS[result.model] || "未知"}</h2>
                        <p>${escapeHtml(nodeText)} · 在线 ${summary.online_fingers || 0}/5 · 平均 ${summary.avg || 0}</p>
                    </div>
                    <strong class="sensor-peak">${summary.max || 0}</strong>
                </div>
                ${result.supported ? renderHand(result) : `<div class="sensor-empty">${escapeHtml(result.message || "暂不支持该型号")}</div>`}
            `;
            els.sensorCards.appendChild(card);
        }
    }

    function renderHand(result) {
        const fingers = Object.fromEntries((result.fingers || []).map((finger) => [finger.key, finger]));
        return `
            <div class="sensor-hand-scene ${result.model === "o20" ? "o20-hand" : "l30-hand"}">
                <div class="sensor-palm-core"><span>${result.model === "o20" ? "O20" : "L30"}</span></div>
                ${FINGERS.map((key) => renderFinger(fingers[key] || { key, label: FINGER_LABELS[key], values: [] })).join("")}
            </div>
        `;
    }

    function renderFinger(finger) {
        const values = Array.isArray(finger.values) ? finger.values : [];
        const online = finger.online !== false;
        const cells = Array.from({ length: 72 }, (_unused, index) => Number(values[index]) || 0)
            .map((value, index) => renderCell(value, index))
            .join("");
        const error = finger.error ? `<small title="${escapeHtml(finger.error)}">${escapeHtml(finger.error)}</small>` : "";
        return `
            <section class="sensor-finger sensor-finger-${finger.key} ${online ? "online" : "offline"}">
                <div class="sensor-finger-title">
                    <strong>${escapeHtml(finger.label || FINGER_LABELS[finger.key] || finger.key)}</strong>
                    <span>max ${Number(finger.max) || 0}</span>
                </div>
                <div class="tactile-grid" aria-label="${escapeHtml(finger.label || finger.key)}触觉矩阵">
                    ${cells}
                </div>
                ${error}
            </section>
        `;
    }

    function renderCell(value, index) {
        const level = clamp(value / 255, 0, 1);
        const alpha = 0.08 + level * 0.86;
        const color = level > 0.55 ? "#fffdf6" : "#4d211b";
        return `<span class="tactile-cell" title="P${index + 1}: ${value}" style="background: rgba(182, 75, 56, ${alpha.toFixed(3)}); color: ${color};">${value}</span>`;
    }

    function bindEvents() {
        for (const button of els.viewButtons) {
            button.addEventListener("click", () => switchView(button.dataset.viewTarget));
        }
        window.addEventListener("hashchange", () => switchView(viewFromLocation(), false));
        els.sensorRefreshBtn.addEventListener("click", () => void refreshSensorDevices(true));
        els.sensorReadOnceBtn.addEventListener("click", () => void readSensors());
        els.sensorStartBtn.addEventListener("click", startMonitor);
        els.sensorStopBtn.addEventListener("click", stopMonitor);
        els.sensorIntervalMs.addEventListener("change", (event) => {
            state.intervalMs = Number(event.target.value) || 200;
            if (state.running) schedulePoll();
        });
    }

    function init() {
        bindElements();
        if (!els.sensorView || !els.dashboardView) return;
        bindEvents();
        switchView(viewFromLocation(), false);
    }

    window.addEventListener("DOMContentLoaded", init);
})();
