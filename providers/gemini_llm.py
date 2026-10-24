"""Gemini streaming text generation via the official google-genai SDK."""

from __future__ import annotations

import inspect
from typing import AsyncIterator

from .base import History, LanguageModel


class GeminiLanguageModel(LanguageModel):
    name = "gemini"

    def __init__(self, api_key: str, model: str, system_prompt: str) -> None:
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self._client = None

    def _client_or_create(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def stream(self, prompt: str, history: History | None = None) -> AsyncIterator[str]:
        from google.genai import types

        client = self._client_or_create()
        config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            temperature=0.6,
            max_output_tokens=320,
        )
        contents: list[types.Content] = []
        for turn in history or ():
            role = turn.get("role")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                contents.append(
                    types.Content(
                        role="model" if role == "assistant" else "user",
                        parts=[types.Part(text=content)],
                    )
                )
        contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))
        stream = client.aio.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=config,
        )
        if inspect.isawaitable(stream):
            stream = await stream

        async for chunk in stream:
            text = getattr(chunk, "text", None)
            if text:
                yield text
