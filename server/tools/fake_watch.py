"""fake_watch.py — BuddyAI watch simulator speaking the real device protocol (PROTOCOL.md v1).

Develop and test the whole AI pipeline without hardware:
  python tools/fake_watch.py pair                          # show a pairing code, wait for the admin, save token
  python tools/fake_watch.py ask --wav samples/ro_1.wav --lang ro --save out/reply.wav
  python tools/fake_watch.py bench --wav-dir samples --lang ro --repeat 3   # TTFA p50/p95 report
  python tools/fake_watch.py abort-test --wav samples/en_1.wav --abort-after-ms 400
  python tools/fake_watch.py reconnect-test --wav samples/en_1.wav
  python tools/fake_watch.py scenario tests/scenarios/smoke.json

Exit code is non-zero when a check fails (stale frames after abort, missing audio, TTFA over target with --strict).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import struct
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import av
import numpy as np
import websockets

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE.parent / "data" / "fake_watch_token.json"
HEADER = struct.Struct(">BBHII")
FRAME_MS = 60
UPLINK_RATE = 16000
TTFA_P50_TARGET_MS = 1500
TTFA_P95_TARGET_MS = 2500


def now_ms() -> float:
    return time.monotonic() * 1000


# --- audio helpers ---------------------------------------------------------------------------


def load_wav_16k(path: Path) -> np.ndarray:
    """Any WAV/audio file -> mono int16 16 kHz."""
    with av.open(str(path)) as container:
        res = av.AudioResampler(format="s16", layout="mono", rate=UPLINK_RATE)
        chunks = []
        for frame in container.decode(audio=0):
            frame.pts = None
            chunks += [r.to_ndarray().reshape(-1) for r in res.resample(frame)]
        chunks += [r.to_ndarray().reshape(-1) for r in res.resample(None)]
    return np.concatenate(chunks).astype(np.int16)


def speech_end_sample(pcm: np.ndarray, thresh_dbfs: float = -40.0) -> int:
    """Index right after the last 20 ms window louder than the threshold (real end of speech)."""
    win = UPLINK_RATE // 50
    last = 0
    for i in range(0, len(pcm) - win, win):
        w = pcm[i : i + win].astype(np.float32)
        rms = np.sqrt(np.mean(w * w)) / 32768 + 1e-9
        if 20 * np.log10(rms) > thresh_dbfs:
            last = i + win
    return last


class Encoder:
    def __init__(self) -> None:
        self.ctx = av.CodecContext.create("libopus", "w")
        self.ctx.sample_rate, self.ctx.layout, self.ctx.format = UPLINK_RATE, "mono", "s16"
        self.ctx.bit_rate = 24000
        self.ctx.options = {"application": "voip", "frame_duration": str(FRAME_MS)}
        self.ctx.open()
        self.pts = 0

    def encode(self, samples: np.ndarray) -> list[bytes]:
        f = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        f.sample_rate, f.pts = UPLINK_RATE, self.pts
        self.pts += samples.size
        return [bytes(p) for p in self.ctx.encode(f)]


class Decoder:
    def __init__(self, rate: int) -> None:
        self.ctx = av.CodecContext.create("libopus", "r")
        self.ctx.sample_rate, self.ctx.layout = 48000, "mono"
        self.res = av.AudioResampler(format="s16", layout="mono", rate=rate)
        self.rate = rate

    def decode(self, pkt: bytes) -> bytes:
        out = b""
        for fr in self.ctx.decode(av.Packet(pkt)):
            fr.pts = None
            out += b"".join(r.to_ndarray().tobytes() for r in self.res.resample(fr))
        return out


def save_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


# --- turn bookkeeping ------------------------------------------------------------------------


@dataclass
class TurnLog:
    turn_id: int
    speech_end_at: float | None = None
    listen_stop_at: float | None = None
    listen_stop_reason: str | None = None
    stt_final_at: float | None = None
    transcript: str = ""
    first_llm_at: float | None = None
    reply_text: str = ""
    tts_start_at: float | None = None
    first_frame_at: float | None = None
    frames: int = 0
    pcm: bytearray = field(default_factory=bytearray)
    sample_rate: int = 24000
    status: str | None = None
    error: str | None = None
    listen_stopped: asyncio.Event = field(default_factory=asyncio.Event)
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    tts_started: asyncio.Event = field(default_factory=asyncio.Event)

    def rel(self, t: float | None) -> str:
        if t is None or self.speech_end_at is None:
            return "-"
        return f"{t - self.speech_end_at:+.0f} ms"

    @property
    def ttfa_ms(self) -> float | None:
        if self.first_frame_at is None or self.speech_end_at is None:
            return None
        return self.first_frame_at - self.speech_end_at


class FakeWatch:
    def __init__(self, url: str, device_id: str, verbose: bool = False) -> None:
        self.url, self.device_id, self.verbose = url, device_id, verbose
        self.ws: websockets.ClientConnection | None = None
        self.seq = 0
        self.session_id: str | None = None
        self.turn_counter = 0
        self.active_turn: int | None = None
        self.turns: dict[int, TurnLog] = {}
        self.aborted: dict[int, float] = {}  # turn_id -> abort time
        self.stale_violations: list[str] = []
        self.aborted_ended: dict[int, float] = {}  # aborted turn_id -> time its turn_end arrived
        self.dropped_in_flight = 0
        self.inbox: asyncio.Queue[dict] = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self.downlink_rate = 24000
        self.settings: dict[str, Any] = {}

    def log(self, *a: Any) -> None:
        if self.verbose:
            print("  ·", *a)

    # --- connection -----------------------------------------------------------------------

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.url, max_size=2**22, open_timeout=5)
        self.seq = 0
        self.session_id = None
        self._reader = asyncio.create_task(self._read())

    async def close(self, abrupt: bool = False) -> None:
        if self.ws:
            if abrupt:
                self.ws.transport.abort()  # simulate Wi-Fi loss (no close frame)
            else:
                await self.ws.close()
        if self._reader:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass

    async def send(self, type_: str, turn_id: int | None = None, **fields: Any) -> None:
        self.seq += 1
        msg = {
            "type": type_,
            "protocol_version": 1,
            "session_id": self.session_id,
            "turn_id": turn_id,
            "sequence_number": self.seq,
            "timestamp": int(time.time() * 1000),
            **fields,
        }
        assert self.ws
        await self.ws.send(json.dumps(msg, ensure_ascii=False))

    async def expect(self, *types: str, timeout: float = 10) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timeout waiting for {types}")
            msg = await asyncio.wait_for(self.inbox.get(), remaining)
            if msg["type"] in types:
                return msg
            if msg["type"] == "error" and msg.get("turn_id") is None:
                raise RuntimeError(f"server error: {msg.get('code')}: {msg.get('message')}")

    async def hello_token(self, token: str) -> dict:
        await self.send(
            "hello",
            device_id=self.device_id,
            fw_version="sim-0.1",
            hw_model="fake_watch",
            token=token,
            audio={"uplink_rate": UPLINK_RATE, "downlink_rates": [16000, 24000]},
        )
        ack = await self.expect("hello_ack")
        self.session_id = ack["session_id"]
        self.downlink_rate = ack.get("downlink_rate", 24000)
        self.settings = ack.get("settings", {})
        return ack

    async def pair(self) -> str:
        code = f"{random.randint(0, 999999):06d}"
        await self.send("hello", device_id=self.device_id, fw_version="sim-0.1", hw_model="fake_watch", pairing_code=code)
        await self.expect("pairing_pending")
        print(f"\n  Pairing code: {code}  -> enter it in the web app (Devices > Add watch)\n")
        msg = await self.expect("paired", timeout=300)
        return msg["device_token"]

    # --- incoming -------------------------------------------------------------------------

    async def _read(self) -> None:
        assert self.ws
        async for raw in self.ws:
            t = now_ms()
            if isinstance(raw, bytes):
                kind, _flags, _codec, turn_id, fseq = HEADER.unpack_from(raw)
                self._on_audio(kind, turn_id, fseq, raw[HEADER.size :], t)
                continue
            msg = json.loads(raw)
            self._on_json(msg, t)
            await self.inbox.put(msg)

    def _check_stale(self, what: str, turn_id: int | None, t: float) -> bool:
        """True if the message belongs to an aborted turn (the watch drops it).

        Frames already in flight when we sent `abort` are expected and only counted. Anything that
        arrives after the server confirmed the abort with `turn_end` is a server-side violation.
        """
        if turn_id not in self.aborted or t <= self.aborted[turn_id]:
            return False
        ended = self.aborted_ended.get(turn_id)
        if ended is not None and t > ended:
            self.stale_violations.append(f"{what} for aborted turn {turn_id} after its turn_end")
        else:
            self.dropped_in_flight += 1
        return True

    def _on_audio(self, kind: int, turn_id: int, fseq: int, payload: bytes, t: float) -> None:
        if kind != 0x02:
            return
        if self._check_stale("audio frame", turn_id, t) or turn_id != self.active_turn:
            return  # a real watch drops it; we also counted it as a violation if it was aborted
        tl = self.turns[turn_id]
        if tl.first_frame_at is None:
            tl.first_frame_at = t
            asyncio.get_running_loop().create_task(self.send("playback_started", turn_id))
        tl.frames += 1
        tl.pcm += tl._decoder.decode(payload)  # type: ignore[attr-defined]

    def _on_json(self, msg: dict, t: float) -> None:
        kind, turn_id = msg["type"], msg.get("turn_id")
        self.log(f"{kind} turn={turn_id}", {k: v for k, v in msg.items() if k not in ("type", "protocol_version", "session_id", "turn_id", "sequence_number", "timestamp")})
        if kind == "turn_end" and turn_id in self.aborted:
            self.aborted_ended.setdefault(turn_id, t)
        if kind != "turn_end" and turn_id is not None:
            if self._check_stale(f"'{kind}'", turn_id, t) or turn_id != self.active_turn:
                return
        tl = self.turns.get(turn_id) if turn_id is not None else None
        if tl is None:
            return
        if kind == "listen_stop":
            tl.listen_stop_at, tl.listen_stop_reason = t, msg.get("reason")
            tl.listen_stopped.set()
        elif kind == "stt_result" and msg.get("final"):
            tl.stt_final_at, tl.transcript = t, msg.get("text", "")
        elif kind == "llm_text":
            tl.first_llm_at = tl.first_llm_at or t
            tl.reply_text += msg.get("delta", "")
        elif kind == "tts_start":
            tl.tts_start_at = t
            tl.sample_rate = msg.get("sample_rate", self.downlink_rate)
            tl._decoder = Decoder(tl.sample_rate)  # type: ignore[attr-defined]
            tl.tts_started.set()
        elif kind == "error":
            tl.error = f"{msg.get('code')}: {msg.get('message')}"
        elif kind == "turn_end":
            tl.status = msg.get("status")
            tl.ended.set()

    # --- turns ------------------------------------------------------------------------------

    def new_turn(self) -> TurnLog:
        self.turn_counter += 1
        tl = TurnLog(self.turn_counter)
        self.turns[tl.turn_id] = tl
        self.active_turn = tl.turn_id
        return tl

    async def stream_utterance(self, tl: TurnLog, pcm: np.ndarray, realtime: bool = True, tail_s: float = 3.0) -> None:
        """Stream speech + trailing silence until the server says listen_stop."""
        audio = np.concatenate([pcm, np.zeros(int(UPLINK_RATE * tail_s), dtype=np.int16)])
        end_idx = speech_end_sample(pcm)
        enc = Encoder()
        n = UPLINK_RATE * FRAME_MS // 1000
        seq = 0
        start = now_ms()
        for off in range(0, len(audio) - n + 1, n):
            if tl.listen_stopped.is_set() or self.active_turn != tl.turn_id:
                return
            for pkt in enc.encode(audio[off : off + n]):
                assert self.ws
                await self.ws.send(HEADER.pack(0x01, 0, 0, tl.turn_id, seq) + pkt)
                seq += 1
            if tl.speech_end_at is None and off + n >= end_idx:
                # The frame containing the last speech sample has just left the "watch".
                tl.speech_end_at = now_ms()
            if realtime:
                target = start + (off + n) / UPLINK_RATE * 1000
                await asyncio.sleep(max(0.0, (target - now_ms()) / 1000))

    async def ask(self, pcm: np.ndarray, language: str | None, realtime: bool = True) -> TurnLog:
        tl = self.new_turn()
        await self.send("listen_start", tl.turn_id, **({"language": language} if language else {}))
        await self.stream_utterance(tl, pcm, realtime)
        await asyncio.wait_for(tl.ended.wait(), 60)
        await self.send("playback_done", tl.turn_id)
        return tl

    async def abort_active(self) -> None:
        tid = self.active_turn
        if tid is None:
            return
        self.aborted[tid] = now_ms()
        self.active_turn = None  # watch flushes playback and ignores the old turn from now on
        await self.send("abort", tid, reason="user_tap")


# --- reporting ---------------------------------------------------------------------------------


def report_turn(tl: TurnLog) -> None:
    print(f"  turn {tl.turn_id}: status={tl.status} stop={tl.listen_stop_reason} frames={tl.frames}")
    print(f"    user : {tl.transcript!r}")
    print(f"    reply: {tl.reply_text.strip()!r}")
    print(
        f"    timeline vs end of speech: listen_stop {tl.rel(tl.listen_stop_at)}, stt_final {tl.rel(tl.stt_final_at)}, "
        f"first_llm {tl.rel(tl.first_llm_at)}, tts_start {tl.rel(tl.tts_start_at)}, FIRST AUDIO {tl.rel(tl.first_frame_at)}"
    )
    if tl.error:
        print(f"    error: {tl.error}")


def pct(values: list[float], p: float) -> float:
    vals = sorted(values)
    k = max(0, min(len(vals) - 1, int(round(p / 100 * len(vals) + 0.5)) - 1))
    return vals[k]


def report_ttfa(values: list[float], strict: bool) -> bool:
    if not values:
        print("  TTFA: no completed turns")
        return False
    p50, p95 = pct(values, 50), pct(values, 95)
    ok = p50 < TTFA_P50_TARGET_MS and p95 < TTFA_P95_TARGET_MS
    print(
        f"\n  TTFA over {len(values)} turns: p50={p50:.0f} ms (target <{TTFA_P50_TARGET_MS}), "
        f"p95={p95:.0f} ms (target <{TTFA_P95_TARGET_MS}), mean={statistics.mean(values):.0f} ms -> "
        f"{'OK' if ok else 'OVER TARGET'}"
    )
    return ok or not strict


# --- commands ------------------------------------------------------------------------------------


def load_token(device_id: str) -> str:
    if not TOKEN_FILE.exists():
        sys.exit(f"No token in {TOKEN_FILE}. Run: python tools/fake_watch.py pair")
    data = json.loads(TOKEN_FILE.read_text())
    if device_id not in data:
        sys.exit(f"No token for device {device_id}. Run pair first.")
    return data[device_id]


async def connected(args) -> FakeWatch:
    w = FakeWatch(args.url, args.device_id, args.verbose)
    await w.connect()
    await w.hello_token(load_token(args.device_id))
    return w


async def cmd_pair(args) -> int:
    w = FakeWatch(args.url, args.device_id, args.verbose)
    await w.connect()
    token = await w.pair()
    data = json.loads(TOKEN_FILE.read_text()) if TOKEN_FILE.exists() else {}
    data[args.device_id] = token
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=2))
    await w.close()
    w = await connected(args)
    print(f"  Paired as {args.device_id}. Settings: {json.dumps(w.settings, ensure_ascii=False)}")
    await w.close()
    return 0


async def cmd_ask(args) -> int:
    w = await connected(args)
    pcm = load_wav_16k(Path(args.wav))
    tl = await w.ask(pcm, args.lang, realtime=not args.fast)
    report_turn(tl)
    if args.save and tl.pcm:
        save_wav(Path(args.save), bytes(tl.pcm), tl.sample_rate)
        print(f"  reply audio saved to {args.save}")
    await w.close()
    return 0 if tl.status == "completed" and tl.frames > 0 else 1


async def cmd_bench(args) -> int:
    files = sorted(Path(args.wav_dir).glob("*.wav")) if args.wav_dir else [Path(args.wav)]
    if args.lang:
        files = [f for f in files if f.name.startswith(args.lang)] or files
    w = await connected(args)
    ttfa: list[float] = []
    failures = 0
    for r in range(args.repeat):
        for f in files:
            print(f"- {f.name} (run {r + 1})")
            tl = await w.ask(load_wav_16k(f), args.lang, realtime=True)
            report_turn(tl)
            if tl.status == "completed" and tl.ttfa_ms is not None:
                ttfa.append(tl.ttfa_ms)
            else:
                failures += 1
    await w.close()
    ok = report_ttfa(ttfa, args.strict)
    print(f"  failures: {failures}")
    return 0 if ok and failures == 0 else 1


async def cmd_abort(args) -> int:
    """Tap-to-interrupt: abort while the reply is playing, start a new turn, verify no stale frames."""
    w = await connected(args)
    pcm = load_wav_16k(Path(args.wav))
    first = w.new_turn()
    await w.send("listen_start", first.turn_id, **({"language": args.lang} if args.lang else {}))
    await w.stream_utterance(first, pcm)
    await asyncio.wait_for(first.tts_started.wait(), 30)
    await asyncio.sleep(args.abort_after_ms / 1000)
    print(f"  aborting turn {first.turn_id} after {first.frames} frames")
    await w.abort_active()
    second = w.new_turn()
    await w.send("listen_start", second.turn_id, **({"language": args.lang} if args.lang else {}))
    await w.stream_utterance(second, pcm)
    await asyncio.wait_for(second.ended.wait(), 60)
    await asyncio.sleep(0.5)  # give late frames a chance to show up
    await asyncio.wait_for(first.ended.wait(), 5)
    report_turn(first)
    report_turn(second)
    await w.close()
    ok = first.status == "aborted" and second.status == "completed" and not w.stale_violations
    for v in w.stale_violations:
        print(f"  STALE: {v}")
    print(
        f"  abort-test: {'PASS' if ok else 'FAIL'} (first={first.status}, second={second.status}, "
        f"dropped in flight={w.dropped_in_flight}, stale after turn_end={len(w.stale_violations)})"
    )
    return 0 if ok else 1


async def cmd_reconnect(args) -> int:
    """Drop the connection mid-utterance (no close frame), reconnect, and complete a new turn."""
    w = await connected(args)
    pcm = load_wav_16k(Path(args.wav))
    tl = w.new_turn()
    await w.send("listen_start", tl.turn_id)
    half = pcm[: len(pcm) // 2]
    enc = Encoder()
    n = UPLINK_RATE * FRAME_MS // 1000
    for i, off in enumerate(range(0, len(half) - n + 1, n)):
        for pkt in enc.encode(half[off : off + n]):
            await w.ws.send(HEADER.pack(0x01, 0, 0, tl.turn_id, i) + pkt)  # type: ignore[union-attr]
        await asyncio.sleep(FRAME_MS / 1000)
    print("  dropping connection abruptly")
    await w.close(abrupt=True)
    await asyncio.sleep(1.0)
    w2 = await connected(args)
    print(f"  reconnected, new session {w2.session_id}")
    tl2 = await w2.ask(pcm, args.lang)
    report_turn(tl2)
    await w2.close()
    ok = tl2.status == "completed" and tl2.frames > 0
    print(f"  reconnect-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


async def cmd_scenario(args) -> int:
    """JSON: {"steps": [{"do": "ask", "wav": "...", "lang": "ro"}, {"do": "abort_test", ...}, {"do": "reconnect", ...}]}"""
    spec = json.loads(Path(args.file).read_text(encoding="utf-8"))
    base = Path(args.file).parent
    rc = 0
    for step in spec["steps"]:
        ns = argparse.Namespace(**{**vars(args), **step})
        if step.get("wav"):
            ns.wav = str((base / step["wav"]).resolve())
        if step.get("wav_dir"):
            ns.wav_dir = str((base / step["wav_dir"]).resolve())
        ns.lang = step.get("lang", args.lang)
        print(f"\n== {step['do']} {step.get('wav', '')}")
        fn = {"ask": cmd_ask, "abort_test": cmd_abort, "reconnect": cmd_reconnect, "bench": cmd_bench}[step["do"]]
        rc |= await fn(ns)
    print(f"\nscenario: {'PASS' if rc == 0 else 'FAIL'}")
    return rc


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="ws://127.0.0.1:8765/ws/device")
    p.add_argument("--device-id", default="sim-000001")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pair")
    a = sub.add_parser("ask")
    a.add_argument("--wav", required=True)
    a.add_argument("--lang", choices=["ro", "en"])
    a.add_argument("--save")
    a.add_argument("--fast", action="store_true", help="stream faster than realtime (TTFA not meaningful)")
    b = sub.add_parser("bench")
    b.add_argument("--wav")
    b.add_argument("--wav-dir")
    b.add_argument("--lang", choices=["ro", "en"])
    b.add_argument("--repeat", type=int, default=1)
    b.add_argument("--strict", action="store_true", help="fail when TTFA is over target")
    ab = sub.add_parser("abort-test")
    ab.add_argument("--wav", required=True)
    ab.add_argument("--lang", choices=["ro", "en"])
    ab.add_argument("--abort-after-ms", type=int, default=400)
    rc = sub.add_parser("reconnect-test")
    rc.add_argument("--wav", required=True)
    rc.add_argument("--lang", choices=["ro", "en"])
    sc = sub.add_parser("scenario")
    sc.add_argument("file")
    sc.add_argument("--lang", choices=["ro", "en"])
    sc.add_argument("--strict", action="store_true")
    args = p.parse_args()
    for attr, default in (("save", None), ("fast", False), ("repeat", 1), ("strict", False), ("wav_dir", None), ("abort_after_ms", 400)):
        if not hasattr(args, attr):
            setattr(args, attr, default)
    fn = {
        "pair": cmd_pair,
        "ask": cmd_ask,
        "bench": cmd_bench,
        "abort-test": cmd_abort,
        "reconnect-test": cmd_reconnect,
        "scenario": cmd_scenario,
    }[args.cmd]
    sys.exit(asyncio.run(fn(args)))


if __name__ == "__main__":
    main()
