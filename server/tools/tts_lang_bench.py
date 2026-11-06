"""Real-provider check for foreign passages (pipeline/lang_tag.py + providers/tts/multilang.py).

1. Every passage route of the profile, for every language with a sample sentence: synthesize, time the
   first audio, then transcribe the audio (OpenAI whisper-1) and check that the voice really spoke that
   language (not the reply's language with a foreign accent).
2. Mixed replies through MultiLangTTS (ro->en, bg->fr+hu, en->ro, de->it): order, timing, a WAV to listen to.
3. Speed: a reply without passages, the reply's voice called directly (as before) vs through MultiLangTTS,
   alternating, N runs each: first audio and total time must be the same within noise.

    .venv\\Scripts\\python tools\\tts_lang_bench.py [--profile openai|qwen] [--languages bg,fr,hu] [--runs 20]

Needs the profile's keys (and the OpenAI key for the language check). Writes out/tts_lang_bench/report.json.
Costs a few pence: ~2,000 characters of speech per profile plus a few minutes of transcription.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app import languages  # noqa: E402
from app.config import SERVER_DIR, Settings, get_settings  # noqa: E402
from app.device_settings import DeviceSettings  # noqa: E402
from app.providers.router import ProviderRouter  # noqa: E402
from app.providers.tts.base import TTSRequest  # noqa: E402
from app.providers.tts.multilang import MultiLangTTS, Route  # noqa: E402

OUT = SERVER_DIR / "out" / "tts_lang_bench"

MIXED = [
    ("ro", [("ro", "În engleză britanică se spune:"), ("en", "My car is just around the corner."), ("ro", "Sună natural.")]),
    ("bg", [("bg", "На френски:"), ("fr", "Ma voiture est juste au coin de la rue."), ("bg", "А на унгарски:"),
            ("hu", "Az autóm itt van a sarkon.")]),
    ("en", [("en", "In Romanian you say:"), ("ro", "Mașina mea e chiar după colț."), ("en", "Easy.")]),
    ("de", [("de", "Auf Italienisch sagt man:"), ("it", "La mia macchina è dietro l'angolo."), ("de", "Ganz einfach.")]),
]
SPEED = [("ro", "Bună ziua! Mâine la 14:30 ai programare la medic."), ("ro", "Nu uita să iei actele cu tine, te rog.")]


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def detect_language(client: httpx.AsyncClient, settings: Settings, wav: bytes) -> str:
    key = settings.provider_key("openai_api_key")
    if not key:
        return "?"
    r = await client.post(
        f"{settings.openai_base_url.rstrip('/')}/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        files={"file": ("a.wav", wav, "audio/wav")},
        data={"model": "whisper-1", "response_format": "verbose_json"},
        timeout=60,
    )
    r.raise_for_status()
    return (r.json().get("language") or "?").lower()


def same_language(code: str, whisper_name: str) -> bool:
    lang = languages.get(code)
    if lang is None:
        return False
    names = {lang.name.lower(), lang.name.lower().split()[0], lang.native_name.lower()}
    aliases = {"no": {"norwegian", "nynorsk"}, "zh": {"chinese", "mandarin"}, "fil": {"tagalog"}, "ms": {"malay"}}
    return whisper_name in names or whisper_name in aliases.get(code, set()) or whisper_name == code


async def synth(route: Route, text: str, rate_out: list[int]) -> tuple[bytes, float | None, float]:
    async def one():
        yield text

    t0 = time.perf_counter()
    first = None
    pcm = b""
    req = TTSRequest(voice=route.voice, language=route.language, instructions=route.instructions or "")
    async for chunk in route.provider.stream(one(), req):
        if first is None:
            first = time.perf_counter() - t0
        pcm += chunk.pcm
        rate_out[:] = [chunk.sample_rate]
    return pcm, first, time.perf_counter() - t0


async def run_multi(tts: MultiLangTTS, frags: list[tuple[str, str]]) -> tuple[bytes, float | None, float, int]:
    async def gen():
        for f in frags:
            yield f

    t0 = time.perf_counter()
    first = None
    pcm = b""
    rate = 24000
    async for chunk in tts.stream(gen()):
        if first is None:
            first = time.perf_counter() - t0
        pcm += chunk.pcm
        rate = chunk.sample_rate
    return pcm, first, time.perf_counter() - t0, rate


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--languages", default="")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--skip-routes", action="store_true")
    args = ap.parse_args()

    base = get_settings()
    settings = Settings(ai_profile=args.profile) if args.profile else base
    config = json.loads(settings.providers_config.read_text(encoding="utf-8"))
    router = ProviderRouter(settings=settings, config=config)
    ds = DeviceSettings()
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {"profile": settings.ai_profile, "azure": router.azure_configured(), "routes": [], "mixed": [],
                    "speed": {}}
    codes = [c for c in (args.languages.split(",") if args.languages else languages.SAMPLES) if c]
    print(f"profile {settings.ai_profile}; Azure key {'set' if report['azure'] else 'missing'}")

    async with httpx.AsyncClient() as client:
        # 1. every route, every language
        if not args.skip_routes:
            for code in codes:
                reply = "en" if code == "ro" else "ro"
                text = languages.sample_sentence(code)
                try:
                    routes = router.tts_passage_routes(code, ds, reply)
                except Exception as exc:  # noqa: BLE001
                    routes = []
                    print(f"{code}: routing error {exc}")
                if not routes:
                    print(f"{code:4} no route: read in the reply language, as before")
                    report["routes"].append({"language": code, "route": None})
                for route in routes:
                    row = {"language": code, "provider": route.provider.name, "voice": route.voice}
                    rate = [24000]
                    try:
                        pcm, first, total = await synth(route, text, rate)
                        heard = await detect_language(client, base, wav_bytes(pcm, rate[0])) if pcm else "-"
                        row.update(first_audio_s=round(first or -1, 3), total_s=round(total, 3),
                                   audio_s=round(len(pcm) / 2 / rate[0], 2), heard=heard,
                                   ok=bool(pcm) and same_language(code, heard))
                    except Exception as exc:  # noqa: BLE001
                        row.update(ok=False, error=str(exc)[:200])
                    report["routes"].append(row)
                    print(f"{code:4} {row['provider']:11} {row['voice']:28} first {row.get('first_audio_s', '-')}s "
                          f"heard {row.get('heard', '-'):12} {'OK' if row['ok'] else 'FAIL ' + row.get('error', '')}")

        # 2. mixed replies
        for reply, frags in MIXED:
            main_sel = router.tts(reply, ds)
            tts = MultiLangTTS(Route(main_sel.provider, main_sel.voice, reply, main_sel.instructions),
                               lambda lang, r=reply: router.tts_passage_routes(lang, ds, r))
            try:
                pcm, first, total, rate = await run_multi(tts, frags)
                path = OUT / f"mixed_{reply}_{'_'.join(sorted({l for l, _ in frags} - {reply}))}.wav"
                path.write_bytes(wav_bytes(pcm, rate))
                row = {"reply": reply, "first_audio_s": round(first or -1, 3), "total_s": round(total, 3),
                       "audio_s": round(len(pcm) / 2 / rate, 2), "fallbacks": tts.fallbacks,
                       "usage": {f"{k[0]}/{k[1]}": v for k, v in tts.usage.items()}, "wav": str(path)}
            except Exception as exc:  # noqa: BLE001
                row = {"reply": reply, "error": str(exc)[:200]}
            report["mixed"].append(row)
            print("mixed", row)

        # 3. speed: no passages, direct vs MultiLangTTS
        main_sel = router.tts("ro", ds)
        main_route = Route(main_sel.provider, main_sel.voice, "ro", main_sel.instructions)
        direct_first, direct_total, multi_first, multi_total = [], [], [], []

        async def direct() -> tuple[float, float]:
            from app.pipeline.spoken import for_speech

            async def gen():
                for _, t in SPEED:
                    yield for_speech(t, "ro")

            t0 = time.perf_counter()
            first = None
            req = TTSRequest(voice=main_sel.voice, language="ro", instructions=main_sel.instructions or "")
            async for _ in main_sel.provider.stream(gen(), req):
                if first is None:
                    first = time.perf_counter() - t0
            return first or -1, time.perf_counter() - t0

        for i in range(args.runs):
            for which in ((0, 1) if i % 2 == 0 else (1, 0)):  # alternate, so network drift hits both
                if which == 0:
                    f, t = await direct()
                    direct_first.append(f)
                    direct_total.append(t)
                else:
                    _, f, t, _ = await run_multi(MultiLangTTS(main_route, lambda lang: []), SPEED)
                    multi_first.append(f or -1)
                    multi_total.append(t)
        med = statistics.median
        report["speed"] = {
            "runs": args.runs,
            "direct_first_audio_median_s": round(med(direct_first), 3),
            "multilang_first_audio_median_s": round(med(multi_first), 3),
            "direct_total_median_s": round(med(direct_total), 3),
            "multilang_total_median_s": round(med(multi_total), 3),
        }
        print("speed", report["speed"])

    (OUT / f"report_{settings.ai_profile}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    failed = [r for r in report["routes"] if r.get("route", 1) is not None and not r.get("ok")]
    print(f"routes: {len(report['routes'])}, failed: {len(failed)} -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
