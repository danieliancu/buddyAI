"""Language registry (config/languages.json), per-profile support and spoken-language detection.

"auto" means: no language hint for STT, detect the language from the transcript, reply in the
same language. Detection only routes TTS/chunker and fills statistics; the LLM is always told to
answer in the user's language, so a wrong guess never produces a reply in the wrong language.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config import SERVER_DIR, load_providers_config

log = logging.getLogger(__name__)

AUTO = "auto"
DEFAULT_LANGUAGE = "en"
MIN_DETECT_CONFIDENCE = 0.25

# Short sample sentences for voice previews; other languages fall back to English.
SAMPLES = {
    "en": "Hi! I'm your ola assistant. How can I help you today?",
    "ro": "Salut! Sunt asistentul tău ola. Cu ce te pot ajuta astăzi?",
    "de": "Hallo! Ich bin dein ola-Assistent. Wie kann ich dir heute helfen?",
    "fr": "Bonjour ! Je suis ton assistant ola. Comment puis-je t'aider aujourd'hui ?",
    "es": "¡Hola! Soy tu asistente ola. ¿En qué puedo ayudarte hoy?",
    "it": "Ciao! Sono il tuo assistente ola. Come posso aiutarti oggi?",
    "pt": "Olá! Sou o teu assistente ola. Como posso ajudar hoje?",
    "nl": "Hallo! Ik ben je ola-assistent. Waarmee kan ik je vandaag helpen?",
    "pl": "Cześć! Jestem twoim asystentem ola. W czym mogę ci dziś pomóc?",
    "cs": "Ahoj! Jsem tvůj asistent ola. S čím ti dnes můžu pomoct?",
    "sk": "Ahoj! Som tvoj asistent ola. S čím ti dnes môžem pomôcť?",
    "hu": "Szia! A ola asszisztensed vagyok. Miben segíthetek ma?",
    "sv": "Hej! Jag är din ola-assistent. Hur kan jag hjälpa dig i dag?",
    "da": "Hej! Jeg er din ola-assistent. Hvordan kan jeg hjælpe dig i dag?",
    "no": "Hei! Jeg er din ola-assistent. Hvordan kan jeg hjelpe deg i dag?",
    "fi": "Hei! Olen ola-avustajasi. Miten voin auttaa sinua tänään?",
    "el": "Γεια σου! Είμαι ο βοηθός σου ola. Πώς μπορώ να σε βοηθήσω σήμερα;",
    "ru": "Привет! Я твой помощник ola. Чем могу помочь сегодня?",
    "uk": "Привіт! Я твій помічник ola. Чим можу допомогти сьогодні?",
    "bg": "Здравей! Аз съм твоят асистент ola. С какво мога да помогна днес?",
    "tr": "Merhaba! Ben ola asistanınım. Bugün sana nasıl yardımcı olabilirim?",
    "zh": "你好！我是你的 ola 助手。今天我能帮你做什么？",
    "ja": "こんにちは！ola アシスタントです。今日はどんなお手伝いができますか？",
    "ko": "안녕하세요! ola 도우미입니다. 오늘 무엇을 도와드릴까요?",
    "ar": "مرحبًا! أنا مساعدك ola. كيف يمكنني مساعدتك اليوم؟",
    "hi": "नमस्ते! मैं आपका ola सहायक हूँ। आज मैं आपकी कैसे मदद कर सकता हूँ?",
    "he": "שלום! אני העוזר שלך ola. איך אוכל לעזור לך היום?",
    "id": "Halo! Saya asisten ola kamu. Ada yang bisa saya bantu hari ini?",
    "vi": "Xin chào! Tôi là trợ lý ola của bạn. Hôm nay tôi có thể giúp gì cho bạn?",
    "cy": "Helo! Fi yw dy gynorthwyydd ola. Sut alla i dy helpu di heddiw?",
}


# Languages the watch menus, messages and date line are translated into (firmware/tools/i18n/lang_*.py:
# keep in sync). For any other language the watch shows them in English.
WATCH_UI_LANGUAGES = frozenset({
    "en", "ro", "de", "fr", "es", "it", "pt", "nl", "ca", "gl",
    "sv", "da", "no", "is", "fi", "et", "lv", "lt", "cy",
    "pl", "cs", "sk", "hu", "sl", "hr", "bs", "sr",
    "el", "bg", "mk", "uk",
})


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    native_name: str
    script: str
    rtl: bool
    captions: bool
    azure_locale: str | None = None
    qwen_language_type: str | None = None
    abbreviations: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "native_name": self.native_name,
            "script": self.script,
            "rtl": self.rtl,
            "captions": self.captions,
            "watch_menus": self.code in WATCH_UI_LANGUAGES,
        }

    @property
    def watch_label(self) -> str:
        """A label the watch font can render (native name for Latin/Greek/Cyrillic, else English)."""
        return self.native_name if self.captions else self.name


@lru_cache
def registry() -> dict[str, Language]:
    data = json.loads((SERVER_DIR / "config" / "languages.json").read_text(encoding="utf-8"))
    out = {}
    for e in data["languages"]:
        e = dict(e)
        e["abbreviations"] = tuple(e.get("abbreviations", ()))
        out[e["code"]] = Language(**e)
    return out


def get(code: str | None) -> Language | None:
    return registry().get(code or "")


def is_known(code: str) -> bool:
    return code == AUTO or code in registry()


def supported_codes() -> list[str]:
    """Languages the active provider profile can both hear and speak."""
    supported = load_providers_config().get("supported_languages", "all")
    codes = list(registry()) if supported == "all" else [c for c in supported if c in registry()]
    return codes


def supported() -> list[Language]:
    return sorted((registry()[c] for c in supported_codes()), key=lambda l: l.name)


def abbreviations() -> dict[str, list[str]]:
    return {code: list(l.abbreviations) for code, l in registry().items() if l.abbreviations}


def display_name(code: str) -> str:
    lang = get(code)
    return lang.name if lang else code


def sample_sentence(code: str) -> str:
    return SAMPLES.get(code, SAMPLES["en"])


def watch_languages() -> list[dict[str, str]]:
    """The watch's language picker: the supported languages the watch menus are translated into
    (WATCH_UI_LANGUAGES - English, the European languages, Ukrainian), with a renderable label + English
    name (for search), sorted by label. Other languages can still be chosen in the web app."""
    items = [
        {"code": lang.code, "label": lang.watch_label, "name": lang.name}
        for lang in supported()
        if lang.code in WATCH_UI_LANGUAGES
    ]
    return sorted(items, key=lambda i: i["label"].casefold())


def quick_languages(preferred: str | None) -> list[dict[str, str]]:
    """Options for the watch's quick-settings language control (max 3, renderable labels)."""
    items = [{"code": AUTO, "label": "Auto"}, {"code": "en", "label": "English"}]
    lang = get(preferred)
    if lang and lang.code != "en":
        items.append({"code": lang.code, "label": lang.watch_label})
    return items


