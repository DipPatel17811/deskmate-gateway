"""Optional Postgres storage for wellness readings.

The gateway works without a database: every helper here fails soft and simply
returns no history. Render's free Postgres is enough for a single device, but
the database expires 30 days after creation unless it is upgraded.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import asyncpg

log = logging.getLogger("deskmate.healthdb")

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()
_retry_at = 0.0
_retry_delay = 30.0


def _dsn() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


async def _get_pool() -> asyncpg.Pool | None:
    global _pool, _retry_at
    if _pool is not None:
        return _pool
    dsn = _dsn()
    if not dsn:
        return None
    now = time.monotonic()
    if now < _retry_at:
        return None
    async with _pool_lock:
        if _pool is not None:
            return _pool
        if time.monotonic() < _retry_at:
            return None
        try:
            pool = await asyncpg.create_pool(
                dsn=dsn,
                min_size=1,
                max_size=4,
                timeout=15,
                command_timeout=8,
                ssl=False,
            )
            if pool is None:
                raise RuntimeError("empty pool")
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS health_readings (
                        id BIGSERIAL PRIMARY KEY,
                        device TEXT NOT NULL,
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        bpm INTEGER NOT NULL,
                        spo2 INTEGER,
                        bpm_quality INTEGER,
                        spo2_quality INTEGER,
                        signal_quality INTEGER,
                        temp_c REAL,
                        duration_s INTEGER,
                        source TEXT
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS health_readings_device_time
                    ON health_readings (device, recorded_at DESC)
                    """
                )
            _pool = pool
            _retry_at = 0.0
            log.info("health database ready")
            return _pool
        except Exception as exc:  # noqa: BLE001 - history is best effort
            _retry_at = time.monotonic() + _retry_delay
            log.warning("health database unavailable: %s", exc)
            return None


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def add_reading(device: str, report: dict[str, Any]) -> bool:
    pool = await _get_pool()
    if pool is None:
        return False
    bpm = _int_or_none(report.get("bpm"))
    if bpm is None or bpm < 40 or bpm > 200:
        return False

    spo2 = _int_or_none(report.get("spo2"))
    if spo2 is not None and not 70 <= spo2 <= 100:
        spo2 = None
    temp = report.get("temp_c")
    try:
        temp_value = float(temp) if temp is not None else None
    except (TypeError, ValueError):
        temp_value = None
    if temp_value is not None and temp_value <= 0:
        temp_value = None

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO health_readings
                    (device, bpm, spo2, bpm_quality, spo2_quality,
                     signal_quality, temp_c, duration_s, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                str(device)[:64],
                bpm,
                spo2,
                _int_or_none(report.get("bpm_quality")),
                _int_or_none(report.get("spo2_quality")),
                _int_or_none(report.get("signal_quality")),
                temp_value,
                _int_or_none(report.get("duration_s")),
                str(report.get("source") or "manual")[:32],
            )
        return True
    except Exception as exc:  # noqa: BLE001 - never break a checkup
        log.warning("could not store health reading: %s", exc)
        return False


async def summary(device: str) -> dict[str, Any] | None:
    """Return a compact long-term view for one device, or None if empty."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    count(*) AS samples,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY bpm) AS median_bpm,
                    avg(bpm)::float8 AS average_bpm,
                    min(bpm) AS min_bpm,
                    max(bpm) AS max_bpm,
                    min(recorded_at) AS first_at,
                    count(*) FILTER (
                        WHERE recorded_at > NOW() - INTERVAL '7 days'
                    ) AS week_samples,
                    avg(bpm) FILTER (
                        WHERE recorded_at > NOW() - INTERVAL '7 days'
                    )::float8 AS week_average_bpm
                FROM health_readings
                WHERE device = $1
                  AND recorded_at > NOW() - INTERVAL '90 days'
                """,
                str(device)[:64],
            )
            if row is None or int(row["samples"] or 0) == 0:
                return None
            last = await conn.fetchrow(
                """
                SELECT bpm, recorded_at
                FROM health_readings
                WHERE device = $1
                ORDER BY recorded_at DESC
                LIMIT 1
                """,
                str(device)[:64],
            )
    except Exception as exc:  # noqa: BLE001 - history is best effort
        log.warning("could not read health history: %s", exc)
        return None

    result: dict[str, Any] = {
        "samples": int(row["samples"]),
        "median_bpm": int(round(float(row["median_bpm"]))) if row["median_bpm"] else None,
        "average_bpm": round(float(row["average_bpm"]), 1) if row["average_bpm"] else None,
        "min_bpm": int(row["min_bpm"]) if row["min_bpm"] else None,
        "max_bpm": int(row["max_bpm"]) if row["max_bpm"] else None,
        "week_samples": int(row["week_samples"] or 0),
        "week_average_bpm": (
            round(float(row["week_average_bpm"]), 1) if row["week_average_bpm"] else None
        ),
        "first_at": row["first_at"].isoformat() if row["first_at"] else None,
        "days": (
            max(1, (time.time() - row["first_at"].timestamp()) / 86400)
            if row["first_at"]
            else 0
        ),
    }
    if last is not None:
        result["last_bpm"] = int(last["bpm"])
        result["last_at"] = last["recorded_at"].isoformat() if last["recorded_at"] else None
    week_avg = result.get("week_average_bpm")
    overall = result.get("average_bpm")
    if week_avg is not None and overall is not None:
        result["trend_bpm"] = round(float(week_avg) - float(overall), 1)
    return result


async def recent(limit: int = 25) -> list[dict[str, Any]]:
    """Latest readings for the dashboard, newest first."""
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT device, bpm, spo2, bpm_quality, spo2_quality,
                       signal_quality, duration_s, source, recorded_at
                FROM health_readings
                ORDER BY recorded_at DESC
                LIMIT $1
                """,
                max(1, min(int(limit), 200)),
            )
    except Exception as exc:  # noqa: BLE001 - dashboard only
        log.warning("could not read recent health rows: %s", exc)
        return []
    return [
        {
            "device": row["device"],
            "bpm": int(row["bpm"]),
            "spo2": int(row["spo2"]) if row["spo2"] else None,
            "bpm_quality": int(row["bpm_quality"]) if row["bpm_quality"] else None,
            "spo2_quality": int(row["spo2_quality"]) if row["spo2_quality"] else None,
            "signal_quality": int(row["signal_quality"]) if row["signal_quality"] else None,
            "duration_s": int(row["duration_s"]) if row["duration_s"] else None,
            "source": row["source"],
            "recorded_at": row["recorded_at"].isoformat() if row["recorded_at"] else None,
        }
        for row in rows
    ]


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def init() -> None:
    """Create the pool and table at startup; failures are retried lazily."""
    await _get_pool()


def ready() -> bool:
    return _pool is not None
