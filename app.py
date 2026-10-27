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
import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import actions
import health_db
import settings
import wellness
from audio_utils import chunks
from protocol import split_sentences
from providers import Pipeline, build_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("deskmate")

app = FastAPI(title="Desk Mate Gateway")
PIPELINE: Pipeline = build_pipeline(settings)


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


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    log.info("device connected (%s)", ws.client)
    await Session(ws, PIPELINE).run()
    log.info("device disconnected")


class Session:
    def __init__(self, ws: WebSocket, pipeline: Pipeline) -> None:
        self.ws = ws
        self.pipeline = pipeline
        self.pcm = bytearray()
        self.turn: asyncio.Task[None] | None = None
        self.history: list[dict[str, str]] = []
        self.health: dict[str, Any] = {}
        self.health_focus = "wellness"
        self.device = "deskmate"
        self.authorized = not bool(settings.DEVICE_TOKEN)

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
            await self._cancel_turn()

    async def _send_json(self, payload: dict[str, Any]) -> None:
        try:
            await self.ws.send_text(json.dumps(payload, separators=(",", ":")))
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def _on_control(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_json({"type": "error", "message": "invalid json"})
            return
        if not isinstance(msg, dict):
            return

        kind = msg.get("type")
        if kind == "hello":
            if not self._token_ok(msg.get("token")):
                await self._reject_unauthorized()
                return
            self.authorized = True
            name = msg.get("device")
            if isinstance(name, str) and name.strip():
                self.device = name.strip()[:64]
            await self._send_json({"type": "hello_ack", "proto": 1, **self.pipeline.describe()})
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
            await self._send_json({"type": "cancel_ack"})
        elif kind in ("new_chat", "newchat", "reset"):
            await self._cancel_turn()
            self.pcm.clear()
            self._reset_history()
            await self._send_json({"type": "chat_reset"})
        elif kind == "text":
            prompt = msg.get("text")
            if not isinstance(prompt, str) or not prompt.strip():
                await self._send_json({"type": "error", "message": "empty text message"})
                return
            prompt = prompt.strip()
            await self._cancel_turn()
            self.pcm.clear()
            log.info("text: %s", prompt[:200])
            self.turn = asyncio.create_task(self._run_text_turn(prompt))
        elif kind == "health_report":
            await self._on_health_report(msg)
        elif kind == "action_ack":
            log.info(
                "action_ack: %s ok=%s %s",
                msg.get("action"), msg.get("ok"), msg.get("error", ""),
            )

    async def _on_audio(self, data: bytes) -> None:
        limit = settings.MAX_UTTERANCE_SECONDS * settings.SAMPLE_RATE * 2
        if len(self.pcm) < limit:
            self.pcm.extend(data[: limit - len(self.pcm)])
        if self.pipeline.mode == "echo":
            await self.ws.send_bytes(data)

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
            await self._speak(prompt, t0, t0, remember=False)
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
        await self._speak(action["prompt"], t0, t_stt, remember=False)

    async def _run_health_summary(self, t0: float, t_stt: float) -> None:
        try:
            history = await health_db.summary(self.device)
            log.info(
                "health summary requested: %s saved readings",
                (history or {}).get("samples", 0),
            )
            await self._speak(wellness.build_history_prompt(history), t0, t_stt, remember=False)
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

    async def _speak(self, prompt: str, t0: float, t_stt: float, remember: bool = True) -> None:
        assert self.pipeline.llm is not None and self.pipeline.tts is not None
        history = self._history_snapshot()
        sentence_q: asyncio.Queue[str | None] = asyncio.Queue()
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=8)
        stats: dict[str, float] = {"llm": 0.0, "tts": 0.0, "total": 0.0, "audio": 0.0}
        audio_sent = False
        failure: list[BaseException] = []

        async def produce() -> None:
            buffer = ""
            answer_parts: list[str] = []
            try:
                async for delta in self.pipeline.llm.stream(prompt, history=history):
                    if not delta:
                        continue
                    if stats["llm"] == 0.0:
                        stats["llm"] = _now_ms() - t_stt
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
                        stats["tts"] = _now_ms() - t_stt - stats["llm"]
                        stats["total"] = _now_ms() - t0
                        audio_sent = True
                    await self.ws.send_bytes(chunk)
                    stats["audio"] += len(chunk) * 1000.0 / (settings.SAMPLE_RATE * 2)
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
                    stats["tts"] = _now_ms() - t_stt - stats["llm"]
                    stats["total"] = _now_ms() - t0
                    audio_sent = True
                await self.ws.send_bytes(bytes(pending))
                stats["audio"] += len(pending) * 1000.0 / (settings.SAMPLE_RATE * 2)

            if not audio_sent:
                if failure:
                    return
                await self._send_json({"type": "error", "message": "empty reply"})
            log.info(
                "turn: stt %d ms, llm %d ms, tts %d ms, ttfa %d ms, audio %d ms",
                int(t_stt - t0), int(stats["llm"]), int(stats["tts"]),
                int(stats["total"]), int(stats["audio"]),
            )
            await self._send_json({"type": "tts_end"})
            await self._send_json(
                {
                    "type": "metrics",
                    "stt_ms": int(t_stt - t0),
                    "llm_ms": int(stats["llm"]),
                    "tts_ms": int(stats["tts"]),
                    "total_ms": int(stats["total"]),
                    "audio_ms": int(stats["audio"]),
                }
            )

        async with asyncio.TaskGroup() as tg:
            tg.create_task(produce())
            tg.create_task(synthesize())
            tg.create_task(consume())
