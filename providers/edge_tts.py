"""Edge TTS synthesis (development only: Microsoft terms restrict this use)."""

from __future__ import annotations

import numpy as np

from audio_utils import decode_mp3_to_pcm16

from .base import TextToSpeech


class EdgeTextToSpeech(TextToSpeech):
    name = "edge"

    def __init__(self, voice: str, sample_rate: int = 16000) -> None:
        self.voice = voice
        self.sample_rate = sample_rate

    async def synthesize(self, text: str) -> np.ndarray:
        import edge_tts

        if not text.strip():
            return np.zeros(0, dtype=np.int16)

        mp3 = bytearray()
        communicate = edge_tts.Communicate(text, self.voice)
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                mp3.extend(chunk["data"])

        return decode_mp3_to_pcm16(bytes(mp3), self.sample_rate)
