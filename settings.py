"""Gateway configuration. Everything here is read from deskmate-server/.env."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320            # 20 ms
FRAME_BYTES = FRAME_SAMPLES * 2
MAX_UTTERANCE_SECONDS = 30

# echo = send microphone audio straight back (hardware/latency test)
# mock = full STT -> LLM -> TTS pipeline, no network, no keys
# auto = use real providers when their key is present, mock otherwise
MODE = os.getenv("MODE", "auto").strip().lower()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8765"))

# When set, every device must send this value in its hello message. Leave
# blank for local development to keep the gateway open on the LAN.
DEVICE_TOKEN = os.getenv("DEVICE_TOKEN", "").strip()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()
GROQ_LLM_MODEL = os.getenv("GROQ_LLM_MODEL", "qwen/qwen3.8-27b")
EDGE_VOICE = os.getenv("EDGE_VOICE", "en-US-AriaNeural")

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are Desk Mate, a concise, friendly voice assistant on a small desk "
    "device. Reply in one to three short sentences. Use plain spoken English, "
    "no markdown, no lists, and no emoji. Use the recent conversation to "
    "resolve follow-ups such as 'that' or 'the same' instead of asking for "
    "context that is already in the conversation.",
)

# Keep a bounded amount of conversation memory per WebSocket session. Each
# turn contributes one user and one assistant message; 0 disables memory.
try:
    MAX_HISTORY_TURNS = max(0, int(os.getenv("MAX_HISTORY_TURNS", "8")))
except ValueError:
    MAX_HISTORY_TURNS = 8

# Real-time pacing multiplier for TTS chunks leaving the gateway. 1.0 is
# real time; 0 disables pacing (send as fast as possible).
TTS_PACE = float(os.getenv("TTS_PACE", "1.0"))
# How much audio to send before real-time pacing starts. This is the device's
# jitter cushion while the next sentence is being synthesized.
TTS_PREBUFFER = float(os.getenv("TTS_PREBUFFER", "1.0"))
