"""OpenAI TTS (/v1/audio/speech, response_format=pcm -> 24 kHz s16le mono), e.g. gpt-4o-mini-tts."""

from __future__ import annotations

from typing import Any

from app import languages
from app.providers.base import ProviderError
from app.providers.tts.base import TTSRequest
from app.providers.tts.http_stream import HTTPStreamingTTS


def language_instruction(code: str) -> str:
    """The API has no language parameter and guesses it from each (short) fragment, so a number or a name
    could come out in another language: name the turn's language explicitly."""
    lang = languages.get(code)
    if not lang:  # "auto" or unknown: let the model follow the text
        return ""
    return (
        f"The text is in {lang.name}. Speak it in {lang.name} with a native {lang.name} accent, and read every "
        f"number, time, date, amount and unit in {lang.name}."
    )


class OpenAITTS(HTTPStreamingTTS):
    name = "openai_tts"
    label = "OpenAI"

    def __init__(self, api_key: str, base_url: str, model: str, **kw: Any) -> None:
        super().__init__(**kw)
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model

    def check_configured(self) -> None:
        if not self.api_key:
            raise ProviderError("tts", "OpenAI API key is not configured")

    def build_request(self, text: str, request: TTSRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "voice": request.voice,
            "input": text,
            "response_format": "pcm",
            "speed": request.speech_rate,
        }
        instructions = " ".join(filter(None, [language_instruction(request.language), request.instructions]))
        if instructions:
            body["instructions"] = instructions
        return {
            "url": f"{self.base_url}/audio/speech",
            "headers": {"Authorization": f"Bearer {self.api_key}"},
            "json": body,
        }
