"""Is the answer a confirmation? Only a pure yes counts; edit modes need a delete verb too."""

import pytest

from app.confirm_words import classify_answer


@pytest.mark.parametrize(
    "text, strict, expected",
    [
        ("Da.", False, "yes"),
        ("da, te rog", False, "yes"),
        ("Yes, delete it", False, "yes"),
        ("sigur, șterge-o", False, "yes"),
        ("da", True, "unclear"),  # edit mode: a bare yes from a TV is not enough
        ("da, șterge", True, "yes"),
        ("yes, delete", True, "yes"),
        ("Nu.", False, "no"),
        ("nu, nu șterge", False, "no"),
        ("anulează", True, "no"),
        ("hmm", False, "unclear"),
        ("da nu", False, "unclear"),
        ("da, dar mută-l mâine", False, "other"),
        ("ok, mulțumesc, ce vreme e mâine", False, "other"),
        ("adaugă și pâine", True, "other"),
        ("", False, "unclear"),
    ],
)
def test_classify_answer(text: str, strict: bool, expected: str) -> None:
    assert classify_answer(text, ["ro"], strict=strict) == expected


def test_other_languages_and_english_fallback() -> None:
    assert classify_answer("ja, löschen", ["de"], strict=True) == "yes"
    assert classify_answer("oui", ["fr"]) == "yes"
    assert classify_answer("yes", ["xx"]) == "yes"  # unknown language: English always works
