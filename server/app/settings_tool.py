"""Voice control of the watch settings: volume, brightness, language and colours.

The assistant calls `watch_settings` ("a bit quieter", "brightness to maximum", "switch to Romanian",
"make it red"). Relative changes come as signed steps (volume_change / brightness_change); the result
is clamped to each setting's range. The new settings are saved here and pushed to the watch when the
turn ends (after the reply has been spoken).
"""

from __future__ import annotations

import json
from typing import Any

from app import languages
from app.db.repositories import SettingsRepo
from app.db.session import session_scope
from app.device_settings import HEX_COLOR, THEME_PRESETS, DeviceSettings

VOLUME_RANGE = (0, 100)
BRIGHTNESS_RANGE = (5, 100)

SETTINGS_TOOL: dict[str, Any] = {
    "name": "watch_settings",
    "description": "Change the watch's volume, screen brightness, language or colour theme. Pass only what "
    "changes. Use volume / brightness for an exact level (0-100, e.g. 'maximum' = 100, 'minimum' = 0) and "
    "volume_change / brightness_change for relative requests: +/-10 for 'a bit', +/-15 when no amount is "
    "given ('louder', 'dimmer'), +/-30 for 'much'. Returns the new values.",
    "parameters": {
        "type": "object",
        "properties": {
            "volume": {"type": "integer", "description": "Exact speaker volume, 0-100"},
            "volume_change": {"type": "integer", "description": "Relative volume step, e.g. -15 for 'quieter'"},
            "brightness": {"type": "integer", "description": "Exact screen brightness, 5-100"},
            "brightness_change": {"type": "integer", "description": "Relative brightness step, e.g. +15"},
            "language": {
                "type": "string",
                "description": "Language to speak: its name in any language ('Romanian', 'română') or ISO "
                "code ('ro'), or 'auto' to answer in whatever language the user speaks",
            },
            "theme": {"type": "string", "enum": list(THEME_PRESETS), "description": "Colour theme preset"},
            "accent_color": {
                "type": "string",
                "description": "Accent colour as #RRGGBB (convert colour names, e.g. red -> #FF3B30)",
            },
        },
    },
}

SETTINGS_RULE = (
    "You can change the watch's volume, brightness, language and colours with the watch_settings tool, "
    "also for vague requests ('a bit quieter', 'brighter', 'much louder'). After changing, confirm briefly "
    "with the new value, e.g. 'Volume 55.'"
)


def resolve_language(value: str) -> str | None:
    """'ro' / 'Romanian' / 'română' / 'auto' -> code, or None if unknown."""
    v = value.strip().casefold()
    if v in ("auto", "automatic", "automat"):
        return languages.AUTO
    if languages.get(v):
        return v
    for lang in languages.supported():
        if v in (lang.name.casefold(), lang.native_name.casefold()):
            return lang.code
    return None


def _level(current: int, exact: Any, change: Any, lo: int, hi: int) -> int | None:
    if exact is not None:
        value = int(exact)
    elif change is not None:
        value = current + int(change)
    else:
        return None
    return max(lo, min(hi, value))


def apply(device_id: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Run the tool. Returns (JSON result for the model, whether anything changed)."""
    with session_scope() as db:
        repo = SettingsRepo(db)
        current, _ = repo.get(device_id)
        changes: dict[str, Any] = {}
        volume = _level(current.volume, args.get("volume"), args.get("volume_change"), *VOLUME_RANGE)
        if volume is not None:
            changes["volume"] = volume
        brightness = _level(
            current.brightness, args.get("brightness"), args.get("brightness_change"), *BRIGHTNESS_RANGE
        )
        if brightness is not None:
            changes["brightness"] = brightness
        if args.get("language"):
            code = resolve_language(str(args["language"]))
            if code is None:
                return _err(f"unknown language {args['language']!r}"), False
            changes["language"] = code
            if code != languages.AUTO:
                changes["preferred_language"] = code
        theme: dict[str, Any] = {}
        if args.get("theme"):
            if args["theme"] not in THEME_PRESETS:
                return _err(f"unknown theme {args['theme']!r}; choose from {', '.join(THEME_PRESETS)}"), False
            theme = {"preset": args["theme"]}
        if args.get("accent_color"):
            color = str(args["accent_color"]).strip()
            if not HEX_COLOR.match(color):
                return _err("accent_color must be #RRGGBB"), False
            base = THEME_PRESETS[theme["preset"]] if theme else current.theme.model_dump()
            theme = {**{k: base[k] for k in ("background", "clock", "text")}, "accent": color, "preset": "custom"}
        if theme:
            changes["theme"] = theme
        if not changes:
            return _err("nothing to change"), False
        new, _ = repo.update(device_id, changes)
    return json.dumps({"ok": True, **_view(new)}, ensure_ascii=False), True


def _view(s: DeviceSettings) -> dict[str, Any]:
    lang = languages.get(s.language)
    return {
        "volume": s.volume,
        "brightness": s.brightness,
        "language": lang.name if lang else s.language,
        "theme": s.theme.preset,
        "accent_color": s.theme.accent,
    }


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})
