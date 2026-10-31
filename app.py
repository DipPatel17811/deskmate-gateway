"""Desk Mate gateway: WebSocket audio in, streaming audio out.

Run from deskmate-server:
    python -m uvicorn app:app --host 0.0.0.0 --port 8765

Modes (set MODE in .env):
    echo  raw PCM is looped back; no providers, used to test the device path
    mock  offline STT -> LLM -> TTS; no keys and no network
    auto  real providers where keys exist, mock otherwise
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

import actions
import health_db
import settings
import stats
import wellness
from audio_utils import chunks
from dashboard import DASHBOARD_HTML
from protocol import split_sentences
from providers import Pipeline, build_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("deskmate")

app = FastAPI(title="Calmi Gateway")
PIPELINE: Pipeline = build_pipeline(settings)

# "Hello Calmi" is detected on the server from a short, locally gated speech
# clip. The firmware only uploads when its own energy gate sees speech.
WAKE_RE = re.compile(
    r"(?:^|\b(?:hello|hi|hey|okay|ok)\s+)"
    r"(?:calmi|calmie|calmy|calm me|calmee|karmi)\b",
    re.IGNORECASE,
)


def _normalize_wake(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


async def _prewarm_tts() -> None:
    """Edge TTS pays a multi-second cost on its first connection; hide it."""
    try:
        if PIPELINE.tts is not None and PIPELINE.tts.name == "edge":
            await PIPELINE.tts.synthesize("Hello.")
    except Exception:  # noqa: BLE001 - warmup is best effort
        pass


@app.on_event("startup")
async def _on_startup() -> None:
    await health_db.init()
    asyncio.create_task(_prewarm_tts())


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    await health_db.close()



@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, **PIPELINE.describe()}


def _service_spec() -> dict[str, Any]:
    """Public, secret-free description of the running gateway."""
    db_ready = health_db.ready()
    return {
        **PIPELINE.describe(),
        "sample_rate": settings.SAMPLE_RATE,
        "frame_ms": int(settings.FRAME_SAMPLES * 1000 / settings.SAMPLE_RATE),
        "tts_pace": settings.TTS_PACE,
        "tts_prebuffer": settings.TTS_PREBUFFER,
        "device_token_set": bool(settings.DEVICE_TOKEN),
        "database": db_ready,
        "wake_word": "Hello Calmi",
        "proto": 1,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    return DASHBOARD_HTML


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    return stats.snapshot(PIPELINE.describe(), _service_spec())


@app.get("/api/readings")
async def api_readings(limit: int = 25) -> dict[str, Any]:
    return {"readings": await health_db.recent(limit)}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    client = f"{ws.client.host}:{ws.client.port}" if ws.client else "unknown"
    log.info("device connected (%s)", ws.client)
    session = Session(ws, PIPELINE, client)
    stats.session_open(session.sid, client)
    try:
        await session.run()
    finally:
        stats.session_close(session.sid)
    log.info("device disconnected")


class Session:
    _next_id = 0

    def __init__(self, ws: WebSocket, pipeline: Pipeline, client: str = "unknown") -> None:
        Session._next_id += 1
        self.sid = f"s{Session._next_id}"
        self.client = client
        self.ws = ws
        self.pipeline = pipeline
        self.pcm = bytearray()
        self.wake_buf = bytearray()
        self.wake_active = False
        self.heartbeat: asyncio.Task[None] | None = None
        self.turn: asyncio.Task[None] | None = None
        self.history: list[dict[str, str]] = []
        self.health: dict[str, Any] = {}
        self.health_focus = "wellness"
        self.device = "deskmate"
        self.authorized = not bool(settings.DEVICE_TOKEN)
        self.speaker = True

    def _token_ok(self, token: Any) -> bool:
        if not settings.DEVICE_TOKEN:
            return True
        if not isinstance(token, str):
            return False
        return hmac.compare_digest(token.encode("utf-8"), settings.DEVICE_TOKEN.encode("utf-8"))

    async def _reject_unauthorized(self) -> None:
        log.warning("rejected unauthorized device (%s)", self.ws.client)
        await self._send_json({"type": "error", "message": "unauthorized"})
        try:
            await self.ws.close(code=4401)
        except RuntimeError:
            pass

    def _history_snapshot(self) -> list[dict[str, str]]:
        return [dict(turn) for turn in self.history]

    def _remember_turn(self, prompt: str, answer: str) -> None:
        if settings.MAX_HISTORY_TURNS <= 0:
            self.history.clear()
            return
        self.history.append({"role": "user", "content": prompt})
        self.history.append({"role": "assistant", "content": answer})
        limit = settings.MAX_HISTORY_TURNS * 2
        if len(self.history) > limit:
            del self.history[: len(self.history) - limit]

    def _reset_history(self) -> None:
        self.history.clear()

    async def run(self) -> None:
        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("text") is not None:
                    await self._on_control(message["text"])
                elif message.get("bytes"):
                    await self._on_audio(message["bytes"])
        except WebSocketDisconnect:
            pass
        finally:
            if self.heartbeat is not None and not self.heartbeat.done():
                self.heartbeat.cancel()
            await self._cancel_turn()

    async def _heartbeat_loop(self) -> None:
        """Application-level keepalive.

        Protocol ping frames were being dropped somewhere between Render's proxy
        and the device, which killed healthy sockets after ~25 s. A normal text
        frame survives every proxy we tested.
        """
        try:
            while True:
                await asyncio.sleep(15)
                await self._send_json({"type": "ping", "ts": int(time.time() * 1000)})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the receive loop reports the disconnect
            return

    async def _send_json(self, payload: dict[str, Any]) -> None:
        try:
            raw = json.dumps(payload, separators=(",", ":"))
            await self.ws.send_text(raw)
            stats.add_out(len(raw))
            stats.session_bytes(self.sid, sent=len(raw))
            if payload.get("type") == "error":
                stats.count_error()
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def _send_bytes(self, data: bytes) -> None:
        try:
            await self.ws.send_bytes(data)
            stats.add_out(len(data))
            stats.session_bytes(self.sid, sent=len(data))
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def _on_control(self, raw: str) -> None:
        stats.add_in(len(raw))
        stats.session_bytes(self.sid, received=len(raw))
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_json({"type": "error", "message": "invalid json"})
            return
        if not isinstance(msg, dict):
            return

        kind = msg.get("type")
        if kind == "pong":
            return
        if kind == "hello":
            if not self._token_ok(msg.get("token")):
                await self._reject_unauthorized()
                return
            self.authorized = True
            speaker = msg.get("speaker")
            if isinstance(speaker, bool):
                self.speaker = speaker
            name = msg.get("device")
            if isinstance(name, str) and name.strip():
                self.device = name.strip()[:64]
            stats.session_hello(self.sid, self.device, True)
            stats.event("hello", self.device)
            await self._send_json({"type": "hello_ack", "proto": 1, **self.pipeline.describe()})
            if self.heartbeat is None or self.heartbeat.done():
                self.heartbeat = asyncio.create_task(self._heartbeat_loop())
            return

        if not self.authorized:
            await self._reject_unauthorized()
            return

        if kind == "speech_start":
            await self._cancel_turn()
            self.pcm.clear()
            await self._send_json({"type": "asr_partial", "text": ""})
        elif kind == "speech_end":
            await self._finish_speech()
        elif kind == "cancel":
            await self._cancel_turn()
            self.pcm.clear()
            self.wake_active = False
            self.wake_buf.clear()
            await self._send_json({"type": "cancel_ack"})
        elif kind in ("new_chat", "newchat", "reset"):
            await self._cancel_turn()
            self.pcm.clear()
            self._reset_history()
            await self._send_json({"type": "chat_reset"})
        elif kind == "speaker":
            on = msg.get("on")
            if isinstance(on, bool):
                self.speaker = on
                if not on:
                    await self._cancel_turn()
                await self._send_json({"type": "speaker_ack", "on": on})
        elif kind == "wake_start":
            self.wake_active = True
            self.wake_buf.clear()
        elif kind == "wake_end":
            await self._finish_wake()
        elif kind == "text":
            prompt = msg.get("text")
            if not isinstance(prompt, str) or not prompt.strip():
                await self._send_json({"type": "error", "message": "empty text message"})
                return
            prompt = prompt.strip()
            await self._cancel_turn()
            self.pcm.clear()
            log.info("text: %s", prompt[:200])
            stats.event("text", prompt[:120])
            self.turn = asyncio.create_task(self._run_text_turn(prompt))
        elif kind == "health_report":
            await self._on_health_report(msg)
        elif kind == "action_ack":
            log.info(
                "action_ack: %s ok=%s %s",
                msg.get("action"), msg.get("ok"), msg.get("error", ""),
            )

    async def _on_audio(self, data: bytes) -> None:
        stats.add_in(len(data))
        stats.session_bytes(self.sid, received=len(data))
        if self.wake_active:
            limit = settings.SAMPLE_RATE * 2 * 4
            if len(self.wake_buf) < limit:
                self.wake_buf.extend(data[: limit - len(self.wake_buf)])
            return
        limit = settings.MAX_UTTERANCE_SECONDS * settings.SAMPLE_RATE * 2
        if len(self.pcm) < limit:
            self.pcm.extend(data[: limit - len(self.pcm)])
        if self.pipeline.mode == "echo":
            await self._send_bytes(data)

    async def _finish_wake(self) -> None:
        self.wake_active = False
        audio = bytes(self.wake_buf)
        self.wake_buf.clear()
        matched = False
        transcript = ""
        if audio and self.pipeline.stt is not None:
            try:
                transcript = (await self.pipeline.stt.transcribe(audio)).strip()
                matched = bool(WAKE_RE.search(_normalize_wake(transcript)))
            except Exception as exc:  # noqa: BLE001 - wake is best effort
                log.warning("wake check failed: %s", exc)
        stats.count_wake(matched)
        if matched:
            stats.event("wake", transcript[:120])
            log.info("wake word matched: %s", transcript[:120])
        await self._send_json({"type": "wake_result", "matched": matched})

    async def _on_health_report(self, report: dict[str, Any]) -> None:
        bpm = report.get("bpm")
        spo2 = report.get("spo2")
        if not isinstance(bpm, (int, float)) or bpm <= 0:
            await self._send_json(
                {"type": "health_ack", "id": report.get("id"), "ok": False,
                 "message": "invalid health report"}
            )
            return

        # SpO2 is optional: the device may finish a checkup with heart rate only.
        if not isinstance(spo2, (int, float)) or spo2 <= 0:
            spo2 = None
            report["spo2_available"] = False
        report["spo2"] = spo2

        self.health = dict(report)
        stats.count_health()
        stats.event(
            "health",
            f"{report.get('bpm')} bpm / spo2 {report.get('spo2') or '--'}",
        )
        await self._cancel_turn()
        self.pcm.clear()
        await self._send_json(
            {"type": "health_ack", "id": report.get("id"), "ok": True,
             "spo2_available": report.get("spo2_available", True)}
        )
        log.info(
            "health: bpm=%s spo2=%s available=%s signal=%s",
            report.get("bpm"), report.get("spo2"),
            report.get("spo2_available", True), report.get("signal_quality"),
        )
        self.turn = asyncio.create_task(self._run_health_turn(report))

    async def _run_health_turn(self, report: dict[str, Any]) -> None:
        t0 = _now_ms()
        # A focus applies only to the checkup the user just asked for.
        focus = self.health_focus
        self.health_focus = "wellness"
        try:
            if self.pipeline.llm is None or self.pipeline.tts is None:
                await self._send_json({"type": "error", "message": "health reply unavailable"})
                await self._send_json({"type": "tts_end"})
                return
            if await health_db.add_reading(self.device, report):
                history = await health_db.summary(self.device)
                if history:
                    report["server_history"] = history
                    log.info("health history: %s saved readings", history.get("samples"))
            prompt = wellness.build_prompt(report, focus=focus)
            await self._speak(prompt, t0, t0, remember=False, kind="health")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the device UI
            if isinstance(exc, ExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            log.exception("health turn failed: %s", exc)
            await self._send_json(
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"[:200]}
            )
            await self._send_json({"type": "tts_end"})

    async def _finish_speech(self) -> None:
        if self.pipeline.mode == "echo":
            log.info("echo turn: %.2f s of audio", len(self.pcm) / 2 / settings.SAMPLE_RATE)
            await self._send_json({"type": "tts_end"})
            await self._send_json(
                {
                    "type": "metrics",
                    "stt_ms": 0,
                    "llm_ms": 0,
                    "tts_ms": 0,
                    "total_ms": 0,
                    "audio_ms": len(self.pcm) * 1000 // (settings.SAMPLE_RATE * 2),
                }
            )
            self.pcm.clear()
            return

        if not self.pcm:
            await self._send_json({"type": "error", "message": "no audio received"})
            return

        pcm = bytes(self.pcm)
        self.pcm.clear()
        await self._cancel_turn()
        self.turn = asyncio.create_task(self._run_turn(pcm))

    async def _cancel_turn(self) -> None:
        if self.turn is not None and not self.turn.done():
            self.turn.cancel()
        self.turn = None

    async def _run_action(self, action: dict, t0: float, t_stt: float) -> None:
        action_id = f"act-{int(time.time() * 1000)}"
        focus = action.get("focus", "wellness")
        self.health_focus = focus
        await self._send_json({
            "type": "action",
            "id": action_id,
            "action": action["name"],
            "focus": focus,
            "duration_s": action["duration_s"],
        })
        log.info("action: %s (%s)", action["name"], action_id)
        stats.count_action()
        stats.event("action", action["name"])
        await self._speak(action["prompt"], t0, t_stt, remember=False, kind="action")

    async def _run_health_summary(self, t0: float, t_stt: float) -> None:
        try:
            history = await health_db.summary(self.device)
            log.info(
                "health summary requested: %s saved readings",
                (history or {}).get("samples", 0),
            )
            await self._speak(wellness.build_history_prompt(history), t0, t_stt, remember=False, kind="survey")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the device UI
            if isinstance(exc, ExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            log.exception("health summary failed: %s", exc)
            await self._send_json(
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"[:200]}
            )
            await self._send_json({"type": "tts_end"})

    async def _run_turn(self, pcm: bytes) -> None:
        t0 = _now_ms()
        try:
            transcript = (await self.pipeline.stt.transcribe(pcm)).strip()
            t_stt = _now_ms()

            if not transcript:
                await self._send_json({"type": "error", "message": "no speech detected"})
                return

            await self._send_json({"type": "asr_final", "text": transcript})
            log.info("asr: %s", transcript)
            action = actions.detect_action(transcript)
            if action:
                if action["name"] == "health_summary":
                    await self._run_health_summary(t0, t_stt)
                else:
                    await self._run_action(action, t0, t_stt)
                return
            await self._speak(transcript, t0, t_stt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the device UI
            if isinstance(exc, ExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            log.exception("turn failed: %s", exc)
            await self._send_json({"type": "error", "message": f"{type(exc).__name__}: {exc}"[:200]})
            await self._send_json({"type": "tts_end"})

    async def _run_text_turn(self, prompt: str) -> None:
        t0 = _now_ms()
        try:
            action = actions.detect_action(prompt)
            if action:
                if action["name"] == "health_summary":
                    await self._run_health_summary(t0, t0)
                else:
                    await self._run_action(action, t0, t0)
                return
            await self._speak(prompt, t0, t0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the device UI
            if isinstance(exc, ExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            log.exception("text turn failed: %s", exc)
            await self._send_json({"type": "error", "message": f"{type(exc).__name__}: {exc}"[:200]})
            await self._send_json({"type": "tts_end"})

    async def _speak_text_only(self, prompt: str, t0: float, t_stt: float,
                               remember: bool, kind: str) -> None:
        """No-speaker devices only need the streamed text; skip TTS entirely."""
        answer_parts: list[str] = []
        llm_ms = 0
        try:
            async for delta in self.pipeline.llm.stream(prompt, history=self._history_snapshot()):
                if not delta:
                    continue
                if llm_ms == 0:
                    llm_ms = int(_now_ms() - t_stt)
                answer_parts.append(delta)
                await self._send_json({"type": "llm_delta", "text": delta})
            total_ms = int(_now_ms() - t0)
            answer = "".join(answer_parts).strip()
            if answer and remember:
                self._remember_turn(prompt, answer)
            await self._send_json({"type": "tts_start"})
            await self._send_json({"type": "tts_end"})
            await self._send_json(
                {
                    "type": "metrics",
                    "stt_ms": int(t_stt - t0),
                    "llm_ms": llm_ms,
                    "tts_ms": 0,
                    "total_ms": total_ms,
                    "audio_ms": 0,
                }
            )
            stats.session_turn(self.sid)
            stats.add_latency(kind, total_ms, total_ms, 0)
            if kind != "chat":
                stats.event(kind, f"text {total_ms} ms")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the device UI
            if isinstance(exc, ExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            log.exception("text turn failed: %s", exc)
            await self._send_json({"type": "error", "message": f"{type(exc).__name__}: {exc}"[:200]})
            await self._send_json({"type": "tts_end"})

    async def _speak(self, prompt: str, t0: float, t_stt: float, remember: bool = True,
                     kind: str = "chat") -> None:
        assert self.pipeline.llm is not None and self.pipeline.tts is not None
        if not self.speaker:
            await self._speak_text_only(prompt, t0, t_stt, remember, kind)
            return
        history = self._history_snapshot()
        sentence_q: asyncio.Queue[str | None] = asyncio.Queue()
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=8)
        timing: dict[str, float] = {"llm": 0.0, "tts": 0.0, "total": 0.0, "audio": 0.0}
        audio_sent = False
        failure: list[BaseException] = []

        async def produce() -> None:
            buffer = ""
            answer_parts: list[str] = []
            try:
                async for delta in self.pipeline.llm.stream(prompt, history=history):
                    if not delta:
                        continue
                    if timing["llm"] == 0.0:
                        timing["llm"] = _now_ms() - t_stt
                    answer_parts.append(delta)
                    await self._send_json({"type": "llm_delta", "text": delta})
                    buffer += delta
                    sentences, buffer = split_sentences(buffer)
                    for sentence in sentences:
                        await sentence_q.put(sentence)
                if buffer.strip():
                    await sentence_q.put(buffer.strip())
                answer = "".join(answer_parts).strip()
                if answer and remember:
                    self._remember_turn(prompt, answer)
            except BaseException as exc:
                failure.append(exc)
                raise
            finally:
                await sentence_q.put(None)

        async def synthesize() -> None:
            while True:
                sentence = await sentence_q.get()
                if sentence is None:
                    await audio_q.put(None)
                    return
                pcm = await self.pipeline.tts.synthesize(sentence)
                if len(pcm) > 0:
                    await audio_q.put(pcm)

        async def consume() -> None:
            nonlocal audio_sent
            frames = 0
            prebuffer = int(
                settings.TTS_PREBUFFER * settings.SAMPLE_RATE / settings.FRAME_SAMPLES
            )
            prebuffer_bytes = prebuffer * settings.FRAME_BYTES
            pending = bytearray()
            done = False
            pace_start: float | None = None
            pace_frames = 0
            frame_seconds = settings.FRAME_SAMPLES / settings.SAMPLE_RATE

            # Pre-roll: hold playback until we have enough synthesized audio
            # to cover the gap before the next sentence arrives. Without this,
            # the first short sentence plays, the queue runs dry while the
            # next sentence is still being synthesized, and the device hears
            # a cut. If the whole reply is shorter than the prebuffer, wait
            # for synthesis to finish and play it continuously.
            while not done and len(pending) < prebuffer_bytes:
                pcm = await audio_q.get()
                if pcm is None:
                    done = True
                else:
                    pending.extend(pcm)

            while True:
                while len(pending) >= settings.FRAME_BYTES:
                    chunk = bytes(pending[: settings.FRAME_BYTES])
                    del pending[: settings.FRAME_BYTES]
                    if not audio_sent:
                        await self._send_json({"type": "tts_start"})
                        timing["tts"] = _now_ms() - t_stt - timing["llm"]
                        timing["total"] = _now_ms() - t0
                        audio_sent = True
                    await self._send_bytes(chunk)
                    timing["audio"] += len(chunk) * 1000.0 / (settings.SAMPLE_RATE * 2)
                    frames += 1
                    if settings.TTS_PACE > 0 and frames > prebuffer:
                        # Pace against an absolute clock. A per-frame
                        # asyncio.sleep(20ms) actually takes ~30ms on Windows
                        # and makes the stream run at ~half real time, which
                        # drains the device ring and chops the speech.
                        if pace_start is None:
                            pace_start = time.monotonic()
                        pace_frames += 1
                        target = pace_start + pace_frames * frame_seconds / settings.TTS_PACE
                        delay = target - time.monotonic()
                        if delay > 0:
                            await asyncio.sleep(delay)
                if done:
                    break
                pcm = await audio_q.get()
                if pcm is None:
                    done = True
                else:
                    pending.extend(pcm)

            if pending:
                if not audio_sent:
                    await self._send_json({"type": "tts_start"})
                    timing["tts"] = _now_ms() - t_stt - timing["llm"]
                    timing["total"] = _now_ms() - t0
                    audio_sent = True
                await self._send_bytes(bytes(pending))
                timing["audio"] += len(pending) * 1000.0 / (settings.SAMPLE_RATE * 2)

            if not audio_sent:
                if failure:
                    return
                await self._send_json({"type": "error", "message": "empty reply"})
            log.info(
                "turn: stt %d ms, llm %d ms, tts %d ms, ttfa %d ms, audio %d ms",
                int(t_stt - t0), int(timing["llm"]), int(timing["tts"]),
                int(timing["total"]), int(timing["audio"]),
            )
            await self._send_json({"type": "tts_end"})
            await self._send_json(
                {
                    "type": "metrics",
                    "stt_ms": int(t_stt - t0),
                    "llm_ms": int(timing["llm"]),
                    "tts_ms": int(timing["tts"]),
                    "total_ms": int(timing["total"]),
                    "audio_ms": int(timing["audio"]),
                }
            )
            stats.session_turn(self.sid)
            stats.add_latency(
                kind,
                int(timing["total"]),
                int(timing["total"]),
                int(timing["audio"]),
            )
            if kind != "chat":
                stats.event(kind, f"ttfa {int(timing['total'])} ms")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(produce())
            tg.create_task(synthesize())
            tg.create_task(consume())
