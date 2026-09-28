import numpy as np

from app.pipeline.vad import EndpointDetector, EnergyProbability


def tone(ms: int, amp: float = 0.3) -> bytes:
    n = 16 * ms
    return (amp * 32767 * np.sin(2 * np.pi * 200 * np.arange(n) / 16000)).astype(np.int16).tobytes()


def silence(ms: int) -> bytes:
    return bytes(32 * ms)


def test_speech_then_silence_ends():
    d = EndpointDetector(EnergyProbability(), sensitivity="medium")
    events = d.feed(silence(300)) + d.feed(tone(800)) + d.feed(silence(1000))
    kinds = [e.kind for e in events]
    assert kinds == ["speech_start", "speech_end"]
    end = events[-1].audio_ms
    assert 1050 <= end <= 1150  # 300 ms silence + 800 ms speech


def test_no_speech_timeout():
    d = EndpointDetector(EnergyProbability(), no_speech_timeout_ms=1000)
    events = d.feed(silence(1500))
    assert [e.kind for e in events] == ["no_speech"]


def test_max_duration():
    d = EndpointDetector(EnergyProbability(), max_duration_ms=2000)
    events = d.feed(tone(2500))
    assert events[-1].kind == "max_duration"


def test_high_sensitivity_ends_sooner():
    lo = EndpointDetector(EnergyProbability(), sensitivity="low")
    hi = EndpointDetector(EnergyProbability(), sensitivity="high")
    for d in (lo, hi):
        d.feed(tone(500))
    assert [e.kind for e in hi.feed(silence(600))] == ["speech_end"]
    assert lo.feed(silence(600)) == []
