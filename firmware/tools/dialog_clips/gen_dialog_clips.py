"""Generate firmware/main/dialog_clips.h: "Dialog mode on" spoken in every watch UI language.

The watch plays the clip itself when the mic button is held for 2 s (no server round trip, works the same
online or offline). Same voice, model and instructions as the replies (config/providers.openai.json), then
60 ms Opus packets at 16 kHz (the speaker's rate), like the reply audio.

Run from server/ with its venv (needs the OpenAI key in server/.env):
    .venv\\Scripts\\python ..\\firmware\\tools\\dialog_clips\\gen_dialog_clips.py
"""

from __future__ import annotations

import difflib
import io
import json
import sys
import unicodedata
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "server"))

from app.audio.codec import OpusEncoder  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.providers.tts.openai import language_instruction  # noqa: E402

OUT = ROOT / "firmware" / "main" / "dialog_clips.h"
RATE = 16000
BITRATE = 32000  # the reply audio's bitrate (opus_downlink_bitrate)
TAKES = 4
GOOD_ENOUGH = 0.9

# The UI languages (firmware/tools/i18n): the clip follows the watch's language.
PHRASES: dict[str, str] = {
    "en": "Dialog mode on.",
    "ro": "Mod dialog activat.",
    "de": "Dialogmodus aktiviert.",
    "fr": "Mode dialogue activé.",
    "es": "Modo diálogo activado.",
    "it": "Modalità dialogo attivata.",
    "pt": "Modo de diálogo ativado.",
    "nl": "Dialoogmodus ingeschakeld.",
    "ca": "Mode diàleg activat.",
    "gl": "Modo diálogo activado.",
    "sv": "Dialogläge på.",
    "da": "Dialogtilstand slået til.",
    "no": "Dialogmodus på.",
    "is": "Samtalshamur virkur.",
    "fi": "Keskustelutila päällä.",
    "et": "Dialoogirežiim sees.",
    "lv": "Dialoga režīms ieslēgts.",
    "lt": "Dialogo režimas įjungtas.",
    "cy": "Modd sgwrs ymlaen.",
    "pl": "Tryb rozmowy włączony.",
    "cs": "Režim dialogu zapnut.",
    "sk": "Režim dialógu zapnutý.",
    "hu": "Párbeszéd mód bekapcsolva.",
    "sl": "Način pogovora vklopljen.",
    "hr": "Način razgovora uključen.",
    "bs": "Način razgovora uključen.",
    "sr": "Режим разговора укључен.",
    "el": "Λειτουργία διαλόγου ενεργή.",
    "bg": "Режимът на диалог е включен.",
    "mk": "Режимот на дијалог е вклучен.",
    "uk": "Режим діалогу увімкнено.",
}


def speak(client: httpx.Client, key: str, base_url: str, tts: dict, lang: str, text: str) -> bytes:
    """24 kHz s16le mono PCM."""
    instructions = " ".join(filter(None, [language_instruction(lang), tts.get("instructions", "")]))
    r = client.post(
        f"{base_url.rstrip('/')}/audio/speech",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": tts["model"], "voice": tts["default_voice"], "input": text, "response_format": "pcm",
              "instructions": instructions},
    )
    r.raise_for_status()
    return r.content


def heard(client: httpx.Client, key: str, base_url: str, lang: str, pcm: bytes) -> str:
    """What a speech-to-text model hears in the clip (24 kHz PCM)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)
    r = client.post(
        f"{base_url.rstrip('/')}/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        files={"file": ("clip.wav", buf.getvalue(), "audio/wav")},
        data={"model": "gpt-4o-transcribe", "language": lang},
    )
    r.raise_for_status()
    return r.json().get("text", "")


def similarity(a: str, b: str) -> float:
    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", s.lower())
        return "".join(c for c in s if c.isalnum())

    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def trim(pcm: bytes, rate: int = 24000) -> bytes:
    """Cut leading / trailing near-silence, keeping 40 ms on each side."""
    import numpy as np

    s = np.frombuffer(pcm, dtype=np.int16)
    loud = np.nonzero(np.abs(s) > 300)[0]
    if loud.size == 0:
        return pcm
    pad = rate * 40 // 1000
    return s[max(0, loud[0] - pad): loud[-1] + pad].tobytes()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    key = s.provider_key("openai_api_key")
    tts = json.loads((ROOT / "server" / "config" / "providers.openai.json").read_text("utf-8"))["tts"]["default"]
    clips: dict[str, list[bytes]] = {}
    with httpx.Client(timeout=60) as client:
        for lang, text in PHRASES.items():
            # The voice sometimes slips on a short phrase in a smaller language: up to TAKES takes, the one a
            # speech-to-text model hears closest to the text is kept.
            best = (-1.0, b"", "")
            for _ in range(TAKES):
                pcm = trim(speak(client, key, s.openai_base_url, tts, lang, text))
                got = heard(client, key, s.openai_base_url, lang, pcm)
                best = max(best, (similarity(got, text), pcm, got), key=lambda t: t[0])
                if best[0] >= GOOD_ENOUGH:
                    break
            score, pcm, got = best
            enc = OpusEncoder(RATE, bitrate=BITRATE)
            clips[lang] = enc.encode(pcm, 24000) + enc.flush()
            print(f"{lang}: {len(pcm) / 48000:.2f} s, {sum(map(len, clips[lang]))} bytes, "
                  f"heard {got!r} ({score:.2f})", flush=True)

    lines = [
        "/* \"Dialog mode on\" in every UI language (firmware/main/dialog_clips.h), generated by",
        " * firmware/tools/dialog_clips/gen_dialog_clips.py: 60 ms Opus packets, 16 kHz mono. Do not edit. */",
        "#pragma once",
        "#include \"protocol_client.h\"",
        "",
        "typedef struct {",
        "    const char   *lang;",
        "    proto_clip_t  clip;",
        "} dialog_clip_t;",
        "",
    ]
    for lang, packets in clips.items():
        data = b"".join(packets)
        lines.append(f"static const uint16_t DIALOG_LENS_{lang.upper()}[{len(packets)}] = {{ "
                     + ", ".join(str(len(p)) for p in packets) + " };")
        lines.append(f"static const uint8_t DIALOG_OPUS_{lang.upper()}[{len(data)}] = {{")
        for i in range(0, len(data), 24):
            lines.append("    " + ", ".join(f"0x{b:02x}" for b in data[i:i + 24]) + ",")
        lines.append("};")
    lines.append("")
    lines.append("static const dialog_clip_t DIALOG_CLIPS[] = {")
    for lang, packets in clips.items():
        u = lang.upper()
        lines.append(f'    {{ "{lang}", {{ DIALOG_OPUS_{u}, DIALOG_LENS_{u}, {len(packets)} }} }},')
    lines.append("};")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({sum(sum(map(len, p)) for p in clips.values())} bytes of audio)")


if __name__ == "__main__":
    main()
