"""Voices for a foreign passage (router.tts_passage_routes), on both shipped profiles, for every language."""

from __future__ import annotations

import json

import pytest

from app import languages
from app.config import SERVER_DIR, Settings
from app.device_settings import DeviceSettings
from app.providers.router import ProviderRouter


def profile(name: str) -> dict:
    return json.loads((SERVER_DIR / "config" / f"providers.{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def router(tmp_path):
    def make(name: str, azure: bool = True) -> ProviderRouter:
        s = Settings(
            data_dir=tmp_path, mock_providers=False, ai_profile=name, openai_api_key="sk", dashscope_api_key="ds",
            azure_speech_key="az" if azure else "", azure_speech_region="westeurope",
        )
        return ProviderRouter(settings=s, config=profile(name))

    return make


def names(routes) -> list[tuple[str, str, str]]:
    return [(r.provider.name, r.voice, r.language) for r in routes]


def test_every_azure_locale_has_native_voices_of_both_genders() -> None:
    for lang in languages.registry().values():
        if not lang.azure_locale:
            continue
        assert lang.azure_voice_f and lang.azure_voice_m, lang.code
        assert lang.azure_voice_f.startswith(lang.azure_locale + "-") and lang.azure_voice_f.endswith("Neural")
        assert lang.azure_voice_m.startswith(lang.azure_locale + "-") and lang.azure_voice_m.endswith("Neural")
        assert lang.azure_voice_f != lang.azure_voice_m


@pytest.mark.parametrize("name", ["openai", "qwen"])
def test_every_profile_voice_has_a_gender(name: str) -> None:
    tts = profile(name)["tts"]
    for entry in [tts["default"], *tts.get("by_language", {}).values()]:
        assert set(entry["voice_genders"]) == set(entry["voices"])
        assert set(entry["voice_genders"].values()) <= {"f", "m"}


def test_openai_profile_same_voice_first_then_azure(router) -> None:
    r = router("openai")
    routes = r.tts_passage_routes("fr", DeviceSettings(), "ro")
    assert names(routes) == [("openai_tts", "marin", "fr"), ("azure_tts", "fr-FR-DeniseNeural", "fr")]
    assert routes[0].instructions == r.tts("ro", DeviceSettings()).instructions
    male = r.tts_passage_routes("hu", DeviceSettings(tts_voice="cedar"), "bg")
    assert names(male) == [("openai_tts", "cedar", "hu"), ("azure_tts", "hu-HU-TamasNeural", "hu")]


def test_openai_profile_without_azure_key(router) -> None:
    assert names(router("openai", azure=False).tts_passage_routes("en", DeviceSettings(), "ro")) == [
        ("openai_tts", "marin", "en")
    ]


def test_qwen_profile_romanian_reply_english_passage_goes_to_azure_same_gender(router) -> None:
    r = router("qwen")
    assert names(r.tts_passage_routes("en", DeviceSettings(), "ro")) == [("azure_tts", "en-GB-SoniaNeural", "en")]
    emil = DeviceSettings(tts_voice_overrides={"ro": "ro-RO-EmilNeural"})
    assert names(r.tts_passage_routes("en", emil, "ro")) == [("azure_tts", "en-GB-RyanNeural", "en")]


def test_qwen_profile_qwen_voice_keeps_its_voice_for_its_languages(router) -> None:
    r = router("qwen")
    assert names(r.tts_passage_routes("en", DeviceSettings(), "fr")) == [
        ("qwen_tts", "Cherry", "en"), ("azure_tts", "en-GB-SoniaNeural", "en")
    ]


def test_qwen_profile_romanian_passage_uses_the_profiles_romanian_voice(router) -> None:
    r = router("qwen")
    assert names(r.tts_passage_routes("ro", DeviceSettings(), "en")) == [("azure_tts", "ro-RO-AlinaNeural", "ro")]
    assert names(r.tts_passage_routes("ro", DeviceSettings(tts_voice="Ethan"), "en")) == [
        ("azure_tts", "ro-RO-EmilNeural", "ro")
    ]
    chosen = DeviceSettings(tts_voice="Ethan", tts_voice_overrides={"ro": "ro-RO-AlinaNeural"})
    assert names(r.tts_passage_routes("ro", chosen, "en")) == [("azure_tts", "ro-RO-AlinaNeural", "ro")]


def test_qwen_profile_languages_qwen_does_not_speak(router) -> None:
    r = router("qwen")
    assert names(r.tts_passage_routes("bg", DeviceSettings(), "en")) == [("azure_tts", "bg-BG-KalinaNeural", "bg")]
    assert names(r.tts_passage_routes("hu", DeviceSettings(), "fr")) == [("azure_tts", "hu-HU-NoemiNeural", "hu")]


def test_qwen_profile_without_azure_key(router) -> None:
    r = router("qwen", azure=False)
    assert names(r.tts_passage_routes("bg", DeviceSettings(), "en")) == []  # read as before
    assert names(r.tts_passage_routes("ro", DeviceSettings(), "en")) == []
    assert names(r.tts_passage_routes("de", DeviceSettings(), "en")) == [("qwen_tts", "Cherry", "de")]


@pytest.mark.parametrize("name", ["openai", "qwen"])
def test_every_language_has_a_voice_on_every_profile(router, name: str) -> None:
    """Each language of the registry can be read in its own language as a passage (with the Azure key set),
    whatever the reply language; the two without any voice anywhere are known."""
    r = router(name)
    reply_languages = ["en", "ro", "bg"]
    no_voice = []
    for code in languages.registry():
        for reply in reply_languages:
            if code == reply:
                continue
            routes = r.tts_passage_routes(code, DeviceSettings(), reply)
            if not routes:
                no_voice.append(code)
            for route in routes:
                assert route.language == code and route.voice
    if name == "openai":
        assert no_voice == []
    else:
        assert set(no_voice) == {"be", "mi"}  # no Azure locale and no Qwen language: read as before
