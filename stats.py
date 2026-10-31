"""In-memory gateway telemetry for the live dashboard.

Everything here is best effort and lock-protected: a slow dashboard request must
never block an audio turn. No secrets are stored; only counts, timings, device
names, and short event strings.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

STARTED = time.time()
VERSION = "2.0.0"

_lock = threading.Lock()
_bytes_in = 0
_bytes_out = 0
_turns = 0
_actions = 0
_health_reports = 0
_errors = 0
_wake_checks = 0
_wake_hits = 0
_latencies: deque[dict[str, int]] = deque(maxlen=50)
_sessions: dict[str, dict[str, Any]] = {}
_events: deque[dict[str, Any]] = deque(maxlen=120)


def _iso(ts: float | None = None) -> str:
    moment = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return moment.isoformat(timespec="seconds")


def add_in(count: int) -> None:
    global _bytes_in
    if count <= 0:
        return
    with _lock:
        _bytes_in += count


def add_out(count: int) -> None:
    global _bytes_out
    if count <= 0:
        return
    with _lock:
        _bytes_out += count


def event(kind: str, detail: str = "") -> None:
    with _lock:
        _events.appendleft(
            {"at": _iso(), "kind": kind, "detail": detail[:180]}
        )


def session_open(session_id: str, client: str) -> None:
    with _lock:
        _sessions[session_id] = {
            "id": session_id,
            "client": client,
            "device": "unknown",
            "connected_at": time.time(),
            "bytes_in": 0,
            "bytes_out": 0,
            "turns": 0,
            "last_seen": time.time(),
            "authorized": False,
        }
        _events.appendleft(
            {"at": _iso(), "kind": "connect", "detail": client}
        )


def session_hello(session_id: str, device: str, authorized: bool) -> None:
    with _lock:
        entry = _sessions.get(session_id)
        if entry is None:
            return
        entry["device"] = device
        entry["authorized"] = authorized
        entry["last_seen"] = time.time()
        _events.appendleft(
            {"at": _iso(), "kind": "hello", "detail": f"{device} authorized={authorized}"}
        )


def session_close(session_id: str) -> None:
    with _lock:
        entry = _sessions.pop(session_id, None)
        if entry is not None:
            _events.appendleft(
                {
                    "at": _iso(),
                    "kind": "disconnect",
                    "detail": f"{entry['device']} after {int(time.time() - entry['connected_at'])} s",
                }
            )


def session_bytes(session_id: str, sent: int = 0, received: int = 0) -> None:
    with _lock:
        entry = _sessions.get(session_id)
        if entry is None:
            return
        entry["bytes_in"] += received
        entry["bytes_out"] += sent
        entry["last_seen"] = time.time()


def session_turn(session_id: str) -> None:
    global _turns
    with _lock:
        _turns += 1
        entry = _sessions.get(session_id)
        if entry is not None:
            entry["turns"] += 1
            entry["last_seen"] = time.time()


def session_authorized(session_id: str, authorized: bool) -> None:
    with _lock:
        entry = _sessions.get(session_id)
        if entry is not None:
            entry["authorized"] = authorized


def count_action() -> None:
    global _actions
    with _lock:
        _actions += 1


def count_health() -> None:
    global _health_reports
    with _lock:
        _health_reports += 1


def count_error() -> None:
    global _errors
    with _lock:
        _errors += 1


def count_wake(matched: bool) -> None:
    global _wake_checks, _wake_hits
    with _lock:
        _wake_checks += 1
        if matched:
            _wake_hits += 1


def add_latency(kind: str, ttfa_ms: int, total_ms: int, audio_ms: int) -> None:
    with _lock:
        _latencies.append(
            {
                "kind": kind,
                "ttfa_ms": max(0, ttfa_ms),
                "total_ms": max(0, total_ms),
                "audio_ms": max(0, audio_ms),
            }
        )


def _average(values: list[int]) -> int:
    return int(sum(values) / len(values)) if values else 0


def snapshot(pipeline: dict[str, str], service: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        now = time.time()
        sessions = []
        for entry in _sessions.values():
            item = dict(entry)
            item["connected_at"] = _iso(item["connected_at"])
            item["uptime_s"] = int(now - entry["connected_at"])
            item["last_seen_s"] = int(now - entry["last_seen"])
            sessions.append(item)
        sessions.sort(key=lambda item: item["connected_at"])
        latencies = list(_latencies)
        events = list(_events)[:60]

    def avg(key: str) -> int:
        return _average([int(item[key]) for item in latencies])

    return {
        "version": VERSION,
        "started_at": _iso(STARTED),
        "uptime_s": int(now - STARTED),
        "service": service,
        "pipeline": pipeline,
        "totals": {
            "connections": len(_sessions),
            "bytes_in": _bytes_in,
            "bytes_out": _bytes_out,
            "turns": _turns,
            "actions": _actions,
            "health_reports": _health_reports,
            "errors": _errors,
            "wake_checks": _wake_checks,
            "wake_hits": _wake_hits,
        },
        "latency": {
            "samples": len(latencies),
            "ttfa_ms": avg("ttfa_ms"),
            "total_ms": avg("total_ms"),
            "audio_ms": avg("audio_ms"),
        },
        "sessions": sessions,
        "events": events,
        "server_time": _iso(),
    }
