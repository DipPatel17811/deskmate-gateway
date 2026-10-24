"""Provider interfaces: keep the gateway independent of any vendor SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import numpy as np

History = Sequence[dict[str, str]]


class SpeechToText:
    name = "none"

    async def transcribe(self, pcm: bytes) -> str:
        raise NotImplementedError


class LanguageModel:
    name = "none"

    async def stream(self, prompt: str, history: History | None = None) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""  # pragma: no cover


class TextToSpeech:
    name = "none"

    async def synthesize(self, text: str) -> np.ndarray:
        raise NotImplementedError

