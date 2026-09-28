"""Per-turn latency marks and percentile helpers (TTFA = end of speech -> first audio at the device)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


def mono_ms() -> float:
    return time.monotonic() * 1000


@dataclass
class TurnMarks:
    listen_start: float = field(default_factory=mono_ms)
    speech_end: float | None = None  # wall-clock of the real end of speech (monotonic ms)
    stt_final: float | None = None
    llm_request: float | None = None
    llm_first_token: float | None = None
    tts_first_text: float | None = None
    tts_first_pcm: float | None = None
    first_frame_sent: float | None = None
    device_first_frame: float | None = None

    @staticmethod
    def _diff(a: float | None, b: float | None) -> int | None:
        return None if a is None or b is None else int(round(b - a))

    def as_db_fields(self) -> dict[str, int | None]:
        return {
            "stt_ms": self._diff(self.speech_end, self.stt_final),
            "llm_first_token_ms": self._diff(self.llm_request, self.llm_first_token),
            "tts_first_audio_ms": self._diff(self.tts_first_text, self.tts_first_pcm),
            "ttfa_server_ms": self._diff(self.speech_end, self.first_frame_sent),
            "ttfa_device_ms": self._diff(self.speech_end, self.device_first_frame),
        }


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile, p in [0, 100]."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, int(round(p / 100 * len(vals) + 0.5)) - 1))
    return vals[k]
