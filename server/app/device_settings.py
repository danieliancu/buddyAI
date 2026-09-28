"""Per-device settings: schema, defaults, theme presets, device-facing projection (PROTOCOL.md §5)."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

THEME_PRESETS: dict[str, dict[str, str]] = {
    "midnight": {"accent": "#4F8CFF", "background": "#000000", "clock": "#FFFFFF", "text": "#B0B8C8"},
    "ocean": {"accent": "#00C2D1", "background": "#001A26", "clock": "#E6FBFF", "text": "#8FC9D6"},
    "forest": {"accent": "#3DDC84", "background": "#04140A", "clock": "#EFFFF4", "text": "#9CC9AA"},
    "sunset": {"accent": "#FF7A45", "background": "#1A0A05", "clock": "#FFF3EC", "text": "#E0B09A"},
    "mono": {"accent": "#FFFFFF", "background": "#000000", "clock": "#FFFFFF", "text": "#9A9A9A"},
}


class Theme(BaseModel):
    preset: str = "midnight"
    accent: str = THEME_PRESETS["midnight"]["accent"]
    background: str = THEME_PRESETS["midnight"]["background"]
    clock: str = THEME_PRESETS["midnight"]["clock"]
    text: str = THEME_PRESETS["midnight"]["text"]

    @field_validator("accent", "background", "clock", "text")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX_COLOR.match(v):
            raise ValueError("color must be #RRGGBB")
        return v.upper()


class DeviceSettings(BaseModel):
    # --- device-facing -----------------------------------------------------------
    language: Literal["ro", "en"] = "en"
    volume: int = Field(70, ge=0, le=100)
    brightness: int = Field(80, ge=5, le=100)
    screen_timeout_s: int = Field(15, ge=5, le=300)
    time_24h: bool = True
    timezone: str = "Europe/Bucharest"
    theme: Theme = Field(default_factory=Theme)
    max_listen_s: int = Field(15, ge=3, le=60)
    # --- AI (server-only) --------------------------------------------------------
    persona_id: int | None = None
    custom_instructions: str = Field("", max_length=2000)
    llm_model: str | None = None  # None -> server default
    tts_voice_ro: str | None = None
    tts_voice_en: str | None = None
    speech_rate: float = Field(1.0, ge=0.5, le=2.0)
    vad_sensitivity: Literal["low", "medium", "high"] = "medium"
    max_reply_chars: int = Field(400, ge=80, le=2000)
    history_turns: int = Field(6, ge=0, le=30)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        posix_tz(v)  # raises if unknown
        return v


DEVICE_EDITABLE = {"language", "volume", "brightness", "theme", "time_24h"}


def device_view(s: DeviceSettings) -> dict[str, Any]:
    """Subset sent to the watch."""
    return {
        "language": s.language,
        "volume": s.volume,
        "brightness": s.brightness,
        "screen_timeout_s": s.screen_timeout_s,
        "time_24h": s.time_24h,
        "tz_posix": posix_tz(s.timezone),
        "theme": s.theme.model_dump(),
        "max_listen_s": s.max_listen_s,
    }


def merge(current: DeviceSettings, changes: dict[str, Any]) -> DeviceSettings:
    data = current.model_dump()
    for key, value in changes.items():
        if key == "theme" and isinstance(value, dict):
            theme = {**data["theme"], **value}
            preset = value.get("preset")
            # Choosing a preset without explicit colors applies the preset colors.
            if preset in THEME_PRESETS and not (set(value) - {"preset"}):
                theme.update(THEME_PRESETS[preset])
            data["theme"] = theme
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
