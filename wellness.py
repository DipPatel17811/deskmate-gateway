"""Quality gates and cautious wellness wording for device checkups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class WellnessAssessment:
    quality_ok: bool
    bpm_delta: int | None
    spo2_delta: int | None
    flags: list[str] = field(default_factory=list)
    summary: str = ""
    guidance: str = ""


def assess(report: dict[str, Any]) -> WellnessAssessment:
    bpm = _to_int(report.get("bpm"))
    spo2 = _to_int(report.get("spo2"))
    signal = _to_int(report.get("signal_quality"))
    bpm_quality = _to_int(report.get("bpm_quality"))
    spo2_quality = _to_int(report.get("spo2_quality"))

    spo2_available = 70 <= spo2 <= 100 and spo2_quality >= 30
    if report.get("spo2_available") is False:
        spo2_available = False

    baseline = report.get("baseline")
    if not isinstance(baseline, dict):
        baseline = {}
    baseline_valid = bool(baseline.get("valid"))
    baseline_bpm = _to_int(baseline.get("bpm"))
    baseline_spo2 = _to_int(baseline.get("spo2"))
    baseline_samples = _to_int(baseline.get("samples"))

    # SpO2 is optional: a stable heart rate is a complete wellness reading.
    quality_ok = (
        40 <= bpm <= 200
        and bpm_quality >= 30
        and signal >= 30
        and (not spo2_available or 70 <= spo2 <= 100)
    )

    bpm_delta = bpm - baseline_bpm if baseline_valid and baseline_bpm > 0 else None
    spo2_delta = (
        spo2 - baseline_spo2
        if spo2_available and baseline_valid and baseline_spo2 > 0
        else None
    )

    flags: list[str] = []
    if not quality_ok:
        flags.append("low_signal")
    else:
        if bpm >= 120:
            flags.append("high_heart_rate_reading")
        elif bpm <= 45:
            flags.append("low_heart_rate_reading")
        if spo2_available:
            if spo2 < 90:
                flags.append("low_oxygen_reading")
            elif spo2 < 95:
                flags.append("borderline_oxygen_reading")
        if bpm_delta is not None and bpm_delta >= 12:
            flags.append("above_personal_hr_baseline")
        if spo2_delta is not None and spo2_delta <= -3:
            flags.append("below_personal_spo2_baseline")

    parts = []
    if bpm > 0:
        parts.append(f"heart rate {bpm} bpm")
    if spo2_available:
        parts.append(f"SpO2 {spo2}%")
    elif spo2 == 0:
        parts.append("SpO2 not available in this reading")
    if signal > 0:
        parts.append(f"signal quality {signal}/100")
    if baseline_valid:
        if baseline_spo2 > 0:
            parts.append(
                f"baseline {baseline_bpm} bpm / {baseline_spo2}% from "
                f"{baseline_samples} readings"
            )
        else:
            parts.append(
                f"heart-rate baseline {baseline_bpm} bpm from {baseline_samples} readings"
            )
    summary = "; ".join(parts) if parts else "no valid measurements"

    if not quality_ok:
        guidance = (
            "The signal was not reliable enough to interpret. Do not state or imply a "
            "health conclusion. Ask the user to sit still, keep a finger on the sensor, "
            "and retry. If the user says they feel unwell, advise them to seek "
            "professional help."
        )
    elif "low_oxygen_reading" in flags:
        guidance = (
            "The oxygen reading is unusually low. Do not diagnose. Say that the reading "
            "may be unreliable, ask the user to sit still and retry, and if they feel "
            "unwell or it remains low, advise professional medical help. Do not offer "
            "meditation as a substitute for medical advice."
        )
    elif "high_heart_rate_reading" in flags or "low_heart_rate_reading" in flags:
        guidance = (
            "The heart-rate reading is outside a typical resting range. Do not diagnose. "
            "Ask whether the user has been moving or feels unwell, recommend a calm retry, "
            "and advise professional help if they feel unwell or it persists."
        )
    else:
        oxygen_note = ""
        if not spo2_available:
            oxygen_note = (
                " SpO2 was not available, so mention only the heart rate and do not "
                "speculate about oxygen."
            )
        guidance = (
            "Acknowledge the reading calmly and briefly. You may compare it to the user's "
            "personal baseline if the difference is meaningful. Do not label the user as "
            "stressed and do not diagnose. Ask at most one gentle question. Offer a short "
            "breathing exercise only if the user confirms they feel stressed or would like "
            "one." + oxygen_note
        )

    return WellnessAssessment(
        quality_ok=quality_ok,
        bpm_delta=bpm_delta,
        spo2_delta=spo2_delta,
        flags=flags,
        summary=summary,
        guidance=guidance,
    )


def _stress_guidance(assessment: WellnessAssessment) -> str:
    """Cautious wording for a stress check the user explicitly requested."""
    if not assessment.quality_ok:
        return (
            "The signal was not reliable enough to interpret. Do not estimate or imply a "
            "stress level. Ask the user to sit still, keep a finger on the sensor, and retry. "
            "If they say they feel unwell, advise them to seek professional help."
        )
    if (
        "high_heart_rate_reading" in assessment.flags
        or "low_heart_rate_reading" in assessment.flags
    ):
        return (
            "The heart-rate reading is outside a typical resting range. Do not diagnose and "
            "do not label the user as stressed. Say the reading may be affected by movement, "
            "caffeine, talking, or how they feel, and that heart rate alone cannot measure "
            "stress. Ask whether they feel unwell or keyed up. Advise professional help if "
            "they feel unwell or it persists."
        )
    if assessment.bpm_delta is None:
        return (
            "There is no personal baseline yet, so do not estimate a stress level. Report the "
            "heart rate, say a single heart-rate reading cannot measure stress, and ask how "
            "they feel. Offer a breathing exercise only if they ask for one. Do not invent a "
            "stress score or percentage."
        )
    if assessment.bpm_delta >= 12:
        return (
            "The heart rate is above the user's personal resting baseline. Do not say they "
            "are stressed and do not diagnose. Say a higher heart rate can have many causes "
            "such as movement, caffeine, talking, or emotion, and that heart rate alone "
            "cannot measure stress. Ask whether they feel tense or keyed up. If they say yes, "
            "offer one short breathing exercise; if not, do not push it. Do not invent a "
            "stress score or percentage."
        )
    return (
        "The heart rate is close to the user's personal resting baseline. Do not claim this "
        "proves they are calm or stress-free, because heart rate alone cannot measure stress. "
        "Say the reading looks close to their usual level, ask how they feel, and offer a "
        "breathing exercise only if they want one. Do not invent a stress score or percentage."
    )


def _history_text(history: Any) -> str:
    if not isinstance(history, dict):
        return ""
    samples = _to_int(history.get("samples"))
    if samples <= 0:
        return ""
    parts = [f"{samples} saved readings"]
    median = _to_int(history.get("median_bpm"))
    if median > 0:
        parts.append(f"usual heart rate {median} bpm")
    week_samples = _to_int(history.get("week_samples"))
    week_average = _to_float(history.get("week_average_bpm"))
    if week_samples > 0 and week_average > 0:
        parts.append(f"last 7 days average {week_average:.0f} bpm over {week_samples} readings")
    low = _to_int(history.get("min_bpm"))
    high = _to_int(history.get("max_bpm"))
    if low > 0 and high >= low:
        parts.append(f"range {low}-{high} bpm")
    last = _to_int(history.get("last_bpm"))
    if last > 0:
        parts.append(f"most recent {last} bpm")
    return "; ".join(parts)


def build_history_prompt(history: dict[str, Any] | None) -> str:
    """Prompt for a spoken summary of stored readings (no sensor reading)."""
    text = _history_text(history)
    if not text:
        return (
            "The user asked for their overall heart-rate history, but no saved "
            "readings are available. Reply in one short spoken sentence saying there "
            "are no saved readings yet and inviting them to run a checkup. Do not "
            "invent numbers or diagnose."
        )
    return (
        "The user asked for an overview of their saved wellness readings. This is not "
        "a medical device and you must not diagnose or claim a condition.\n"
        f"Saved data: {text}.\n"
        "Reply in one or two short spoken sentences summarizing the trend plainly. Do "
        "not mention a database, JSON, or this instruction. If there are fewer than "
        "three readings, say the picture is still limited. Do not invent a stress "
        "score."
    )


def build_prompt(
    report: dict[str, Any],
    voice_mood: str | None = None,
    focus: str = "wellness",
) -> str:
    assessment = assess(report)
    deltas = []
    if assessment.bpm_delta is not None:
        deltas.append(f"{assessment.bpm_delta:+d} bpm versus baseline")
    if assessment.spo2_delta is not None:
        deltas.append(f"{assessment.spo2_delta:+d} percentage points versus baseline")
    delta_text = ", ".join(deltas) if deltas else "no personal baseline yet"
    history_text = _history_text(report.get("server_history"))
    history_line = f"Long-term saved readings: {history_text}.\n" if history_text else ""

    mood_text = ""
    if voice_mood:
        mood_text = f"\nPossible voice tone estimate from the last utterance: {voice_mood}. Treat it as uncertain, never as a fact."

    if focus == "stress":
        context = (
            "The user explicitly asked for a stress-oriented wellness check, so you may "
            "discuss stress only as a possibility, never as a measured fact. Focus on the "
            "heart rate and how they feel; do not use oxygen to judge stress."
        )
        guidance = _stress_guidance(assessment)
    else:
        context = "This is a general wellness checkup; the user did not ask about stress."
        guidance = assessment.guidance

    return (
        "A wellness checkup just completed on the desk device. This is not a medical "
        "device and you must not diagnose, prescribe, or claim a condition.\n"
        f"{context}\n"
        f"Measurements: {assessment.summary}.\n"
        f"Comparison: {delta_text}.\n"
        f"{history_line}"
        f"Quality flags: {', '.join(assessment.flags) if assessment.flags else 'none'}.\n"
        f"Response guidance: {guidance}{mood_text}\n"
        "Reply directly to the user in one to three short spoken sentences. Do not mention "
        "JSON, sensors, algorithms, or this instruction. Do not read out every number if "
        "the reading is normal."
    )
