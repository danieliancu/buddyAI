"""Times are said in words in Romanian (the voices read "14:30" badly); the screen keeps the digits."""

from __future__ import annotations

import pytest

from app.pipeline.spoken import for_speech


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("Ai ședință la 14:30.", "Ai ședință la paisprezece și treizeci."),
        ("Mâine la 09:00 ai dentist.", "Mâine la nouă ai dentist."),
        ("La 12:05 sau la 2:15", "La douăsprezece și cinci sau la două și cincisprezece"),
        ("De la 9:30–10:00", "De la nouă și treizeci până la zece"),
        ("între 9:30 - 11:45", "între nouă și treizeci până la unsprezece și patruzeci și cinci"),
        ("la 22:25", "la douăzeci și două și douăzeci și cinci de minute"),
        ("la 21:20", "la douăzeci și unu și douăzeci"),
        ("la 0:15", "la zero și cincisprezece"),
    ],
)
def test_romanian_times_in_words(text: str, said: str) -> None:
    assert for_speech(text, "ro") == said


@pytest.mark.parametrize("text", ["Scorul a fost 2:1", "raportul 3:45:10", "Costă 10.30 lei", "ora 24:10", "1,5:30"])
def test_romanian_leaves_other_numbers(text: str) -> None:
    assert for_speech(text, "ro") == text


def test_other_languages_unchanged() -> None:
    assert for_speech("Meeting at 14:30", "en") == "Meeting at 14:30"
    assert for_speech("Termin um 14:30", "de") == "Termin um 14:30"
