"""Server-side VAD (authoritative end-of-speech detection in the MVP).

`EndpointDetector` consumes 16 kHz PCM as it arrives and reports speech start / end.
Silero VAD is used when available; an energy VAD is the fallback (tests, missing onnxruntime).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from app.audio.codec import rms_dbfs

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 512  # 32 ms, Silero's native window at 16 kHz
CHUNK_BYTES = CHUNK_SAMPLES * 2
CHUNK_MS = CHUNK_SAMPLES * 1000 / SAMPLE_RATE

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "silero_vad.onnx"
_session_cache: dict[str, Any] = {}  # InferenceSession is thread-safe and reusable across turns

# End-of-speech silence per sensitivity: higher sensitivity = ends sooner (the customer's "Pause before ola
# answers": high = Short, medium = Normal, low = Long).
END_SILENCE_MS = {"low": 1200, "medium": 900, "high": 700}
# ... and while what was said so far looks unfinished ("set a reminder for...", turn_end.py): a thinking pause.
UNFINISHED_SILENCE_MS = {"low": 2800, "medium": 2000, "high": 1500}


class SpeechProbability(Protocol):
    def __call__(self, chunk: bytes) -> float: ...

    def reset(self) -> None: ...


class SileroProbability:
    """Silero VAD v5 via onnxruntime, pinned to one thread.

    Single-threaded on purpose: multi-threaded backends spin-wait and collapse (100-1000x slower)
    when the host CPU is busy, which would stall the whole event loop. ~0.3 ms per 32 ms window.
    Model: models/silero_vad.onnx (snakers4/silero-vad, MIT).
    """

    CONTEXT = 64  # v5 expects the last 64 samples of the previous window prepended

    def __init__(self, model_path: Path = MODEL_PATH) -> None:
        import numpy as np
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._np = np
        self._session = _session_cache.get(str(model_path))
        if self._session is None:
            self._session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
            _session_cache[str(model_path)] = self._session
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def __call__(self, chunk: bytes) -> float:
        np = self._np
        x = np.frombuffer(chunk, dtype=np.int16).astype(np.float32).reshape(1, -1) / 32768.0
        inp = np.concatenate([self._context, x], axis=1)
        out, self._state = self._session.run(None, {"input": inp, "state": self._state, "sr": self._sr})
        self._context = x[:, -self.CONTEXT :]
        return float(out[0][0])

    def reset(self) -> None:
        np = self._np
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.CONTEXT), dtype=np.float32)


class EnergyProbability:
    """Crude fallback: maps RMS level to a pseudo-probability."""

    def __init__(self, speech_dbfs: float = -38.0) -> None:
        self.speech_dbfs = speech_dbfs

    def __call__(self, chunk: bytes) -> float:
        return 1.0 if rms_dbfs(chunk) > self.speech_dbfs else 0.0

    def reset(self) -> None:
        pass


def make_probability(prefer_silero: bool = True) -> SpeechProbability:
    if prefer_silero:
        try:
            return SileroProbability()
        except Exception as exc:  # pragma: no cover - depends on environment
            log.warning("Silero VAD unavailable (%s); using energy VAD", exc)
    return EnergyProbability()


@dataclass
class VadEvent:
    kind: str  # "speech_start" | "speech_end" | "no_speech" | "max_duration"
    audio_ms: float  # position on the audio timeline


class EndpointDetector:
    def __init__(
        self,
        prob: SpeechProbability,
        sensitivity: str = "medium",
        threshold: float = 0.5,
        min_speech_ms: float = 160,
        no_speech_timeout_ms: float = 6000,
        max_duration_ms: float = 15000,
        unfinished: Callable[[], bool] | None = None,
    ) -> None:
        self.prob = prob
        self.prob.reset()
        self.threshold = threshold
        self.end_silence_ms = END_SILENCE_MS.get(sensitivity, END_SILENCE_MS["medium"])
        self.unfinished_silence_ms = UNFINISHED_SILENCE_MS.get(sensitivity, UNFINISHED_SILENCE_MS["medium"])
        self.unfinished = unfinished  # asked once the normal pause is reached: wait longer if True
        self.min_speech_ms = min_speech_ms
        self.no_speech_timeout_ms = no_speech_timeout_ms
        self.max_duration_ms = max_duration_ms
        self._pending = b""
        self._pos_ms = 0.0
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self.in_speech = False
        self.speech_started = False
        self.last_speech_end_ms: float | None = None
        self.speech_start_ms: float | None = None
        self.done = False

    @property
    def silence_ms(self) -> float:
        """Length of the current pause after speech (0 while speaking)."""
        return self._silence_ms if self.speech_started else 0.0

    def feed(self, pcm: bytes) -> list[VadEvent]:
        if self.done:
            return []
        events: list[VadEvent] = []
        data = self._pending + pcm
        n_full = len(data) // CHUNK_BYTES * CHUNK_BYTES
        self._pending = data[n_full:]
        for off in range(0, n_full, CHUNK_BYTES):
            ev = self._step(data[off : off + CHUNK_BYTES])
            if ev:
                events.append(ev)
                if ev.kind != "speech_start":
                    self.done = True
                    break
        return events

    def _step(self, chunk: bytes) -> VadEvent | None:
        p = self.prob(chunk)
        self._pos_ms += CHUNK_MS
        is_speech = p >= self.threshold
        event: VadEvent | None = None
        if is_speech:
            self._speech_ms += CHUNK_MS
            self._silence_ms = 0.0
            self.last_speech_end_ms = self._pos_ms
            if not self.speech_started and self._speech_ms >= self.min_speech_ms:
                self.speech_started = True
                self.speech_start_ms = self._pos_ms - self._speech_ms
                event = VadEvent("speech_start", self.speech_start_ms)
            self.in_speech = True
        else:
            if not self.speech_started:
                self._speech_ms = 0.0  # require contiguous speech to start
            self._silence_ms += CHUNK_MS
            self.in_speech = False
            if self.speech_started and self._silence_ms >= self.end_silence_ms and (
                self._silence_ms >= self.unfinished_silence_ms or not (self.unfinished and self.unfinished())
            ):
                return VadEvent("speech_end", self.last_speech_end_ms or self._pos_ms)
        if not self.speech_started and self._pos_ms >= self.no_speech_timeout_ms:
            return VadEvent("no_speech", self._pos_ms)
        # The longest question is measured from the first word, not from the mic tap.
        if self.speech_start_ms is not None and self._pos_ms - self.speech_start_ms >= self.max_duration_ms:
            return VadEvent("max_duration", self._pos_ms)
        return event
