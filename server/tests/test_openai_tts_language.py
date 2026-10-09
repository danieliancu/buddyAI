from app.providers.tts.base import TTSRequest
from app.providers.tts.openai import OpenAITTS


def _instructions(language: str, style: str = "Warm.") -> str | None:
    tts = OpenAITTS("key", "https://api.example", "gpt-4o-mini-tts")
    body = tts.build_request("La 14:30.", TTSRequest(voice="marin", language=language, instructions=style))["json"]
    return body.get("instructions")


def test_names_the_turn_language_for_any_language():
    assert _instructions("ro").startswith("The text is in Romanian.")
    assert "read every number" in _instructions("de") and "German" in _instructions("de")
    assert _instructions("ro").endswith("Warm.")


def test_auto_or_unknown_keeps_only_the_style():
    assert _instructions("auto") == "Warm."
    assert _instructions("xx", style="") is None
