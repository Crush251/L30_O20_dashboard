(function (global) {
    const FINGER_CONFIGS = [
        { key: "index", label: "食指", mcp: 5, pip: 6, dip: 7, tip: 8 },
        { key: "middle", label: "中指", mcp: 9, pip: 10, dip: 11, tip: 12 },
        { key: "ring", label: "无名指", mcp: 13, pip: 14, dip: 15, tip: 16 },
        { key: "pinky", label: "小指", mcp: 17, pip: 18, dip: 19, tip: 20 }
    ];

    const ACCEPTED_CONFIDENCE = 0.67;
    const OPEN_STATE_THRESHOLD = 0.67;
    const CLOSED_STATE_THRESHOLD = 0.38;

    const GESTURE_TEMPLATES = [
        {
            name: "石头",
            targets: { index: 0.32, middle: 0.32, ring: 0.32, pinky: 0.32 },
            validate(fingerScores) {
                const values = getFingerScoreValues(fingerScores);
                return average(values) <= 0.50 && Math.max(...values) <= 0.64;
            }
        },
        {
            name: "布",
            targets: { index: 0.72, middle: 0.72, ring: 0.72, pinky: 0.72 },
            validate(fingerScores) {
                const values = getFingerScoreValues(fingerScores);
                return average(values) >= 0.61 && Math.min(...values) >= 0.52;
            }
        },
        {
            name: "剪刀",
            targets: { index: 0.72, middle: 0.72, ring: 0.32, pinky: 0.32 },
            validate(fingerScores) {
                const openAvg = average([fingerScores.index, fingerScores.middle]);
                const closedAvg = average([fingerScores.ring, fingerScores.pinky]);
                return (
                    Math.min(fingerScores.index, fingerScores.middle) >= 0.56 &&
                    Math.max(fingerScores.ring, fingerScores.pinky) <= 0.62 &&
                    openAvg - closedAvg >= 0.10
                );
            }
        }
    ];

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function average(values) {
        if (!values.length) {
            return 0;
        }
        return values.reduce((sum, value) => sum + value, 0) / values.length;
    }

    function normalize(value, min, max) {
        if (max <= min) {
            return value >= max ? 1 : 0;
        }
        return clamp((value - min) / (max - min), 0, 1);
    }

    function toPoint(landmark) {
        return {
            x: landmark?.x || 0,
            y: landmark?.y || 0,
            z: (landmark?.z || 0) * 0.6
        };
    }

    function distance(a, b) {
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dz = a.z - b.z;
        return Math.sqrt((dx * dx) + (dy * dy) + (dz * dz));
    }

    function angleAt(a, b, c) {
        const ba = { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
        const bc = { x: c.x - b.x, y: c.y - b.y, z: c.z - b.z };
        const baLen = Math.sqrt((ba.x * ba.x) + (ba.y * ba.y) + (ba.z * ba.z));
        const bcLen = Math.sqrt((bc.x * bc.x) + (bc.y * bc.y) + (bc.z * bc.z));

        if (baLen === 0 || bcLen === 0) {
            return 180;
        }

        const dot = (ba.x * bc.x) + (ba.y * bc.y) + (ba.z * bc.z);
        const cosine = clamp(dot / (baLen * bcLen), -1, 1);
        return Math.acos(cosine) * (180 / Math.PI);
    }

    function getFingerScoreValues(fingerScores) {
        return FINGER_CONFIGS.map((finger) => fingerScores[finger.key]);
    }

    function getFingerStateLabel(score) {
        if (score >= OPEN_STATE_THRESHOLD) {
            return "伸展";
        }
        if (score <= CLOSED_STATE_THRESHOLD) {
            return "弯曲";
        }
        return "过渡";
    }

    function getPalmSize(landmarks) {
        const wrist = toPoint(landmarks[0]);
        const indexMcp = toPoint(landmarks[5]);
        const middleMcp = toPoint(landmarks[9]);
        const pinkyMcp = toPoint(landmarks[17]);

        return Math.max(
            average([
                distance(wrist, indexMcp),
                distance(wrist, middleMcp),
                distance(wrist, pinkyMcp),
                distance(indexMcp, pinkyMcp)
            ]),
            0.05
        );
    }

    function analyzeFinger(landmarks, palmSize, finger) {
        const wrist = toPoint(landmarks[0]);
        const mcp = toPoint(landmarks[finger.mcp]);
        const pip = toPoint(landmarks[finger.pip]);
        const dip = toPoint(landmarks[finger.dip]);
        const tip = toPoint(landmarks[finger.tip]);

        const chainLength =
            distance(mcp, pip) +
            distance(pip, dip) +
            distance(dip, tip);
        const directDistance = distance(mcp, tip);
        const straightnessScore = normalize(
            directDistance / Math.max(chainLength, 0.0001),
            0.55,
            0.98
        );
        const pipAngleScore = normalize(angleAt(mcp, pip, dip), 95, 175);
        const dipAngleScore = normalize(angleAt(pip, dip, tip), 110, 175);
        const jointScore = average([pipAngleScore, dipAngleScore]);
        const reachScore = normalize(
            (distance(wrist, tip) - distance(wrist, mcp)) / palmSize,
            0.12,
            1.05
        );
        const score = clamp(
            (straightnessScore * 0.5) +
                (jointScore * 0.35) +
                (reachScore * 0.15),
            0,
            1
        );

        return {
            key: finger.key,
            label: finger.label,
            score,
            state: getFingerStateLabel(score),
            tipIndex: finger.tip
        };
    }

    function analyzeFingers(landmarks) {
        const palmSize = getPalmSize(landmarks);
        const fingerDetails = FINGER_CONFIGS.map((finger) =>
            analyzeFinger(landmarks, palmSize, finger)
        );
        const fingerScores = {};

        for (const detail of fingerDetails) {
            fingerScores[detail.key] = detail.score;
        }

        return { fingerDetails, fingerScores };
    }

    function scoreTemplate(fingerScores, template) {
        let totalDiff = 0;

        for (const finger of FINGER_CONFIGS) {
            totalDiff += Math.abs(fingerScores[finger.key] - template.targets[finger.key]);
        }

        return clamp(1 - (totalDiff / FINGER_CONFIGS.length), 0, 1);
    }

    function buildTemplateEvaluations(fingerScores) {
        return GESTURE_TEMPLATES.map((template) => ({
            name: template.name,
            score: scoreTemplate(fingerScores, template),
            valid: template.validate(fingerScores)
        })).sort((a, b) => b.score - a.score);
    }

    function recognizeGesture(landmarks) {
        const { fingerDetails, fingerScores } = analyzeFingers(landmarks);
        const templateEvaluations = buildTemplateEvaluations(fingerScores);
        const best = templateEvaluations[0] || { name: "未识别", score: 0, valid: false };
        const second = templateEvaluations[1] || { name: "未识别", score: 0, valid: false };
        const confidence = clamp(
            (best.score * 0.72) +
                (Math.max(best.score - second.score, 0) * 0.6) +
                (best.valid ? 0.12 : 0),
            0,
            0.99
        );
        const accepted = best.valid && confidence >= ACCEPTED_CONFIDENCE;
        const templateScores = {};
        const templateValidity = {};

        for (const evaluation of templateEvaluations) {
            templateScores[evaluation.name] = evaluation.score;
            templateValidity[evaluation.name] = evaluation.valid;
        }

        return {
            name: accepted ? best.name : "未识别",
            confidence: accepted
                ? confidence
                : Math.min(confidence, ACCEPTED_CONFIDENCE - 0.01),
            candidateName: best.name,
            accepted,
            fingerDetails,
            fingerScores,
            templateScores,
            templateValidity
        };
    }

    function isAcceptedGesture(gesture) {
        return Boolean(gesture && gesture.name !== "未识别" && gesture.confidence >= ACCEPTED_CONFIDENCE);
    }

    function getDebugLines(gesture) {
        const lines = ["四指伸展分数(拇指不参与判定):"];

        for (const finger of gesture.fingerDetails) {
            lines.push(`  ${finger.label}: ${finger.score.toFixed(2)} (${finger.state})`);
        }

        lines.push(
            `模板匹配: 石头 ${Math.round((gesture.templateScores["石头"] || 0) * 100)}% | ` +
                `布 ${Math.round((gesture.templateScores["布"] || 0) * 100)}% | ` +
                `剪刀 ${Math.round((gesture.templateScores["剪刀"] || 0) * 100)}%`
        );
        lines.push(
            `模板约束: 石头 ${gesture.templateValidity["石头"] ? "√" : "×"} | ` +
                `布 ${gesture.templateValidity["布"] ? "√" : "×"} | ` +
                `剪刀 ${gesture.templateValidity["剪刀"] ? "√" : "×"}`
        );

        if (!gesture.accepted && gesture.candidateName && gesture.candidateName !== "未识别") {
            lines.push(`候选手势: ${gesture.candidateName}，但未达到统一判定阈值`);
        }

        return lines;
    }

    global.RPSGestureRecognition = {
        ACCEPTED_CONFIDENCE,
        FINGER_CONFIGS,
        getFingerStateLabel,
        getDebugLines,
        isAcceptedGesture,
        recognizeGesture
    };
})(window);
