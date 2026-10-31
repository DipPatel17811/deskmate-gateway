"""Small, deterministic voice intents that control the device."""

from __future__ import annotations

import re

_STRESS_PATTERNS = (
    re.compile(r"\b(am i|do i (?:seem|look|sound))\b.*\b(stressed|stress|anxious|tense)\b"),
    re.compile(
        r"\b(check|measure|scan|get|read|test|analyse|analyze|detect)\b"
        r".*\b(stress|tension|anxiety)\b"
    ),
    re.compile(r"\b(stress|tension|anxiety)\b.*\b(level|check|reading|measure|test|analysis)\b"),
    re.compile(r"\bhow stressed\b"),
)

_SUMMARY_PATTERNS = (
    re.compile(
        r"\b(heart rate|heart|bpm|pulse|health|wellness|readings?|vitals?)\b"
        r".*\b(history|trend|average|overall|summary|over time|past readings|records)\b"
    ),
    re.compile(
        r"\b(history|trend|average|overall|summary)\b"
        r".*\b(heart rate|heart|bpm|pulse|health|wellness|readings?|vitals?)\b"
    ),
    re.compile(
        r"\bhow (?:has|have|is)\b.*\b(heart rate|heart|bpm|pulse|health)\b"
        r".*\b(been|trending|doing)\b"
    ),
    re.compile(r"\b(past|saved|previous|earlier|recent)\b.*\breadings?\b"),
    re.compile(r"\breadings?\b.*\b(so far|over time|history|trend|average|summary)\b"),
    # "analyze my previous heart rate" asks for a survey of stored data, not a
    # new sensor reading.
    re.compile(
        r"\b(analyse|analyze|analysis|survey|review|summarise|summarize)\b"
        r".*\b(previous|past|saved|recorded|earlier|old|history)\b"
        r".*\b(heart rate|heart|bpm|pulse|readings?|health|wellness)\b"
    ),
    re.compile(
        r"\b(previous|past|saved|recorded|earlier|old)\b"
        r".*\b(heart rate|heart|bpm|pulse|readings?|health|wellness)\b"
        r".*\b(analysis|survey|summary|overview|trend|average)\b"
    ),
    re.compile(
        r"\b(tell me|give me|make|do|run|show)\b"
        r".*\b(survey|overview|summary|analysis)\b"
        r".*\b(heart rate|heart|bpm|pulse|readings?|health|wellness)\b"
    ),
)

_HEALTH_PATTERNS = (
    re.compile(
        r"\b(health|wellness|vitals?)\b"
        r".*\b(check|checkup|scan|test|reading|measure|report|analysis)\b"
    ),
    re.compile(
        r"\b(check|do|start|begin|run|take|measure|read|scan|analyse|analyze)\b"
        r".*\b(health|wellness|vitals?)\b(?! insurance)"
    ),
    re.compile(
        r"\b(check|measure|take|scan|get|read|monitor|analyse|analyze)\b"
        r".*\b(heart rate|heart|pulse|bpm|beats per minute|oxygen|spo2|"
        r"blood oxygen|vitals?)\b"
    ),
    re.compile(
        r"\b(analyse|analyze|analysis|assess|assessment)\b"
        r".*\b(heart rate|heart|bpm|pulse|health|wellness|vitals?)\b"
    ),
    re.compile(
        r"\b(heart rate|heart|pulse|bpm|beats per minute|blood oxygen|"
        r"oxygen level|spo2|vitals?)\b.*\b(check|reading|measure|scan|number|level|analysis)\b"
    ),
    re.compile(
        r"\b(what(?:'s| is)|how(?:'s| is)|tell me)\b"
        r".*\b(bpm|heart rate|pulse|spo2|oxygen|vitals?)\b"
    ),
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
                "duration_s": 60,
                "prompt": (
                    "Reply in one short spoken sentence confirming that a checkup is "
                    "opening to look at their heart rate, and ask them to keep a finger "
                    "still on the sensor for up to a minute. Do not guess their stress "
                    "level before the reading. Do not diagnose."
                ),
            }
    for pattern in _SUMMARY_PATTERNS:
        if pattern.search(normalized):
            return {"name": "health_summary"}
    for pattern in _HEALTH_PATTERNS:
        if pattern.search(normalized):
            return {
                "name": "health_checkup",
                "focus": "wellness",
                "duration_s": 60,
                "prompt": (
                    "Reply in one short spoken sentence confirming that a checkup is "
                    "opening, and ask the user to keep a finger still on the sensor "
                    "for up to a minute. Do not diagnose or add other advice."
                ),
            }
    return None
