"""Offline self-test: boots the gateway in echo and mock modes, runs a fake turn.

    python tests/selftest.py

No API keys and no hardware are required.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audio_utils import tone_pcm  # noqa: E402
from sim_device import run_turn  # noqa: E402

OUT_DIR = ROOT / "tests" / "out"


def wait_for_health(port: int, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"gateway exited early:\n{output}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                print(f"health: {r.read().decode()}")
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.2)
    raise TimeoutError(f"gateway did not become healthy on port {port}")


def run_mode(mode: str, port: int, name: str) -> dict:
    env = os.environ.copy()
    env["MODE"] = mode
    env["PORT"] = str(port)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("GROQ_API_KEY", None)
    env.pop("GEMINI_API_KEY", None)

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_health(port, proc)
        return asyncio.run(
            run_turn(
                f"ws://127.0.0.1:{port}/ws",
                tone_pcm(1.0),
                OUT_DIR / f"{name}.wav",
                realtime=False,
                timeout=30,
            )
        )
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def check(condition: bool, label: str) -> None:
    print(("PASS " if condition else "FAIL ") + label)
    if not condition:
        raise SystemExit(1)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    echo = run_mode("echo", 8799, "echo")
    kinds = {m.get("type") for m in echo["messages"] if isinstance(m, dict)}
    check("hello_ack" in kinds, "echo: handshake")
    check(echo["audio_bytes"] > 0, "echo: loopback audio returned")
    check("tts_end" in kinds, "echo: turn ended")

    mock = run_mode("mock", 8800, "mock")
    kinds = {m.get("type") for m in mock["messages"] if isinstance(m, dict)}
    check("hello_ack" in kinds, "mock: handshake")
    check("asr_final" in kinds, "mock: transcription")
    check("llm_delta" in kinds, "mock: streaming reply")
    check("tts_start" in kinds, "mock: speech started")
    check(mock["audio_bytes"] > 0, "mock: synthesized audio returned")
    check("metrics" in kinds, "mock: metrics reported")
    total = int(mock["metrics"].get("total_ms", -1))
    check(total >= 0, f"mock: total latency {total} ms")

    print("\nAll offline checks passed.")


if __name__ == "__main__":
    main()
