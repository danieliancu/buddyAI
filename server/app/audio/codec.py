"""Opus encode/decode (PyAV/libopus), resampling and level normalization. PCM is s16le mono."""

from __future__ import annotations

import av
import numpy as np

FRAME_MS = 60


def _resampler(rate: int) -> av.AudioResampler:
    return av.AudioResampler(format="s16", layout="mono", rate=rate)


class OpusDecoder:
    """Decodes one Opus packet at a time to PCM at `out_rate`."""

    def __init__(self, out_rate: int = 16000) -> None:
        self._ctx = av.CodecContext.create("libopus", "r")
        self._ctx.sample_rate = 48000
        self._ctx.layout = "mono"
        self._res = _resampler(out_rate)

    def decode(self, packet: bytes) -> bytes:
        out = bytearray()
        for frame in self._ctx.decode(av.Packet(packet)):
            frame.pts = None
            for r in self._res.resample(frame):
                out += r.to_ndarray().tobytes()
        return bytes(out)


class OpusEncoder:
    """Accepts PCM at any rate, emits 60 ms Opus packets at `rate`. One instance per stream."""

    def __init__(self, rate: int, bitrate: int = 32000, application: str = "voip") -> None:
        self.rate = rate
        self._ctx = av.CodecContext.create("libopus", "w")
        self._ctx.sample_rate = rate
        self._ctx.layout = "mono"
        self._ctx.format = "s16"
        self._ctx.bit_rate = bitrate
        self._ctx.options = {"application": application, "frame_duration": str(FRAME_MS)}
        self._ctx.open()
        self._frame_samples = rate * FRAME_MS // 1000
        self._buf = np.zeros(0, dtype=np.int16)
        self._pts = 0
        self._resamplers: dict[int, av.AudioResampler] = {}

    def _to_rate(self, pcm: bytes, in_rate: int) -> np.ndarray:
        samples = np.frombuffer(pcm, dtype=np.int16)
        if in_rate == self.rate or samples.size == 0:
            return samples
        res = self._resamplers.setdefault(in_rate, _resampler(self.rate))
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = in_rate
        chunks = [r.to_ndarray().reshape(-1) for r in res.resample(frame)]
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)

    def _encode_frame(self, samples: np.ndarray) -> list[bytes]:
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = self.rate
        frame.pts = self._pts
        self._pts += samples.size
        return [bytes(p) for p in self._ctx.encode(frame)]

    def encode(self, pcm: bytes, in_rate: int) -> list[bytes]:
        self._buf = np.concatenate([self._buf, self._to_rate(pcm, in_rate)])
        packets: list[bytes] = []
        n = self._frame_samples
        while self._buf.size >= n:
            packets += self._encode_frame(self._buf[:n])
            self._buf = self._buf[n:]
        return packets

    def flush(self) -> list[bytes]:
        """Pad the tail with silence and drain the encoder. The encoder is unusable afterwards."""
        packets: list[bytes] = []
        if self._buf.size:
            pad = np.zeros(self._frame_samples - self._buf.size, dtype=np.int16)
            packets += self._encode_frame(np.concatenate([self._buf, pad]))
            self._buf = np.zeros(0, dtype=np.int16)
        packets += [bytes(p) for p in self._ctx.encode(None)]
        return packets


def apply_gain(pcm: bytes, gain: float) -> bytes:
    if gain == 1.0 or not pcm:
        return pcm
    s = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * gain
    return np.clip(s, -32768, 32767).astype(np.int16).tobytes()


def rms_dbfs(pcm: bytes) -> float:
    s = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if s.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(s * s))) / 32768.0
    return 20 * np.log10(max(rms, 1e-6))


def pcm_seconds(pcm_len_bytes: int, rate: int) -> float:
    return pcm_len_bytes / 2 / rate
