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


# Natural confirmations that used to be read as a new request (the model then asked a second time).
@pytest.mark.parametrize("text", [
    "Yes, I'm sure.", "Yes, of course.", "Yes, go on.", "Yes, that's right.", "Yes, delete the meeting",
    "Yes, delete the reminder", "Da, sunt sigur.", "Da, sigur că da.", "Da, bineînțeles.", "Da, vreau.",
    "Da, șterge-l.", "Da, șterge memento-ul.", "Da, ștergeți", "Da, sterge evenimentul din calendar",
    "Da, poți s-o ștergi.", "Da, șterge-o te rog frumos", "Daaa", "Yesss",
])
def test_natural_confirmations_are_yes(text: str) -> None:
    assert classify_answer(text, ["ro"]) == "yes"


def test_only_the_pending_kind_fits_a_yes() -> None:
    meeting = "delete Ședință cu Ștefan (Mon 10:00)"
    assert classify_answer("Da, șterge ședința", ["ro"], target_words=meeting, kind="reminder") == "yes"
    assert classify_answer("Yes, delete the meeting", ["en"], target_words=meeting, kind="reminder") == "yes"
    # another kind of item: not an answer to "delete the meeting"
    for text in ("da, șterge lista", "yes delete my list", "ok, delete the list", "da, șterge nota"):
        assert classify_answer(text, ["ro"], target_words=meeting, kind="reminder") != "yes", text
    assert classify_answer("Da, șterge memento-ul", ["ro"], target_words="delete Lapte (note)", kind="note") != "yes"


def test_the_item_named_in_the_question_may_be_repeated() -> None:
    facts = "delete Lista de cumpărături (note)"
    assert classify_answer("Da, șterge lista de cumpărături", ["ro"], target_words=facts) == "yes"
    assert classify_answer("Yes, delete the shopping list", ["en"], target_words="delete Shopping list") == "yes"
    # words that are neither a yes nor the item: never a yes
    assert classify_answer("Da, șterge lista de cumpărături și pâinea", ["ro"], target_words=facts) == "unclear"


@pytest.mark.parametrize("text, expected", [
    ("Yes, but not now", "unclear"),  # a yes with a no: never a yes, never a new request
    ("I'm not sure", "unclear"),
    ("Da, și adaugă pâine pe listă", "unclear"),  # a clear "da" with something we cannot place: ask again
    ("Yes and add bread", "unclear"),
    ("please add milk to my list", "other"),  # a weak "please" starts new requests
    ("șterge și nota de ieri", "other"),  # another deletion, not an answer
    ("Ce vreme e mâine?", "other"),
    ("Nuuu", "no"),
])
def test_never_a_yes_by_accident(text: str, expected: str) -> None:
    assert classify_answer(text, ["ro"]) == expected


def test_other_languages_and_english_fallback() -> None:
    assert classify_answer("ja, löschen", ["de"], strict=True) == "yes"
    assert classify_answer("oui", ["fr"]) == "yes"
    assert classify_answer("yes", ["xx"]) == "yes"  # unknown language: English always works
