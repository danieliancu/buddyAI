import json

import numpy as np
import pytest

from app.audio.codec import OpusDecoder, OpusEncoder
from app.device_settings import DeviceSettings, device_view, merge, posix_tz
from app.gateway.protocol import AudioFrame, Envelope, ProtocolError, parse_message


def test_audio_header_roundtrip():
    f = AudioFrame(kind=0x02, turn_id=7, frame_seq=123, payload=b"opus")
    raw = f.pack()
    assert len(raw) == 12 + 4
    assert raw[:12] == bytes([2, 0, 0, 0, 0, 0, 0, 7, 0, 0, 0, 123])
    g = AudioFrame.unpack(raw)
    assert (g.kind, g.turn_id, g.frame_seq, g.payload) == (2, 7, 123, b"opus")


def test_envelope_fields_and_sequence():
    env = Envelope()
    env.session_id = "s1"
    a, b = json.loads(env.build("state", 3, state="thinking")), json.loads(env.build("pong"))
    assert a["protocol_version"] == 1 and a["session_id"] == "s1" and a["turn_id"] == 3 and a["state"] == "thinking"
    assert b["sequence_number"] == a["sequence_number"] + 1 and b["timestamp"] > 0


def test_parse_rejects_bad_version():
    with pytest.raises(ProtocolError) as e:
        parse_message('{"type":"hello","protocol_version":2}')
    assert e.value.code == "protocol_unsupported"
    with pytest.raises(ProtocolError):
        parse_message('{"type":"listen_start","protocol_version":1,"turn_id":-1}')


def test_opus_roundtrip_16k_to_24k():
    enc = OpusEncoder(24000)
    t = np.arange(24000) / 24000
    pcm = (0.3 * 32767 * np.sin(2 * np.pi * 300 * t)).astype(np.int16).tobytes()
    packets = enc.encode(pcm, 24000) + enc.flush()
    assert len(packets) >= 16  # 1 s / 60 ms
    dec = OpusDecoder(24000)
    out = b"".join(dec.decode(p) for p in packets)
    assert abs(len(out) / 2 / 24000 - 1.0) < 0.15


def test_encoder_resamples_input():
    enc = OpusEncoder(16000)
    pcm = np.zeros(24000, dtype=np.int16).tobytes()  # 1 s at 24 kHz
    packets = enc.encode(pcm, 24000) + enc.flush()
    assert 15 <= len(packets) <= 19


def test_settings_tz_and_theme_preset():
    assert posix_tz("Europe/Bucharest") == "EET-2EEST,M3.5.0/3,M10.5.0/4"
    s = merge(DeviceSettings(), {"theme": {"preset": "forest"}})
    assert s.theme.accent == "#3DDC84"
    view = device_view(s)
    assert set(view) == {"language", "quick_languages", "volume", "brightness", "screen_timeout_s", "time_24h", "tz_posix", "theme", "max_listen_s"}
    with pytest.raises(ValueError):
        merge(DeviceSettings(), {"theme": {"accent": "red"}})
    with pytest.raises(ValueError):
        DeviceSettings(timezone="Mars/Olympus")
