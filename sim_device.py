"""Stand-in for the ESP32 desk device. Sends one push-to-talk turn and saves TTS.

Examples:
    python sim_device.py --tone
    python sim_device.py --text "What is the capital of France?"
    python sim_device.py --wav my_question.wav --url ws://192.168.1.50:8765/ws
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import numpy as np

import settings
from audio_utils import (
    SR,
    chunks,
    decode_mp3_to_pcm16,
    pcm_to_wav,
    tone_pcm,
    wav_to_pcm,
)


async def _text_to_pcm(text: str) -> np.ndarray:
    import edge_tts

    mp3 = bytearray()
    communicate = edge_tts.Communicate(text, settings.EDGE_VOICE)
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            mp3.extend(chunk["data"])
    return decode_mp3_to_pcm16(bytes(mp3), SR)


async def run_turn(
    url: str,
    pcm: np.ndarray,
    out_path: Path,
    *,
    realtime: bool = True,
    timeout: float = 45.0,
) -> dict:
    import websockets

    messages: list[dict] = []
    audio = bytearray()
    first_audio_ms = -1.0

    async with websockets.connect(url, max_size=None, open_timeout=10) as ws:
        await ws.send(json.dumps({
            "type": "hello",
            "proto": 1,
            "device": "simulator",
            "rate": SR,
            "ch": 1,
            "fmt": "pcm_s16le",
        }))
        try:
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            messages.append(ack)
            print(f"<- {ack}")
        except asyncio.TimeoutError:
            print("warning: no hello_ack within 5 s")

        await ws.send(json.dumps({"type": "speech_start"}))
        for chunk in chunks(pcm, settings.FRAME_BYTES):
            await ws.send(chunk)
            if realtime:
                await asyncio.sleep(settings.FRAME_BYTES / 2 / SR)

        end_sent = time.perf_counter()
        await ws.send(json.dumps({"type": "speech_end"}))

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("warning: receive timeout")
                break
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                print("warning: receive timeout")
                break

            if isinstance(message, (bytes, bytearray)):
                if first_audio_ms < 0:
                    first_audio_ms = (time.perf_counter() - end_sent) * 1000.0
                audio.extend(message)
                continue

            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue
            messages.append(payload)
            print(f"<- {payload.get('type')}: {message[:160]}")
            if payload.get("type") == "error":
                break
            if payload.get("type") == "metrics":
                break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(pcm_to_wav(bytes(audio)))
    metrics = next((m for m in messages if m.get("type") == "metrics"), {})

    print(
        f"audio: {len(audio)} bytes ({len(audio) / 2 / SR:.2f} s) -> {out_path}\n"
        f"first audio: {first_audio_ms:.0f} ms | metrics: {metrics}"
    )
    return {
        "messages": messages,
        "audio_bytes": len(audio),
        "audio_pcm": bytes(audio),
        "first_audio_ms": first_audio_ms,
        "metrics": metrics,
        "out_path": str(out_path),
    }


def _build_pcm(args: argparse.Namespace) -> np.ndarray:
    if args.wav:
        return wav_to_pcm(args.wav, SR)
    if args.text:
        try:
            return asyncio.run(_text_to_pcm(args.text))
        except Exception as exc:  # noqa: BLE001
            print(f"warning: edge-tts failed ({exc}); using a tone instead")
    return tone_pcm(args.seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Desk Mate device simulator")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--tone", action="store_true", help="send a 440 Hz tone (default)")
    source.add_argument("--text", help="synthesize this sentence with Edge TTS and send it")
    source.add_argument("--wav", help="send a WAV file (any rate/channels, converted to 16k mono)")
    parser.add_argument("--url", default=f"ws://127.0.0.1:{settings.PORT}/ws")
    parser.add_argument("--out", default="sim_response.wav")
    parser.add_argument("--seconds", type=float, default=1.2)
    parser.add_argument("--fast", action="store_true", help="do not pace input in real time")
    args = parser.parse_args()

    pcm = _build_pcm(args)
    print(f"sending {len(pcm) / SR:.2f} s of audio to {args.url}")
    asyncio.run(
        run_turn(args.url, pcm, Path(args.out), realtime=not args.fast)
    )


if __name__ == "__main__":
    main()
