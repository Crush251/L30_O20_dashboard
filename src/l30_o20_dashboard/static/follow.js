// Follow 模式：把 MediaPipe 手部关键点映射成 L30 真实关节值。
const L30Follow = (() => {
    const { JOINT_COUNT, JOINT_RANGES } = window.L30AppConfig;

    // MediaPipe 21 点索引。
    const WRIST = 0;
    const THUMB_MCP = 2;
    const THUMB_IP = 3;
    const THUMB_TIP = 4;
    const INDEX_MCP = 5;
    const INDEX_PIP = 6;
    const INDEX_DIP = 7;
    const INDEX_TIP = 8;
    const MIDDLE_MCP = 9;
    const MIDDLE_PIP = 10;
    const MIDDLE_DIP = 11;
    const MIDDLE_TIP = 12;
    const RING_MCP = 13;
    const RING_PIP = 14;
    const RING_DIP = 15;
    const RING_TIP = 16;
    const PINKY_MCP = 17;
    const PINKY_PIP = 18;
    const PINKY_DIP = 19;
    const PINKY_TIP = 20;

    // 创建 Follow 状态容器，保存平滑、去抖和丢帧保持状态。
    function createFollowMapper() {
        const mapper = {
            slowSmoothing: 0.45,
            fastSmoothing: 0.9,
            adaptiveDelta: 320,
            prevTargets: Array.from({ length: JOINT_COUNT }, () => 0),
            lastGoodPositions: Array.from({ length: JOINT_COUNT }, () => 0),
            lostCount: 0,
            maxLostHold: 8,
            thumbTarget: "",
            thumbTargetLastSwitch: 0,
            thumbSwitchCooldownMs: 250,
            thumbTouchOn: 0.65,
            thumbTouchOff: 0.68,
            thumbJ3Prev: 0,
            thumbJ4Prev: 0
        };
        mapper.lastGoodPositions[16] = 0;
        mapper.prevTargets[16] = 0;
        return mapper;
    }

    function adaptiveSmoothing(mapper, delta) {
        const ratio = clamp(Math.abs(delta) / mapper.adaptiveDelta, 0, 1);
        return mapper.slowSmoothing + (mapper.fastSmoothing - mapper.slowSmoothing) * ratio;
    }

    function point(landmark) {
        if (Array.isArray(landmark)) {
            return landmark;
        }
        return [landmark.x, landmark.y, landmark.z || 0];
    }

    function clamp(value, lower, upper) {
        return Math.max(lower, Math.min(upper, Number(value) || 0));
    }

    function clampPercent(value) {
        return clamp(value, 0, 100);
    }

    function sub(a, b) {
        return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
    }

    function norm(v) {
        return Math.hypot(v[0], v[1], v[2]);
    }

    function dot(a, b) {
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    }

    function angleBetween(v1, v2) {
        const n1 = norm(v1);
        const n2 = norm(v2);
        if (!n1 || !n2) {
            return 0;
        }
        const cos = Math.max(-1, Math.min(1, dot(v1, v2) / (n1 * n2)));
        return (Math.acos(cos) * 180) / Math.PI;
    }

    function jointAngle(points, a, b, c) {
        return angleBetween(sub(points[a], points[b]), sub(points[c], points[b]));
    }

    function dist3(points, a, b) {
        return norm(sub(points[a], points[b]));
    }

    function mapDegToRange(deg, lower, upper) {
        const clamped = Math.max(0, Math.min(90, deg));
        return lower + (clamped / 90) * (upper - lower);
    }

    function mapRangeToPercent(value, minValue, maxValue) {
        if (Math.abs(maxValue - minValue) < 1e-8) {
            return 0;
        }
        return clampPercent(((value - minValue) / (maxValue - minValue)) * 100);
    }

    function percentToJointValue(percent, jointIndex0Based) {
        const [minValue, maxValue] = JOINT_RANGES[jointIndex0Based];
        const ratio = clampPercent(percent) / 100;
        return Math.round(minValue + (maxValue - minValue) * ratio);
    }

    function jointCurlPercent(angle) {
        // L30 屈伸通道实测方向：值越大越弯曲。伸直约 175 度，弯曲到 95 度以下视为全握。
        return 100 - mapRangeToPercent(angle, 95, 175);
    }

    function rootCurlPercent(angle) {
        // MCP 根部角受掌骨方向影响，张开时通常达不到 175 度，单独用更窄阈值。
        return 100 - mapRangeToPercent(angle, 95, 135);
    }

    // 计算单根非拇指的指根弯曲度和指尖弯曲度。
    function computeFingerRootTipCurl(points, mcp, pip, dip, tip) {
        const rootAngle = jointAngle(points, WRIST, mcp, pip);
        const pipAngle = jointAngle(points, mcp, pip, dip);
        const dipAngle = jointAngle(points, pip, dip, tip);
        const pipCurl = jointCurlPercent(pipAngle);
        const dipCurl = jointCurlPercent(dipAngle);
        const tipCurl = clampPercent(pipCurl * 0.7 + dipCurl * 0.3);
        const rootCurl = Math.max(rootCurlPercent(rootAngle), tipCurl * 0.75);
        return {
            rootCurl,
            tipCurl,
            rootAngle,
            pipAngle,
            dipAngle
        };
    }

    function fingerSpreadPercent(points, mcp1, pip1, mcp2, pip2, minAngle, maxAngle) {
        const v1 = sub(points[pip1], points[mcp1]);
        const v2 = sub(points[pip2], points[mcp2]);
        const angle = angleBetween(v1, v2);
        return {
            angle,
            percent: mapRangeToPercent(angle, minAngle, maxAngle)
        };
    }

    function spreadPercentToSideValue(percent, sign = 1) {
        return Math.round(sign * 200 * (clampPercent(percent) / 100));
    }

    // L30 非拇指真实关节映射：J5-J16，不包含手腕。
    function applyNonThumbFingerJoints(points, positions, debug) {
        const index = computeFingerRootTipCurl(points, INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP);
        const middle = computeFingerRootTipCurl(points, MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP);
        const ring = computeFingerRootTipCurl(points, RING_MCP, RING_PIP, RING_DIP, RING_TIP);
        const pinky = computeFingerRootTipCurl(points, PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP);

        const indexSide = fingerSpreadPercent(points, INDEX_MCP, INDEX_PIP, MIDDLE_MCP, MIDDLE_PIP, 2, 18);
        const ringSide = fingerSpreadPercent(points, RING_MCP, RING_PIP, MIDDLE_MCP, MIDDLE_PIP, 2, 15);
        const pinkySide = fingerSpreadPercent(points, PINKY_MCP, PINKY_PIP, RING_MCP, RING_PIP, 2, 20);

        positions[4] = spreadPercentToSideValue(ringSide.percent, -1); // J5 无名指侧摆
        positions[5] = percentToJointValue(ring.tipCurl, 5); // J6 无名指指尖
        positions[6] = percentToJointValue(ring.rootCurl, 6); // J7 无名指根
        positions[7] = percentToJointValue(middle.rootCurl, 7); // J8 中指指根
        positions[8] = percentToJointValue(middle.tipCurl, 8); // J9 中指指尖
        positions[9] = percentToJointValue(pinky.rootCurl, 9); // J10 小指指根
        positions[10] = percentToJointValue(pinky.tipCurl, 10); // J11 小指指尖
        positions[11] = spreadPercentToSideValue(pinkySide.percent, -1); // J12 小指侧摆
        positions[12] = 0; // J13 中指侧摆，第一版固定中位
        positions[13] = spreadPercentToSideValue(indexSide.percent, 1); // J14 食指侧摆
        positions[14] = percentToJointValue(index.rootCurl, 14); // J15 食指指根
        positions[15] = percentToJointValue(index.tipCurl, 15); // J16 食指指尖

        debug.curlPct = `I ${index.rootCurl.toFixed(0)}/${index.tipCurl.toFixed(0)} M ${middle.rootCurl.toFixed(0)}/${middle.tipCurl.toFixed(0)} R ${ring.rootCurl.toFixed(0)}/${ring.tipCurl.toFixed(0)} P ${pinky.rootCurl.toFixed(0)}/${pinky.tipCurl.toFixed(0)}`;
        debug.sidePct = `I ${indexSide.percent.toFixed(0)} R ${ringSide.percent.toFixed(0)} P ${pinkySide.percent.toFixed(0)}`;
    }

    // 计算拇指根部和指尖弯曲度，值越大越弯曲。
    function computeThumbCurl(points) {
        const rootAngle = jointAngle(points, WRIST, THUMB_MCP, THUMB_IP);
        const tipAngle = jointAngle(points, THUMB_MCP, THUMB_IP, THUMB_TIP);
        return {
            rootCurl: jointCurlPercent(rootAngle),
            tipCurl: jointCurlPercent(tipAngle),
            rootAngle,
            tipAngle
        };
    }

    // 根据拇指尖到各指尖的 3D 距离选择对指目标。
    function pickThumbTarget(mapper, points) {
        const palmWidth = Math.max(dist3(points, 5, 17), 1e-6);
        const tipIndexes = { index: 8, middle: 12, ring: 16, pinky: 20 };
        const ratios = Object.fromEntries(
            Object.entries(tipIndexes).map(([name, tip]) => [name, dist3(points, 4, tip) / palmWidth])
        );
        const groupA = ["index", "pinky"];
        const groupB = ["middle", "ring"];
        const bestA = groupA.reduce((best, name) => (ratios[name] < ratios[best] ? name : best));
        const bestB = groupB.reduce((best, name) => (ratios[name] < ratios[best] ? name : best));
        const minA = ratios[bestA];
        const minB = ratios[bestB];
        const groupMargin = 0.06;
        let preferB;
        if (groupB.includes(mapper.thumbTarget)) {
            preferB = minB <= minA + groupMargin;
        } else if (groupA.includes(mapper.thumbTarget)) {
            preferB = minB + groupMargin < minA;
        } else {
            preferB = minB + groupMargin < minA;
        }
        const bestName = preferB ? bestB : bestA;
        const bestRatio = preferB ? minB : minA;
        const now = Date.now();

        if (!mapper.thumbTarget) {
            if (bestRatio < mapper.thumbTouchOn) {
                mapper.thumbTarget = bestName;
                mapper.thumbTargetLastSwitch = now;
            }
        } else {
            const currentRatio = ratios[mapper.thumbTarget] ?? 1;
            if (currentRatio > mapper.thumbTouchOff) {
                if (
                    bestRatio < mapper.thumbTouchOn &&
                    now - mapper.thumbTargetLastSwitch > mapper.thumbSwitchCooldownMs
                ) {
                    mapper.thumbTarget = bestName;
                    mapper.thumbTargetLastSwitch = now;
                } else {
                    mapper.thumbTarget = "";
                    mapper.thumbTargetLastSwitch = now;
                }
            } else if (
                bestName !== mapper.thumbTarget &&
                currentRatio - bestRatio > 0.07 &&
                now - mapper.thumbTargetLastSwitch > mapper.thumbSwitchCooldownMs
            ) {
                mapper.thumbTarget = bestName;
                mapper.thumbTargetLastSwitch = now;
            }
        }
        return { target: mapper.thumbTarget, ratios };
    }

    // 主映射函数：MediaPipe 关键点 -> 17 个 L30 真实关节值。
    function buildFollowPositions(mapper, landmarks) {
        if (!landmarks) {
            mapper.lostCount += 1;
            const held = mapper.lostCount <= mapper.maxLostHold;
            const positions = held ? mapper.lastGoodPositions.slice() : Array.from({ length: JOINT_COUNT }, () => 0);
            positions[16] = 0;
            return { positions, debug: { lostHold: held } };
        }

        mapper.lostCount = 0;
        const points = landmarks.map(point);
        const positions = Array.from({ length: JOINT_COUNT }, () => 0);
        const debug = {};
        applyNonThumbFingerJoints(points, positions, debug);

        const { target, ratios } = pickThumbTarget(mapper, points);
        debug.thumbTarget = target || "none";
        const thumbCurl = computeThumbCurl(points);
        const presets = {
            index: [429, 250, 378, 596],
            middle: [429, 250, 152, 596],
            ring: [430, 250, -8, 596],
            pinky: [373, 251, -42, 849]
        };
        if (target && presets[target]) {
            [positions[0], positions[1], positions[2], positions[3]] = presets[target];
            mapper.thumbJ3Prev = positions[2];
            mapper.thumbJ4Prev = positions[3];
        } else {
            positions[0] = percentToJointValue(thumbCurl.rootCurl, 0);
            positions[1] = percentToJointValue(thumbCurl.tipCurl, 1);
            mapper.thumbJ3Prev *= 0.9;
            mapper.thumbJ4Prev *= 0.9;
            positions[2] = Math.round(mapper.thumbJ3Prev);
            positions[3] = Math.round(mapper.thumbJ4Prev);
        }
        debug.thumbRatios = `I ${ratios.index.toFixed(2)} M ${ratios.middle.toFixed(2)} R ${ratios.ring.toFixed(2)} P ${ratios.pinky.toFixed(2)}`;
        debug.thumbCurl = `T ${thumbCurl.rootCurl.toFixed(0)}/${thumbCurl.tipCurl.toFixed(0)}`;

        positions[16] = 0;
        let maxRawDelta = 0;
        let maxAlpha = mapper.slowSmoothing;
        for (let index = 0; index < 16; index += 1) {
            const targetValue = positions[index];
            const previous = mapper.prevTargets[index];
            const rawDelta = targetValue - previous;
            const alpha = adaptiveSmoothing(mapper, rawDelta);
            const smoothed = alpha * targetValue + (1 - alpha) * previous;
            maxRawDelta = Math.max(maxRawDelta, Math.abs(rawDelta));
            maxAlpha = Math.max(maxAlpha, alpha);
            positions[index] = Math.round(smoothed);
            mapper.prevTargets[index] = smoothed;
        }
        debug.filter = `filter a=${maxAlpha.toFixed(2)} raw=${Math.round(maxRawDelta)}`;
        mapper.prevTargets[16] = 0;
        mapper.lastGoodPositions = positions.slice();
        return { positions, debug };
    }

    return {
        createFollowMapper,
        buildFollowPositions
    };
})();

window.L30Follow = L30Follow;
