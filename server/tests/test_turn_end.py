"""Does the transcript so far look unfinished (a thinking pause) or finished (answer now)?"""

import pytest

from app.pipeline.turn_end import looks_unfinished


@pytest.mark.parametrize("text", [
    "Pune-mi un memento pentru",
    "Pune-mi un memento pentru mâine și",
    "Vreau să știu dacă",
    "Adaugă lapte, pâine,",
    "Sună-l pe Ion ăăă",
    "Ce program are magazinul de",
    "Remind me to call the",
    "Add milk and",
    "What's the weather in London, um",
    "So I was thinking...",
    "Add to my list:",
])
def test_unfinished(text):
    assert looks_unfinished(text, ["ro"])


@pytest.mark.parametrize("text", [
    "Cât e ceasul?",
    "Pune un memento mâine la 5.",
    "Pune-o pe lista mea",           # "o" ("pune-o") never counts as unfinished in Romanian
    "Mulțumesc",
    "What time is it",
    "I think so",
    "Set a timer for 10 minutes!",
])
def test_finished(text):
    assert not looks_unfinished(text, ["ro"])


def test_without_diacritics_too():
    assert looks_unfinished("pune un memento si", ["ro"])
    assert looks_unfinished("pune un memento și", ["ro"])


def test_languages():
    assert looks_unfinished("Erinnere mich an den Termin und", ["de"])
    assert looks_unfinished("Ajoute du lait et", ["fr"])
    assert not looks_unfinished("Pune-o pe lista mea o", ["ro"])   # Spanish / Portuguese "o" is not Romanian
    assert looks_unfinished("Pune-o pe lista mea o", [None, None])  # no known language: every list
    assert looks_unfinished("call mom and", ["ro"])                  # English is always added


def test_nothing_heard_yet_is_not_unfinished():
    assert not looks_unfinished("", ["ro"])
    assert not looks_unfinished("   ", None)
