from __future__ import annotations

from dataclasses import dataclass


JOINT_COUNT = 17


@dataclass(frozen=True)
class MotionFrame:
    joints: list[int]
    hold_ms: int = 30

    def __post_init__(self) -> None:
        if len(self.joints) != JOINT_COUNT:
            raise ValueError(f"expected {JOINT_COUNT} joints, got {len(self.joints)}")


def joints_from_hex(hex_bytes: str) -> list[int]:
    raw = bytes.fromhex(hex_bytes)
    if len(raw) != JOINT_COUNT * 2:
        raise ValueError(f"expected {JOINT_COUNT * 2} bytes, got {len(raw)}")
    return [
        int.from_bytes(raw[index : index + 2], byteorder="big", signed=True)
        for index in range(0, len(raw), 2)
    ]




ROCK_POSE = joints_from_hex(
    "02 BC 03 A5 00 00 00 00 00 00 04 B0 04 B0 04 B0 04 B0 "
    "04 B0 04 B0 00 00 00 00 00 00 04 B0 04 B0 00 00"
)



SCISSORS_POSE = joints_from_hex(
    "02 72 04 9D FF F7 00 5C FF 10 04 67 04 C0 00 12 00 02 "
    "04 A8 04 5C FF 8A FE FE 00 77 00 05 00 05 FF EF"
)

PAPER_POSE = joints_from_hex(
    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
)


ROCK_SEQUENCE: list[MotionFrame] = [
    MotionFrame(ROCK_POSE, hold_ms=30),
]

SCISSORS_SEQUENCE: list[MotionFrame] = [
    MotionFrame(SCISSORS_POSE, hold_ms=30),
]

PAPER_SEQUENCE: list[MotionFrame] = [
    MotionFrame(PAPER_POSE, hold_ms=30),
]


GESTURE_SEQUENCES = {
    "石头": ROCK_SEQUENCE,
    "剪刀": SCISSORS_SEQUENCE,
    "布": PAPER_SEQUENCE,
}


def serialize_sequence(sequence: list[MotionFrame]) -> list[dict]:
    return [{"joints": frame.joints, "hold_ms": frame.hold_ms} for frame in sequence]
