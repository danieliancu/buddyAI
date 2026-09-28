"""OpenAI TTS (/v1/audio/speech, response_format=pcm -> 24 kHz s16le mono), e.g. gpt-4o-mini-tts."""

from __future__ import annotations

from typing import Any

from app.providers.base import ProviderError
from app.providers.tts.base import TTSRequest
from app.providers.tts.http_stream import HTTPStreamingTTS


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
        if request.instructions:
            body["instructions"] = request.instructions
        return {
            "url": f"{self.base_url}/audio/speech",
            "headers": {"Authorization": f"Bearer {self.api_key}"},
            "json": body,
        }
