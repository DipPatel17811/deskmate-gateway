"""Offline providers, used for tests and for running without any API keys."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import numpy as np

from .base import History, LanguageModel, SpeechToText, TextToSpeech

MOCK_TRANSCRIPT = "What can you do?"

MOCK_REPLY = (
    "I can hear you. This reply comes from the offline mock model, so the whole "
    "speech to text, language model, and text to speech path is running without "
    "any API keys. Press the button and try again."
)


class MockSpeechToText(SpeechToText):
    name = "mock"

    async def transcribe(self, pcm: bytes) -> str:
        await asyncio.sleep(0.2)
        return MOCK_TRANSCRIPT if pcm else ""


class MockLanguageModel(LanguageModel):
    name = "mock"

    def __init__(self, reply: str = MOCK_REPLY, delay: float = 0.015) -> None:
        self.reply = reply
        self.delay = delay

    async def stream(self, prompt: str, history: History | None = None) -> AsyncIterator[str]:
        for word in self.reply.split(" "):
            yield word + " "
            await asyncio.sleep(self.delay)


class MockTextToSpeech(TextToSpeech):
    """Speech-shaped tones, one burst per word, so timing looks realistic."""

    name = "mock"

    def __init__(self, sample_rate: int = 16000, word_seconds: float = 0.14) -> None:
        self.sample_rate = sample_rate
        self.word_seconds = word_seconds

    async def synthesize(self, text: str) -> np.ndarray:
        words = text.split()[:40]
        await asyncio.sleep(0.08)
        if not words:
            return np.zeros(0, dtype=np.int16)

        out: list[np.ndarray] = []
        gap = np.zeros(int(0.03 * self.sample_rate), dtype=np.int16)
        for i, _word in enumerate(words):
            n = int(self.word_seconds * self.sample_rate)
            t = np.arange(n, dtype=np.float64) / self.sample_rate
            freq = 170.0 + (i * 13) % 130
            env = np.sin(np.pi * t / (n / self.sample_rate)) ** 2
            tone = 0.22 * np.sin(2 * np.pi * freq * t) * env * 32767
            out.append(tone.astype(np.int16))
            out.append(gap)
        return np.concatenate(out)
