"""Groq-hosted streaming chat completion (development only; text leaves the LAN)."""

from __future__ import annotations

from typing import AsyncIterator

from .base import History, LanguageModel


class GroqLanguageModel(LanguageModel):
    name = "groq"

    def __init__(self, api_key: str, model: str, system_prompt: str) -> None:
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self._client = None

    def _client_or_create(self):
        if self._client is None:
            from groq import AsyncGroq

            self._client = AsyncGroq(api_key=self.api_key)
        return self._client

    async def stream(self, prompt: str, history: History | None = None) -> AsyncIterator[str]:
        client = self._client_or_create()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt},
        ]
        for turn in history or ():
            role = turn.get("role")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt})

        stream = await client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.6,
            max_tokens=320,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                yield text
