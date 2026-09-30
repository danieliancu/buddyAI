"""Voice control of the watch settings (watch_settings tool)."""

from __future__ import annotations

import json
import secrets

import pytest

from app.db.repositories import DeviceRepo, SettingsRepo
from app.db.session import session_scope
from app.items import AssistantTools
from app.security import hash_device_token
from app.settings_tool import apply, resolve_language


def _device(**settings) -> str:
    device_id = f"dev-{secrets.token_hex(4)}"
    with session_scope() as db:
        DeviceRepo(db).pair(device_id, hash_device_token(secrets.token_hex(8)), "Watch", "t", "t", None)
        SettingsRepo(db).ensure(device_id)
        if settings:
            SettingsRepo(db).update(device_id, settings)
    return device_id


def _settings(device_id: str):
    with session_scope() as db:
        return SettingsRepo(db).get(device_id)[0]


def test_relative_and_exact_levels_are_clamped() -> None:
    dev = _device(volume=70, brightness=80)
    result, changed = apply(dev, {"volume_change": -15})  # "quieter"
    assert changed and json.loads(result)["volume"] == 55
    apply(dev, {"volume_change": 50})  # "much louder" past the top
    assert _settings(dev).volume == 100
    apply(dev, {"brightness": 0})  # "minimum" -> the lowest allowed
    assert _settings(dev).brightness == 5
    apply(dev, {"brightness_change": 10})
    assert _settings(dev).brightness == 15


@pytest.mark.parametrize(
    "value, code", [("ro", "ro"), ("Romanian", "ro"), ("română", "ro"), ("Deutsch", "de"), ("auto", "auto")]
)
def test_language_by_name_or_code(value: str, code: str) -> None:
    assert resolve_language(value) == code


def test_language_change_also_sets_the_preferred_language() -> None:
    dev = _device()
    apply(dev, {"language": "Romanian"})
    s = _settings(dev)
    assert s.language == "ro" and s.preferred_language == "ro"
    result, changed = apply(dev, {"language": "Klingon"})
    assert not changed and json.loads(result)["ok"] is False


def test_theme_and_accent_colour() -> None:
    dev = _device()
    apply(dev, {"theme": "ocean"})
    assert _settings(dev).theme.preset == "ocean" and _settings(dev).theme.accent == "#00C2D1"
    apply(dev, {"accent_color": "#ff3b30"})  # "make it red": keeps the ocean background
    t = _settings(dev).theme
    assert t.accent == "#FF3B30" and t.background == "#001A26" and t.preset == "custom"
    result, changed = apply(dev, {"accent_color": "red"})
    assert not changed and "RRGGBB" in json.loads(result)["error"]


def test_nothing_to_change() -> None:
    result, changed = apply(_device(), {})
    assert not changed and json.loads(result)["ok"] is False


def test_tool_reports_a_settings_change_for_watches_without_an_owner() -> None:
    dev = _device(volume=40)
    out = AssistantTools().execute(None, "Europe/London", "watch_settings", '{"volume_change": 10}', dev)
    assert out.settings_changed and _settings(dev).volume == 50
    blocked = AssistantTools().execute(None, "Europe/London", "item_list", '{"kind": "note"}', dev)
    assert json.loads(blocked.result)["ok"] is False  # notes need an account
