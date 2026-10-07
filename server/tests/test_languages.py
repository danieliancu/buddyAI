import pytest

from app import languages
from app.device_settings import DeviceSettings, device_view, merge
from app.providers.router import ProviderRouter

WATCH_31 = languages.WATCH_UI_LANGUAGES


@pytest.fixture
def all_languages(monkeypatch):
    """Detection tests cover every language the engine knows (a profile with supported_languages = "all");
    the shipped OpenAI profile is limited to the 31 watch languages."""
    from app import config as config_mod

    real = config_mod.load_providers_config

    def with_all(*a, **kw):
        return {**real(*a, **kw), "supported_languages": "all"}

    monkeypatch.setattr(languages, "load_providers_config", with_all)


def test_registry_and_profile_support():
    reg = languages.registry()
    assert len(reg) >= 55
    assert {"en", "ro", "de", "fr", "es", "pl", "uk", "el", "zh", "ar", "cy"} <= set(reg)
    assert reg["ru"].captions and reg["el"].captions and reg["ro"].captions
    assert not reg["zh"].captions and not reg["ar"].captions and reg["ar"].rtl
    # The shipped OpenAI profile: exactly the 31 languages the watch screens are translated into.
    assert set(languages.supported_codes()) == set(WATCH_31)
    assert "ar" not in languages.supported_codes() and "af" not in languages.supported_codes()


@pytest.mark.parametrize(
    "text,code",
    [
        ("What will the weather be like tomorrow?", "en"),
        ("Ce vreme va fi mâine în București?", "ro"),
        ("Wie wird das Wetter morgen?", "de"),
        ("Quel temps fera-t-il demain ?", "fr"),
        ("¿Qué tiempo hará mañana?", "es"),
        ("Che tempo farà domani?", "it"),
        ("Jaka będzie jutro pogoda?", "pl"),
        ("Какая завтра будет погода?", "ru"),
        ("Яка сьогодні погода в Києві?", "uk"),
        ("Τι καιρό θα κάνει αύριο;", "el"),
        ("明天天气怎么样？", "zh"),
        ("Hur blir vädret i morgon?", "sv"),
    ],
)
def test_detect_short_phrases(text, code, all_languages):
    detected, confidence = languages.detect(text)
    assert detected == code, (text, detected, confidence)


def test_close_languages_follow_preference(all_languages):
    # No letters unique to Ukrainian: ambiguous with Russian, so the owner's language decides.
    assert languages.detect("Яка завтра буде погода?", prefer=["uk"])[0] == "uk"
    assert languages.detect("Какая завтра будет погода?", prefer=["uk"])[0] == "ru"  # clearly Russian: preference ignored


def test_detect_unsure_returns_none():
    assert languages.detect("")[0] is None
    assert languages.detect("ok")[0] is None or languages.detect("ok")[1] >= languages.MIN_DETECT_CONFIDENCE


def test_settings_language_validation_and_legacy_voices():
    assert DeviceSettings().language == "auto"
    assert DeviceSettings(language="de").language == "de"
    with pytest.raises(ValueError):
        DeviceSettings(language="xx")
    with pytest.raises(ValueError):
        DeviceSettings(preferred_language="auto")
    legacy = DeviceSettings.model_validate({"tts_voice_ro": "ro-RO-EmilNeural", "tts_voice_en": "Ethan"})
    assert legacy.tts_voice_overrides == {"ro": "ro-RO-EmilNeural", "en": "Ethan"}


def test_quick_languages_labels_are_renderable():
    view = device_view(merge(DeviceSettings(), {"preferred_language": "uk"}))
    assert [q["code"] for q in view["quick_languages"]] == ["auto", "en", "uk"]
    assert view["quick_languages"][2]["label"] == "Українська"  # Cyrillic is in the watch font
    view = device_view(merge(DeviceSettings(), {"preferred_language": "ja"}))
    assert view["quick_languages"][2]["label"] == "Japanese"  # no Japanese glyphs on the watch


def test_router_tts_any_language_and_overrides():
    r = ProviderRouter()
    s = DeviceSettings(tts_voice="cedar", tts_voice_overrides={"de": "nova"})
    assert r.tts("fi", s).voice == "cedar"
    assert r.tts("de", s).voice == "nova"
    assert r.tts("sw", DeviceSettings()).voice == r.tts_options()["default_voice"]
