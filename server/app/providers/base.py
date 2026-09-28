"""Common provider types."""

from __future__ import annotations

from dataclasses import dataclass


class ProviderError(Exception):
    """Raised by providers; `stage` is stt | llm | tts."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.message = message


@dataclass
class UsageItem:
    kind: str  # stt | llm | tts
    provider: str
    model: str
    unit: str  # audio_second | input_token | output_token | character
    quantity: float
