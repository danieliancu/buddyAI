"""Per-device settings: schema, defaults, theme presets, device-facing projection (PROTOCOL.md §5)."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app import languages

HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# The two watch themes, exactly as they are: "midnight" (blue) and "mono" (white). No custom colours.
THEME_PRESETS: dict[str, dict[str, str]] = {
    "midnight": {"accent": "#4F8CFF", "background": "#000000", "clock": "#FFFFFF", "text": "#B0B8C8"},
    "mono": {"accent": "#FFFFFF", "background": "#000000", "clock": "#FFFFFF", "text": "#9A9A9A"},
}
DEFAULT_THEME = "midnight"


class Theme(BaseModel):
    preset: str = DEFAULT_THEME
    accent: str = THEME_PRESETS[DEFAULT_THEME]["accent"]
    background: str = THEME_PRESETS[DEFAULT_THEME]["background"]
    clock: str = THEME_PRESETS[DEFAULT_THEME]["clock"]
    text: str = THEME_PRESETS[DEFAULT_THEME]["text"]

    @model_validator(mode="before")
    @classmethod
    def _preset_colors(cls, data: Any) -> Any:
        # The colours always come from the preset. Older themes (ocean, forest, sunset, custom colours)
        # become the default blue one.
        if isinstance(data, dict):
            preset = data.get("preset")
            if preset not in THEME_PRESETS:
                preset = DEFAULT_THEME
            data = {"preset": preset, **THEME_PRESETS[preset]}
        return data

    @field_validator("accent", "background", "clock", "text")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX_COLOR.match(v):
            raise ValueError("color must be #RRGGBB")
        return v.upper()


class DeviceSettings(BaseModel):
    # --- device-facing -----------------------------------------------------------
    language: str = "auto"  # "auto" (reply in the language spoken) or an ISO 639-1 code
    preferred_language: str | None = None  # owner's main language, offered on the watch quick toggle
    volume: int = Field(70, ge=0, le=100)
    brightness: int = Field(80, ge=5, le=100)
    screen_timeout_s: int = Field(15, ge=5, le=300)
    timezone: str = "Europe/London"
    theme: Theme = Field(default_factory=Theme)
    max_listen_s: int = Field(30, ge=3, le=60)  # longest question, counted from the first word
    wait_for_speech_s: int = Field(20, ge=5, le=60)  # mic open, waiting for the first word (free: no STT)
    # --- AI (server-only) --------------------------------------------------------
    persona_id: int | None = None
    custom_instructions: str = Field("", max_length=2000)
    llm_model: str | None = None  # None -> server default
    tts_voice: str | None = None  # None -> profile default voice
    tts_voice_overrides: dict[str, str] = Field(default_factory=dict)  # language code -> voice
    speech_rate: float = Field(1.0, ge=0.5, le=2.0)
    vad_sensitivity: Literal["low", "medium", "high"] = "medium"
    max_reply_chars: int = Field(400, ge=80, le=2000)
    history_turns: int = Field(6, ge=0, le=30)
    web_search: bool = True  # let the AI look things up on the internet (each search is billed)

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        # Rev. 2 stored per-language voices as tts_voice_ro / tts_voice_en.
        if isinstance(data, dict) and ("tts_voice_ro" in data or "tts_voice_en" in data):
            data = dict(data)
            overrides = dict(data.get("tts_voice_overrides") or {})
            for code in ("ro", "en"):
                voice = data.pop(f"tts_voice_{code}", None)
                if voice:
                    overrides.setdefault(code, voice)
            data["tts_voice_overrides"] = overrides
        return data

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        posix_tz(v)  # raises if unknown
        return v

    @field_validator("language")
    @classmethod
    def _language(cls, v: str) -> str:
        if not languages.is_known(v):
            raise ValueError(f"unknown language {v!r}")
        return v

    @field_validator("preferred_language")
    @classmethod
    def _preferred(cls, v: str | None) -> str | None:
        if v is not None and (v == languages.AUTO or not languages.is_known(v)):
            raise ValueError(f"unknown language {v!r}")
        return v

    @field_validator("tts_voice_overrides")
    @classmethod
    def _overrides(cls, v: dict[str, str]) -> dict[str, str]:
        bad = [k for k in v if k == languages.AUTO or not languages.is_known(k)]
        if bad:
            raise ValueError(f"unknown language(s) {bad}")
        return v


DEVICE_EDITABLE = {
    "language",
    "preferred_language",
    "volume",
    "brightness",
    "screen_timeout_s",
    "theme",
}


def device_view(s: DeviceSettings, chat_title: str = "") -> dict[str, Any]:
    """Subset sent to the watch. chat_title: the active persona's name for the dialog screen
    ("" = the default persona: the watch shows its own greeting)."""
    return {
        "chat_title": chat_title,
        "language": s.language,
        "quick_languages": languages.quick_languages(s.preferred_language),
        "volume": s.volume,
        "brightness": s.brightness,
        "screen_timeout_s": s.screen_timeout_s,
        "tz_posix": posix_tz(s.timezone),
        "theme": s.theme.model_dump(),
        # The watch's whole listening window: waiting for the first word + the longest question.
        "max_listen_s": s.wait_for_speech_s + s.max_listen_s,
    }


def merge(current: DeviceSettings, changes: dict[str, Any]) -> DeviceSettings:
    data = current.model_dump()
    for key, value in changes.items():
        if key == "theme" and isinstance(value, dict):
            # Only the two presets exist and their colours are fixed: refuse anything else.
            preset = value.get("preset", data["theme"]["preset"])
            if preset not in THEME_PRESETS:
                raise ValueError(f"unknown theme {preset!r}; choose from {', '.join(THEME_PRESETS)}")
            for k, v in value.items():
                if k != "preset" and str(v).upper() != THEME_PRESETS[preset].get(k):
                    raise ValueError("custom theme colours are not supported")
            data["theme"] = {"preset": preset}
        else:
            data[key] = value
    return DeviceSettings.model_validate(data)


@lru_cache(maxsize=64)
def posix_tz(iana: str) -> str:
    """POSIX TZ string for ESP-IDF (read from the TZif footer in the tzdata package)."""
    parts = iana.split("/")
    try:
        ref = resources.files("tzdata.zoneinfo").joinpath(*parts)
        raw = ref.read_bytes()
    except (FileNotFoundError, IsADirectoryError, ModuleNotFoundError) as exc:
        raise ValueError(f"unknown timezone {iana!r}") from exc
    if not raw.startswith(b"TZif"):
        raise ValueError(f"unknown timezone {iana!r}")
    footer = raw.rstrip(b"\n").rsplit(b"\n", 1)[-1]
    return footer.decode("ascii") or "UTC0"
