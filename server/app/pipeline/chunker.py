"""SemanticSpeechChunker: turns a streaming LLM reply into natural speech fragments for TTS.

It does not wait for full sentences. A fragment is emitted when (first match wins):
  1. natural punctuation (. , ; : ? !) followed by whitespace, once the fragment is long enough
     (first_chunk_min_chars for the first one, min_chars after: every fragment is one TTS request),
  2. the buffer grows past ~soft_max_chars (split at the last word boundary),
  3. no new tokens for idle_flush_ms and enough text is buffered,
  4. the LLM stream ends.
It avoids splitting abbreviations (Dr., etc.), decimals/prices (19.99, £19.99, 3,5) and times (10:30):
punctuation only counts when followed by whitespace, and "." after a known abbreviation or a single
letter is ignored. Pure logic, no I/O: the pipeline drives it with feed()/poll()/finish().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STRONG = ".?!"
WEAK = ",;:"
CLOSERS = "\"')]»”’*_`"
_MARKDOWN = re.compile(r"(\*\*|__|`+|^#+\s*|^\s*[-*•]\s+)", re.MULTILINE)
_SPACES = re.compile(r"\s+")
# Emoji and pictographs: not spoken, and the watch font can't draw them (they would hide captions).
_EMOJI = re.compile(
    "[🀀-🫿☀-➿🤀-🧿️‍🇦-🇿]+"
)


def strip_emoji(text: str) -> str:
    return _EMOJI.sub("", text)


def clean_for_speech(text: str) -> str:
    return _SPACES.sub(" ", strip_emoji(_MARKDOWN.sub("", text))).strip()


@dataclass
class ChunkerConfig:
    min_chars: int = 80
    first_chunk_min_chars: int = 20
    soft_max_chars: int = 160
    hard_max_chars: int = 240
    idle_flush_ms: int = 300
    idle_min_chars: int = 20
    abbreviations: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class SemanticSpeechChunker:
    def __init__(self, language: str, config: ChunkerConfig | None = None) -> None:
        self.cfg = config or ChunkerConfig()
        self.abbrevs = {a.lower() for a in self.cfg.abbreviations.get(language, [])}
        self.abbrevs |= {a.lower() for lang in self.cfg.abbreviations.values() for a in lang if "." in a}
        self._buf = ""
        self._last_input_ms: float | None = None
        self._emitted = 0

    # --- public API -----------------------------------------------------------------

    def feed(self, text: str, now_ms: float) -> list[str]:
        if text:
            self._buf += text
            self._last_input_ms = now_ms
        return self._drain()

    def poll(self, now_ms: float) -> list[str]:
        """Idle flush: call when no tokens arrived for a while."""
        if self._last_input_ms is None or now_ms - self._last_input_ms < self.cfg.idle_flush_ms:
            return []
        if len(self._buf.strip()) < self._idle_min():
            return []
        # The last word may be incomplete (token boundary), so cut at the last whitespace.
        cut = len(self._buf) if self._buf[-1:].isspace() else self._last_space(len(self._buf))
        if cut <= 0:
            return []
        return self._emit(cut)

    def next_deadline_ms(self) -> float | None:
        if self._last_input_ms is None or len(self._buf.strip()) < self._idle_min():
            return None
        return self._last_input_ms + self.cfg.idle_flush_ms

    def finish(self) -> list[str]:
        out = self._drain()
        if self._buf.strip():
            out += self._emit(len(self._buf))
        return out

    @property
    def pending(self) -> str:
        return self._buf

    # --- internals --------------------------------------------------------------------

    def _idle_min(self) -> int:
        return max(self.cfg.idle_min_chars, self._min_len())

    def _min_len(self) -> int:
        # Each fragment is a separate TTS request: its audio must last longer than the next
        # request takes to start (~1 s), or the watch runs dry and there is a pause; and every
        # seam resets the intonation. So the first fragment is short but not tiny ("Da." alone
        # would leave a gap), and later fragments are long.
        return self.cfg.first_chunk_min_chars if self._emitted == 0 else self.cfg.min_chars

    def _drain(self) -> list[str]:
        out: list[str] = []
        while True:
            cut = self._find_boundary()
            if cut is None:
                cut = self._length_cut()
            if cut is None:
                return out
            out += self._emit(cut)

    def _find_boundary(self) -> int | None:
        buf = self._buf
        i = 0
        while i < len(buf):
            ch = buf[i]
            if ch in STRONG or ch in WEAK:
                j = i + 1
                while j < len(buf) and (buf[j] in CLOSERS or buf[j] in STRONG):
                    j += 1  # include closing quotes/parens and "?!" / "..."
                if j >= len(buf):
                    return None  # undecided until we see what follows
                if buf[j].isspace() and self._is_real_boundary(buf, i):
                    if len(buf[:j].strip()) >= self._min_len():
                        return j
                i = j
                continue
            i += 1
        return None

    def _is_real_boundary(self, buf: str, i: int) -> bool:
        if buf[i] != ".":
            return True
        m = re.search(r"([^\s(\[\"'„“]+)\.$", buf[: i + 1])
        if not m:
            return True
        word = m.group(1).lower()
        if word in self.abbrevs:
            return False
        if len(word) == 1 and word.isalpha():
            return False  # initial: "J. Smith"
        return True

    def _length_cut(self) -> int | None:
        if len(self._buf) < self.cfg.soft_max_chars:
            return None
        cut = self._last_space(min(len(self._buf), self.cfg.hard_max_chars))
        if cut > 0 and len(self._buf[:cut].strip()) >= self.cfg.min_chars:
            return cut
        if len(self._buf) >= self.cfg.hard_max_chars:
            return self.cfg.hard_max_chars
        return None

    def _last_space(self, end: int) -> int:
        idx = max(self._buf.rfind(" ", 0, end), self._buf.rfind("\n", 0, end))
        return idx if idx > 0 else 0

    def _emit(self, cut: int) -> list[str]:
        chunk, self._buf = self._buf[:cut], self._buf[cut:]
        self._buf = self._buf.lstrip()
        text = clean_for_speech(chunk)
        if not text:
            return []
        self._emitted += 1
        return [text]
