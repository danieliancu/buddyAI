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
    assert [e.kind for e in hi.feed(silence(800))] == ["speech_end"]
    assert lo.feed(silence(800)) == []


def test_pause_lengths_short_normal_long():
    """A finished sentence ends after 700 / 900 / 1200 ms of silence (Short / Normal / Long)."""
    for sensitivity, end_ms in (("high", 700), ("medium", 900), ("low", 1200)):
        d = EndpointDetector(EnergyProbability(), sensitivity=sensitivity)
        d.feed(tone(500))
        assert d.feed(silence(end_ms - 100)) == []
        assert [e.kind for e in d.feed(silence(200))] == ["speech_end"]


def test_unfinished_sentence_waits_through_a_thinking_pause():
    d = EndpointDetector(EnergyProbability(), sensitivity="medium", unfinished=lambda: True)
    d.feed(tone(500))
    assert d.feed(silence(1500)) == []           # past 900 ms: still listening
    assert d.feed(tone(500)) == []               # speaking again: the same sentence goes on
    assert d.feed(silence(1900)) == []           # the pause starts over
    assert [e.kind for e in d.feed(silence(200))] == ["speech_end"]  # 2 s: ends even if unfinished


def test_unfinished_wait_per_setting():
    for sensitivity, wait_ms in (("high", 1500), ("medium", 2000), ("low", 2800)):
        d = EndpointDetector(EnergyProbability(), sensitivity=sensitivity, unfinished=lambda: True)
        d.feed(tone(500))
        assert d.feed(silence(wait_ms - 100)) == []
        assert [e.kind for e in d.feed(silence(200))] == ["speech_end"]


def test_finished_sentence_is_not_held():
    asked = []
    d = EndpointDetector(EnergyProbability(), sensitivity="medium", unfinished=lambda: asked.append(1) or False)
    d.feed(tone(500))
    assert [e.kind for e in d.feed(silence(1000))] == ["speech_end"]
    assert asked  # asked only once the normal pause was reached


def test_transcript_catching_up_ends_the_wait():
    """The last words arrive during the pause and finish the sentence: it ends then, not at 2 s."""
    state = {"text": "remind me to"}
    from app.pipeline.turn_end import looks_unfinished

    d = EndpointDetector(EnergyProbability(), sensitivity="medium", unfinished=lambda: looks_unfinished(state["text"], ["en"]))
    d.feed(tone(500))
    assert d.feed(silence(1200)) == []
    state["text"] = "remind me to call mum"
    assert [e.kind for e in d.feed(silence(100))] == ["speech_end"]
