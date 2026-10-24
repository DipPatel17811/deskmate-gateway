"""Provider selection and the pipeline container used by the WebSocket app."""

from __future__ import annotations

from .base import LanguageModel, SpeechToText, TextToSpeech
from .edge_tts import EdgeTextToSpeech
from .gemini_llm import GeminiLanguageModel
from .groq_llm import GroqLanguageModel
from .groq_stt import GroqSpeechToText
from .mock import MockLanguageModel, MockSpeechToText, MockTextToSpeech


class Pipeline:
    def __init__(
        self,
        mode: str,
        stt: SpeechToText | None,
        llm: LanguageModel | None,
        tts: TextToSpeech | None,
    ) -> None:
        self.mode = mode
        self.stt = stt
        self.llm = llm
        self.tts = tts

    def describe(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "stt": self.stt.name if self.stt else "loopback",
            "llm": self.llm.name if self.llm else "loopback",
            "tts": self.tts.name if self.tts else "loopback",
        }


def build_pipeline(settings) -> Pipeline:
    if settings.MODE == "echo":
        return Pipeline("echo", None, None, None)

    force_mock = settings.MODE == "mock"
    if settings.MODE not in ("auto", "mock"):
        raise ValueError(f"unknown MODE={settings.MODE!r} (use echo, mock, or auto)")

    stt: SpeechToText
    if force_mock or not settings.GROQ_API_KEY:
        stt = MockSpeechToText()
    else:
        stt = GroqSpeechToText(settings.GROQ_API_KEY, settings.GROQ_STT_MODEL)

    llm: LanguageModel
    if force_mock:
        llm = MockLanguageModel()
    elif settings.LLM_PROVIDER == "groq":
        if not settings.GROQ_API_KEY:
            llm = MockLanguageModel()
        else:
            llm = GroqLanguageModel(
                settings.GROQ_API_KEY, settings.GROQ_LLM_MODEL, settings.SYSTEM_PROMPT
            )
    elif settings.LLM_PROVIDER == "gemini":
        if not settings.GEMINI_API_KEY:
            llm = MockLanguageModel()
        else:
            llm = GeminiLanguageModel(
                settings.GEMINI_API_KEY, settings.GEMINI_MODEL, settings.SYSTEM_PROMPT
            )
    else:
        raise ValueError(
            f"unknown LLM_PROVIDER={settings.LLM_PROVIDER!r} (use groq or gemini)"
        )

    tts: TextToSpeech
    if force_mock:
        tts = MockTextToSpeech(settings.SAMPLE_RATE)
    else:
        tts = EdgeTextToSpeech(settings.EDGE_VOICE, settings.SAMPLE_RATE)

    mode = "mock" if force_mock else ("real" if all(p.name != "mock" for p in (stt, llm, tts)) else "mixed")
    return Pipeline(mode, stt, llm, tts)
