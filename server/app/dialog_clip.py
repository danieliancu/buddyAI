"""The "Dialog mode on" announcement the watch plays itself (PROTOCOL.md 3.5).

One short clip per (language, voice): generated once with the reply voice, kept on disk and shared by every watch
with the same language and voice, sent to a watch only when it does not have it yet (hello `dialog_clip` = the id
it holds). The watch stores it and plays it locally when dialog mode starts - by holding the mic or by voice - so
the announcement costs nothing per use and needs no round trip.

The phrase: a fixed text for every language the profiles declare (supported_languages: the watch's UI languages,
plus the Qwen profile's Chinese, Japanese, Korean and Russian); nothing is translated on the fly, and a language
outside the active profile's list gets the English clip. A take that speech-to-text does not hear as the phrase is
tried again (the voice sometimes slips on a short phrase in a smaller language), when the profile can transcribe one.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import hashlib
import json
import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app import languages
from app.audio.codec import OpusEncoder
from app.config import get_settings
from app.device_settings import DeviceSettings
from app.providers.tts.base import TTSRequest

log = logging.getLogger(__name__)

VERSION = 1  # part of the id: a change of format or phrase rules makes every watch fetch a new clip
RATE = 16000  # the watch's speaker rate (Opus decodes any rate; the clip is small at 16 kHz)
BITRATE = 32000
TAKES = 3
GOOD_ENOUGH = 0.85
MAX_BYTES = 16000  # the watch refuses bigger clips

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
    # the Qwen profile's other languages
    "zh": "对话模式已开启。",
    "ja": "会話モードがオンになりました。",
    "ko": "대화 모드가 켜졌습니다.",
    "ru": "Режим диалога включён.",
}


@dataclass(frozen=True)
class Clip:
    id: str
    language: str
    packets: list[bytes]

    def message(self) -> dict:
        """The `dialog_clip` message fields: packet lengths and the packets, concatenated, in base64."""
        return {"id": self.id, "language": self.language, "lens": [len(p) for p in self.packets],
                "opus": base64.b64encode(b"".join(self.packets)).decode("ascii")}


def _dir() -> Path:
    d = get_settings().data_dir / "dialog_clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


_locks: dict[str, asyncio.Lock] = {}


def clip_language(settings: DeviceSettings, last_language: str | None) -> str:
    """The watch's language for the announcement: its setting, or in "auto" the last language heard (as the watch's
    own menus do), then the owner's preferred one - only a language the active profile declares and has a phrase
    for; otherwise English."""
    declared = set(languages.supported_codes()) & set(PHRASES)
    candidates = [settings.language] if settings.language != languages.AUTO else []
    candidates += [last_language, settings.preferred_language] if settings.language == languages.AUTO else []
    for code in candidates:
        if code in declared:
            return code
    return languages.DEFAULT_LANGUAGE


def phrase(code: str) -> str:
    return PHRASES.get(code, PHRASES[languages.DEFAULT_LANGUAGE])


def _trim(pcm: bytes, rate: int) -> bytes:
    """Leading / trailing near-silence cut, 40 ms kept on each side."""
    s = np.frombuffer(pcm, dtype=np.int16)
    loud = np.nonzero(np.abs(s) > 300)[0]
    if loud.size == 0:
        return pcm
    pad = rate * 40 // 1000
    return s[max(0, loud[0] - pad): loud[-1] + pad].tobytes()


def _similar(a: str, b: str) -> float:
    def norm(s: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if c.isalnum())

    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


async def _speak(router, sel, code: str, text: str, settings: DeviceSettings) -> tuple[bytes, int]:
    async def one():
        yield text

    req = TTSRequest(voice=sel.voice, language=code, speech_rate=settings.speech_rate, instructions=sel.instructions or "")
    pcm, rate = b"", 24000
    async for chunk in sel.provider.stream(one(), req):
        pcm += chunk.pcm
        rate = chunk.sample_rate
    return pcm, rate


async def _heard(pcm: bytes, rate: int, code: str) -> str | None:
    """What speech-to-text hears in the take (OpenAI REST, when the key is there; None: no check)."""
    s = get_settings()
    key = s.provider_key("openai_api_key")
    if not key or s.mock_providers:
        return None
    import io
    import wave

    import httpx

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{s.openai_base_url.rstrip('/')}/audio/transcriptions",
                                  headers={"Authorization": f"Bearer {key}"},
                                  files={"file": ("clip.wav", buf.getvalue(), "audio/wav")},
                                  data={"model": "gpt-4o-mini-transcribe", "language": code.split("-")[0]})
            r.raise_for_status()
            return r.json().get("text", "")
    except Exception as exc:  # noqa: BLE001 - the check is a nicety
        log.info("dialog clip: no transcription check (%s)", exc)
        return None


async def get(router, settings: DeviceSettings, last_language: str | None = None) -> Clip:
    """The clip for this watch (generated on first use, then from disk)."""
    code = clip_language(settings, last_language)
    text = phrase(code)
    sel = router.tts(code, settings)
    key = f"{VERSION}|{code}|{sel.provider.name}|{getattr(sel.provider, 'model', '')}|{sel.voice}|{settings.speech_rate}|{text}"
    cid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    path = _dir() / f"{cid}.json"
    lock = _locks.setdefault(cid, asyncio.Lock())
    async with lock:
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            return Clip(cid, code, [base64.b64decode(p) for p in data["packets"]])
        best: tuple[float, bytes, int] = (-1.0, b"", 24000)
        for _ in range(TAKES):
            pcm, rate = await _speak(router, sel, code, text, settings)
            pcm = _trim(pcm, rate)
            got = await _heard(pcm, rate, code)
            score = 1.0 if got is None else _similar(got, text)
            if score > best[0]:
                best = (score, pcm, rate)
            if score >= GOOD_ENOUGH:
                break
        _, pcm, rate = best
        enc = OpusEncoder(RATE, bitrate=BITRATE)
        packets = enc.encode(pcm, rate) + enc.flush()
        if sum(map(len, packets)) > MAX_BYTES:
            raise ValueError(f"dialog clip too big ({sum(map(len, packets))} bytes)")
        path.write_text(json.dumps({"language": code, "voice": sel.voice, "text": text, "score": round(best[0], 2),
                                    "packets": [base64.b64encode(p).decode("ascii") for p in packets]}), "utf-8")
        log.info("dialog clip %s: %s %r (%s, %d packets, %d bytes, heard %.2f)", cid, code, text, sel.voice,
                 len(packets), sum(map(len, packets)), best[0])
        return Clip(cid, code, packets)
