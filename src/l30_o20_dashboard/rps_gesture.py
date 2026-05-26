from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


ACCEPTED_CONFIDENCE = 0.67
OPEN_STATE_THRESHOLD = 0.67
CLOSED_STATE_THRESHOLD = 0.38


@dataclass(frozen=True)
class FingerConfig:
    key: str
    label: str
    mcp: int
    pip: int
    dip: int
    tip: int


FINGER_CONFIGS = [
    FingerConfig("index", "食指", 5, 6, 7, 8),
    FingerConfig("middle", "中指", 9, 10, 11, 12),
    FingerConfig("ring", "无名指", 13, 14, 15, 16),
    FingerConfig("pinky", "小指", 17, 18, 19, 20),
]


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def normalize(value: float, minimum: float, maximum: float) -> float:
    if maximum <= minimum:
        return 1.0 if value >= maximum else 0.0
    return clamp((value - minimum) / (maximum - minimum), 0.0, 1.0)


def to_point(landmark: Any) -> dict[str, float]:
    if isinstance(landmark, dict):
        return {
            "x": float(landmark.get("x", 0.0)),
            "y": float(landmark.get("y", 0.0)),
            "z": float(landmark.get("z", 0.0)) * 0.6,
        }
    return {
        "x": float(getattr(landmark, "x", 0.0)),
        "y": float(getattr(landmark, "y", 0.0)),
        "z": float(getattr(landmark, "z", 0.0)) * 0.6,
    }


def distance(a: dict[str, float], b: dict[str, float]) -> float:
    dx = a["x"] - b["x"]
    dy = a["y"] - b["y"]
    dz = a["z"] - b["z"]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def angle_at(a: dict[str, float], b: dict[str, float], c: dict[str, float]) -> float:
    ba = {"x": a["x"] - b["x"], "y": a["y"] - b["y"], "z": a["z"] - b["z"]}
    bc = {"x": c["x"] - b["x"], "y": c["y"] - b["y"], "z": c["z"] - b["z"]}
    ba_len = math.sqrt(ba["x"] ** 2 + ba["y"] ** 2 + ba["z"] ** 2)
    bc_len = math.sqrt(bc["x"] ** 2 + bc["y"] ** 2 + bc["z"] ** 2)
    if ba_len == 0 or bc_len == 0:
        return 180.0
    dot = ba["x"] * bc["x"] + ba["y"] * bc["y"] + ba["z"] * bc["z"]
    cosine = clamp(dot / (ba_len * bc_len), -1.0, 1.0)
    return math.acos(cosine) * (180.0 / math.pi)


def get_finger_score_values(finger_scores: dict[str, float]) -> list[float]:
    return [finger_scores[finger.key] for finger in FINGER_CONFIGS]


def get_finger_state_label(score: float) -> str:
    if score >= OPEN_STATE_THRESHOLD:
        return "伸展"
    if score <= CLOSED_STATE_THRESHOLD:
        return "弯曲"
    return "过渡"


def get_palm_size(landmarks: list[Any]) -> float:
    wrist = to_point(landmarks[0])
    index_mcp = to_point(landmarks[5])
    middle_mcp = to_point(landmarks[9])
    pinky_mcp = to_point(landmarks[17])
    return max(
        average(
            [
                distance(wrist, index_mcp),
                distance(wrist, middle_mcp),
                distance(wrist, pinky_mcp),
                distance(index_mcp, pinky_mcp),
            ]
        ),
        0.05,
    )


def analyze_finger(landmarks: list[Any], palm_size: float, finger: FingerConfig) -> dict:
    wrist = to_point(landmarks[0])
    mcp = to_point(landmarks[finger.mcp])
    pip = to_point(landmarks[finger.pip])
    dip = to_point(landmarks[finger.dip])
    tip = to_point(landmarks[finger.tip])

    chain_length = distance(mcp, pip) + distance(pip, dip) + distance(dip, tip)
    direct_distance = distance(mcp, tip)
    straightness_score = normalize(direct_distance / max(chain_length, 0.0001), 0.55, 0.98)
    pip_angle_score = normalize(angle_at(mcp, pip, dip), 95.0, 175.0)
    dip_angle_score = normalize(angle_at(pip, dip, tip), 110.0, 175.0)
    joint_score = average([pip_angle_score, dip_angle_score])
    reach_score = normalize((distance(wrist, tip) - distance(wrist, mcp)) / palm_size, 0.12, 1.05)
    score = clamp(straightness_score * 0.5 + joint_score * 0.35 + reach_score * 0.15, 0.0, 1.0)

    return {
        "key": finger.key,
        "label": finger.label,
        "score": score,
        "state": get_finger_state_label(score),
        "tipIndex": finger.tip,
    }


