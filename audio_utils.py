"""PCM / WAV / TTS helpers shared by the gateway and the device simulator."""

from __future__ import annotations

import io
import subprocess
import wave
from typing import Iterator

import numpy as np

SR = 16000


def pcm_to_wav(pcm: bytes, sample_rate: int = SR) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm(path: str, sample_rate: int = SR) -> np.ndarray:
    with wave.open(path, "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise ValueError(f"unsupported WAV sample width: {width}")

    data = np.frombuffer(frames, dtype=dtype)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)

    if dtype == np.uint8:
        data = (data.astype(np.int16) - 128) << 8
    elif dtype == np.int32:
        data = (data >> 16).astype(np.int16)
    else:
        data = data.astype(np.int16)

    return resample(data, rate, sample_rate)


def resample(samples: np.ndarray, src_rate: int, dst_rate: int = SR) -> np.ndarray:
    if src_rate == dst_rate or len(samples) == 0:
        return samples.astype(np.int16, copy=False)
    n_out = max(1, int(round(len(samples) * dst_rate / src_rate)))
    x_old = np.arange(len(samples), dtype=np.float64)
    x_new = np.linspace(0, len(samples) - 1, n_out)
    out = np.interp(x_new, x_old, samples.astype(np.float64))
    return np.clip(out, -32768, 32767).astype(np.int16)


def chunks(pcm: np.ndarray | bytes, size: int = 640) -> Iterator[bytes]:
    raw = pcm.tobytes() if isinstance(pcm, np.ndarray) else pcm
    for i in range(0, len(raw), size):
        yield raw[i : i + size]


def decode_mp3_to_pcm16(mp3: bytes, sample_rate: int = SR) -> np.ndarray:
    """Decode an MP3 stream to mono int16 PCM at sample_rate."""
    if not mp3:
        return np.zeros(0, dtype=np.int16)

    try:
        import miniaudio

        decoded = miniaudio.decode(
            mp3,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=sample_rate,
        )
        return np.array(decoded.samples, dtype=np.int16)
    except Exception:
        pass

    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "mp3", "-i", "pipe:0",
                "-f", "s16le", "-ac", "1", "-ar", str(sample_rate), "pipe:1",
            ],
            input=mp3,
            capture_output=True,
            check=True,
        )
        return np.frombuffer(proc.stdout, dtype=np.int16).copy()
    except Exception as exc:  # pragma: no cover - depends on host tools
        raise RuntimeError(
            "MP3 decode failed: install 'miniaudio' (pip) or ffmpeg on PATH"
        ) from exc


def tone_pcm(seconds: float = 1.2, freq: float = 440.0, sample_rate: int = SR) -> np.ndarray:
    n = int(seconds * sample_rate)
    t = np.arange(n, dtype=np.float64) / sample_rate
    env = np.minimum(1.0, np.minimum(t / 0.02, (seconds - t) / 0.02))
    samples = 0.25 * np.sin(2 * np.pi * freq * t) * env * 32767
    return samples.astype(np.int16)
