"""Wire protocol constants and helpers (JSON control + raw PCM16 binary)."""

from __future__ import annotations

SENTENCE_END = ".?!"
MIN_SENTENCE_CHARS = 12
MAX_SENTENCE_CHARS = 180


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Pull complete sentences off the front of a streaming LLM buffer.

    Returns (sentences, remainder). Long clauses are split so text-to-speech can
    start before the model has finished generating.
    """
    sentences: list[str] = []
    start = 0
    i = 0
    while i < len(buffer):
        ch = buffer[i]
        at_end = ch in SENTENCE_END and (i + 1 == len(buffer) or buffer[i + 1].isspace())
        long_clause = (i - start) >= MAX_SENTENCE_CHARS and ch in " ,;:"
        if at_end or long_clause:
            piece = buffer[start : i + 1].strip()
            if len(piece) >= MIN_SENTENCE_CHARS:
                sentences.append(piece)
                start = i + 1
        i += 1

    rest = buffer[start:]
    if len(rest) >= MAX_SENTENCE_CHARS:
        cut = rest.rfind(" ", 0, MAX_SENTENCE_CHARS)
        if cut > 0:
            sentences.append(rest[:cut].strip())
            rest = rest[cut + 1 :]
    return [s for s in sentences if s], rest