def analyze_fingers(landmarks: list[Any]) -> dict:
    palm_size = get_palm_size(landmarks)
    finger_details = [analyze_finger(landmarks, palm_size, finger) for finger in FINGER_CONFIGS]
    finger_scores = {detail["key"]: detail["score"] for detail in finger_details}
    return {"fingerDetails": finger_details, "fingerScores": finger_scores}


def _valid_rock(scores: dict[str, float]) -> bool:
    values = get_finger_score_values(scores)
    return average(values) <= 0.50 and max(values) <= 0.64


def _valid_paper(scores: dict[str, float]) -> bool:
    values = get_finger_score_values(scores)
    return average(values) >= 0.61 and min(values) >= 0.52


def _valid_scissors(scores: dict[str, float]) -> bool:
    open_avg = average([scores["index"], scores["middle"]])
    closed_avg = average([scores["ring"], scores["pinky"]])
    return (
        min(scores["index"], scores["middle"]) >= 0.56
        and max(scores["ring"], scores["pinky"]) <= 0.62
        and open_avg - closed_avg >= 0.10
    )


GESTURE_TEMPLATES = [
    {"name": "石头", "targets": {"index": 0.32, "middle": 0.32, "ring": 0.32, "pinky": 0.32}, "validate": _valid_rock},
    {"name": "布", "targets": {"index": 0.72, "middle": 0.72, "ring": 0.72, "pinky": 0.72}, "validate": _valid_paper},
    {"name": "剪刀", "targets": {"index": 0.72, "middle": 0.72, "ring": 0.32, "pinky": 0.32}, "validate": _valid_scissors},
]


def score_template(finger_scores: dict[str, float], template: dict) -> float:
    total_diff = 0.0
    for finger in FINGER_CONFIGS:
        total_diff += abs(finger_scores[finger.key] - template["targets"][finger.key])
    return clamp(1.0 - total_diff / len(FINGER_CONFIGS), 0.0, 1.0)


def build_template_evaluations(finger_scores: dict[str, float]) -> list[dict]:
    evaluations = [
        {
            "name": template["name"],
            "score": score_template(finger_scores, template),
            "valid": template["validate"](finger_scores),
        }
        for template in GESTURE_TEMPLATES
    ]
    return sorted(evaluations, key=lambda item: item["score"], reverse=True)


def recognize_gesture(landmarks: list[Any]) -> dict:
    analysis = analyze_fingers(landmarks)
    finger_details = analysis["fingerDetails"]
    finger_scores = analysis["fingerScores"]
    template_evaluations = build_template_evaluations(finger_scores)
    best = template_evaluations[0] if template_evaluations else {"name": "未识别", "score": 0.0, "valid": False}
    second = template_evaluations[1] if len(template_evaluations) > 1 else {"name": "未识别", "score": 0.0, "valid": False}
    confidence = clamp(
        best["score"] * 0.72 + max(best["score"] - second["score"], 0.0) * 0.6 + (0.12 if best["valid"] else 0.0),
        0.0,
        0.99,
    )
    accepted = bool(best["valid"] and confidence >= ACCEPTED_CONFIDENCE)
    template_scores = {item["name"]: item["score"] for item in template_evaluations}
    template_validity = {item["name"]: item["valid"] for item in template_evaluations}
    return {
        "name": best["name"] if accepted else "未识别",
        "confidence": confidence if accepted else min(confidence, ACCEPTED_CONFIDENCE - 0.01),
        "candidateName": best["name"],
        "accepted": accepted,
        "fingerDetails": finger_details,
        "fingerScores": finger_scores,
        "templateScores": template_scores,
        "templateValidity": template_validity,
    }


def is_accepted_gesture(gesture: dict | None) -> bool:
    return bool(gesture and gesture.get("name") != "未识别" and gesture.get("confidence", 0) >= ACCEPTED_CONFIDENCE)


def get_debug_lines(gesture: dict) -> list[str]:
    lines = ["四指伸展分数(拇指不参与判定):"]
    for finger in gesture["fingerDetails"]:
        lines.append(f"  {finger['label']}: {finger['score']:.2f} ({finger['state']})")
    scores = gesture["templateScores"]
    validity = gesture["templateValidity"]
    lines.append(
        f"模板匹配: 石头 {round(scores.get('石头', 0) * 100)}% | "
        f"布 {round(scores.get('布', 0) * 100)}% | "
        f"剪刀 {round(scores.get('剪刀', 0) * 100)}%"
    )
    lines.append(
        f"模板约束: 石头 {'√' if validity.get('石头') else '×'} | "
        f"布 {'√' if validity.get('布') else '×'} | "
        f"剪刀 {'√' if validity.get('剪刀') else '×'}"
    )
    if not gesture["accepted"] and gesture.get("candidateName") and gesture["candidateName"] != "未识别":
        lines.append(f"候选手势: {gesture['candidateName']}，但未达到统一判定阈值")
    return lines