# --- detection -------------------------------------------------------------------------------

_detector_lock = threading.Lock()
_detector: Any = None
_detector_codes: frozenset[str] = frozenset()


def _build_detector(codes: frozenset[str]) -> Any:
    from lingua import IsoCode639_1, Language as LinguaLanguage, LanguageDetectorBuilder

    langs = []
    for code in codes:
        iso = getattr(IsoCode639_1, code.upper(), None)
        if iso is None and code == "no":
            iso = IsoCode639_1.NB  # lingua uses Bokmål for Norwegian
        if iso is not None:
            try:
                langs.append(LinguaLanguage.from_iso_code_639_1(iso))
            except ValueError:
                pass
    if len(langs) < 2:
        return None
    return LanguageDetectorBuilder.from_languages(*langs).build()


# Letters that exist in only one of the supported Cyrillic languages. Short Ukrainian and Russian
# sentences are otherwise easy to confuse, and mislabelling them matters to users.
_DISTINCTIVE = (("ґ", "uk"), ("ї", "uk"), ("є", "uk"), ("ў", "be"), ("ы", "ru"), ("э", "ru"), ("ё", "ru"))
PREFER_RATIO = 0.6  # a preferred language wins if it scores at least 60% of the top candidate


def _detector_for(codes: frozenset[str]) -> Any:
    global _detector, _detector_codes
    with _detector_lock:
        if _detector is None or codes != _detector_codes:
            try:
                _detector = _build_detector(codes)
            except ImportError:  # pragma: no cover - lingua not installed
                log.warning("lingua not installed: language auto-detection disabled")
                _detector = None
            _detector_codes = codes
        return _detector


def detect(text: str, prefer: list[str | None] | None = None) -> tuple[str | None, float]:
    """(language code, confidence) among supported languages, or (None, confidence) when unsure.

    `prefer`: languages to favour when the top candidates are close (e.g. the language already
    used in this conversation, the owner's preferred language).
    """
    text = (text or "").strip()
    if len(text) < 2:
        return None, 0.0
    codes = frozenset(supported_codes())
    lowered = text.lower()
    for letter, code in _DISTINCTIVE:
        if letter in lowered and code in codes:
            return code, 1.0
    detector = _detector_for(codes)
    if detector is None:
        return None, 0.0
    values = detector.compute_language_confidence_values(text)
    if not values:
        return None, 0.0

    def code_of(v: Any) -> str:
        c = v.language.iso_code_639_1.name.lower()
        return "no" if c == "nb" else c

    scores = {code_of(v): float(v.value) for v in values}
    top_code, top_value = code_of(values[0]), float(values[0].value)
    for wanted in prefer or []:
        if wanted and wanted in codes and scores.get(wanted, 0.0) >= top_value * PREFER_RATIO:
            return wanted, scores[wanted]
    if top_value < MIN_DETECT_CONFIDENCE or top_code not in codes:
        return None, top_value
    return top_code, top_value


def warm_up() -> None:
    """Build the detector at startup (a few seconds) instead of on the first turn."""
    detect("warm up the language detector")
