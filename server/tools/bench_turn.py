"""Latency benchmark: one real turn (real providers) with a WAV sample streamed in real time.

Prints every pipeline mark relative to the end of speech, plus downlink audio pacing: if the TTS
audio arrives slower than real time, the watch runs dry and the listener hears gaps between words.

    .venv\\Scripts\\python tools\\bench_turn.py tests\\samples\\ro_1.wav --language ro [--no-search]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_watch import load_wav_16k  # noqa: E402

from app.config import load_providers_config  # noqa: E402
from app.device_settings import DeviceSettings  # noqa: E402
from app.pipeline.chunker import ChunkerConfig  # noqa: E402
from app.pipeline.conversation import ConversationPipeline  # noqa: E402
from app.pipeline.metrics import mono_ms  # noqa: E402
from app.pipeline.turn import TurnContext  # noqa: E402
from app.providers.router import ProviderRouter  # noqa: E402

FRAME_MS = 60
PACKET_MS = 60  # downlink Opus packet duration


class BenchIO:
    def __init__(self) -> None:
        self.events: list[tuple[float, str, dict]] = []
        self.packets: list[float] = []

    async def send(self, turn, type_, **fields) -> bool:
        self.events.append((mono_ms(), type_, fields))
        return True

    async def send_audio(self, turn, packet) -> bool:
        now = mono_ms()
        if not self.packets:
            turn.marks.first_frame_sent = now
        self.packets.append(now)
        return True


def simple_messages(turn, user_text):
    s = turn.settings
    return [
        {"role": "system", "content": "You are a helpful voice assistant. Always reply in Romanian. "
         f"Be brief, at most about {s.max_reply_chars} characters. Plain sentences only, no URLs."},
        {"role": "user", "content": user_text},
    ]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--language", default="ro")
    ap.add_argument("--no-search", action="store_true")
    ap.add_argument("--silence-ms", type=int, default=2000)
    ap.add_argument("--repeat", type=int, default=2, help="turns in one process (the first warms up)")
    args = ap.parse_args()

    cfg = load_providers_config()
    router = ProviderRouter(config=cfg)
    pipeline = ConversationPipeline(
        router, ChunkerConfig.from_dict(cfg.get("chunker", {})), messages_builder=simple_messages
    )
    settings = DeviceSettings(language=args.language, web_search=not args.no_search)
    for n in range(1, args.repeat + 1):
        print(f"\n===== turn {n}{' (warm-up)' if n == 1 and args.repeat > 1 else ''} =====")
        await one_turn(args, pipeline, settings)


async def one_turn(args, pipeline, settings) -> None:
    turn = TurnContext(turn_id=1, session_id="bench", device_id="bench", language=args.language,
                       settings=settings, downlink_rate=24000)
    io = BenchIO()

    speech = load_wav_16k(args.wav).tobytes()
    pcm = speech + b"\0" * (16 * 2 * args.silence_ms)
    frame = 16 * 2 * FRAME_MS
    audio_ms = len(speech) / 32

    async def feed() -> None:
        t0 = time.monotonic()
        for i in range(0, len(pcm), frame):
            await turn.audio_in.put((pcm[i : i + frame], mono_ms()))
            await asyncio.sleep(max(0.0, t0 + (i // frame + 1) * FRAME_MS / 1000 - time.monotonic()))

    feeder = asyncio.create_task(feed())
    result = await pipeline.run(turn, io)
    feeder.cancel()

    m = turn.marks
    base = m.speech_end
    def rel(v):
        return "   -   " if v is None or base is None else f"{v - base:7.0f}"
    print(f"\nresult: {result.status}   speech in sample: {audio_ms:.0f} ms")
    print(f"user:  {turn.user_text}\nreply: {turn.assistant_text}\n")
    print("ms after end of speech:")
    listen_stop = next((t for t, ty, _ in io.events if ty == "listen_stop"), None)
    for name, v in [("VAD detects end (listen_stop)", listen_stop), ("STT final text", m.stt_final),
                    ("LLM request sent", m.llm_request), ("LLM first token", m.llm_first_token),
                    ("first text to TTS", m.tts_first_text), ("TTS first PCM", m.tts_first_pcm),
                    ("first audio packet out", m.first_frame_sent)]:
        print(f"  {name:32s}{rel(v)}")
    if io.packets:
        # Pacing: audio available vs. real-time playback started at the first packet.
        start = io.packets[0]
        worst = 0.0
        for i, t in enumerate(io.packets):
            late = (t - start) - i * PACKET_MS  # >0: packet i arrived after it should play
            worst = max(worst, late)
        dur = len(io.packets) * PACKET_MS
        print(f"\ndownlink: {len(io.packets)} packets = {dur / 1000:.1f} s audio, "
              f"sent over {(io.packets[-1] - start) / 1000:.1f} s; worst lateness vs real time: {worst:.0f} ms")
        gaps = [b - a for a, b in zip(io.packets, io.packets[1:]) if b - a > 150]
        print(f"send gaps > 150 ms: {[round(g) for g in gaps]}")
    print("usage:", [(u.kind, u.unit, round(u.quantity, 2)) for u in turn.usage])


if __name__ == "__main__":
    asyncio.run(main())
