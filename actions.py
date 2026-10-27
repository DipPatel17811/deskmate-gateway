"""Small, deterministic voice intents that control the device."""

from __future__ import annotations

import re

_STRESS_PATTERNS = (
    re.compile(r"\b(am i|do i (?:seem|look|sound))\b.*\b(stressed|stress|anxious|tense)\b"),
    re.compile(r"\b(check|measure|scan|get|read|test|analyse|analyze|detect)\b.*\b(stress|tension|anxiety)\b"),
    re.compile(r"\b(stress|tension|anxiety)\b.*\b(level|check|reading|measure|test|analysis)\b"),
    re.compile(r"\bhow stressed\b"),
)

_HEALTH_PATTERNS = (
    re.compile(r"\b(health|wellness)\b.*\b(check|checkup|scan|test|reading)\b"),
    re.compile(r"\b(check|do|start|begin|run|take)\b.*\b(health|wellness)\b(?! insurance)"),
    re.compile(r"\b(check|measure|take|scan|get|read)\b.*\b(heart|pulse|oxygen|spo2)\b"),
    re.compile(r"\b(heart rate|blood oxygen|oxygen level|pulse|spo2)\b.*\b(check|reading|measure|scan)\b"),
)


def detect_action(text: str) -> dict | None:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    if len(normalized) > 160:
        return None
    for pattern in _STRESS_PATTERNS:
        if pattern.search(normalized):
            return {
                "name": "health_checkup",
                "focus": "stress",
                "duration_s": 30,
                "prompt": (
                    "Reply in one short spoken sentence confirming that a 30-second "
                    "checkup is opening to look at their heart rate, and ask them to keep "
                    "a finger still on the sensor. Do not guess their stress level before "
                    "the reading. Do not diagnose."
                ),
            }
    for pattern in _HEALTH_PATTERNS:
        if pattern.search(normalized):
            return {
                "name": "health_checkup",
                "focus": "wellness",
                "duration_s": 30,
                "prompt": (
                    "Reply in one short spoken sentence confirming that a 30-second "
                    "wellness checkup is opening, and ask the user to keep a finger "
                    "still on the sensor. Do not diagnose or add other advice."
                ),
            }
    return None
