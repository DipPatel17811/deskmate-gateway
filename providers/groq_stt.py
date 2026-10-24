"""Groq-hosted Whisper transcription (development only; audio leaves the LAN)."""

from __future__ import annotations

import asyncio

from audio_utils import pcm_to_wav

from .base import SpeechToText


class GroqSpeechToText(SpeechToText):
    name = "groq"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self._client = None

    def _client_or_create(self):
        if self._client is None:
            from groq import Groq

            self._client = Groq(api_key=self.api_key)
        return self._client

    def _transcribe_sync(self, pcm: bytes) -> str:
        wav = pcm_to_wav(pcm)
        client = self._client_or_create()
        result = client.audio.transcriptions.create(
            file=("utterance.wav", wav),
            model=self.model,
            language="en",
            response_format="json",
            temperature=0.0,
        )
        return (getattr(result, "text", "") or "").strip()

    async def transcribe(self, pcm: bytes) -> str:
        if not pcm:
            return ""
        return await asyncio.to_thread(self._transcribe_sync, pcm)
