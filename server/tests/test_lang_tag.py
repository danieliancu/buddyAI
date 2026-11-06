"""Foreign passages in a reply: [[lang:xx]]...[[/lang]] becomes LangSwitch events, never text."""

from __future__ import annotations

import pytest

from app.pipeline.display_tag import DisplayTagFilter
from app.pipeline.lang_tag import LangSwitch, LanguageTagFilter, is_lang_tag, strip_tags, text_of


def run(deltas: list[str], reply: str = "ro") -> list:
    f = LanguageTagFilter(reply)
    items: list = []
    for d in deltas:
        items += f.feed(d)
    items += f.flush()
    merged: list = []  # adjacent text pieces from different deltas joined, as the chunker sees them
    for x in items:
        if isinstance(x, str) and merged and isinstance(merged[-1], str):
            merged[-1] += x
        else:
            merged.append(x)
    return merged


def test_no_tags_is_plain_text() -> None:
    assert run(["Mașina mea ", "e după colț."]) == ["Mașina mea e după colț."]


def test_passage_in_one_delta() -> None:
    out = run(["Se spune: [[lang:en]]My car is just around the corner.[[/lang]] Simplu."])
    assert out == ["Se spune: ", LangSwitch("en"), "My car is just around the corner.", LangSwitch(None), " Simplu."]


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7])
def test_tags_split_across_deltas(size: int) -> None:
    text = "Se spune: [[lang:en]]My car.[[/lang]] Gata."
    deltas = [text[i : i + size] for i in range(0, len(text), size)]
    assert run(deltas) == ["Se spune: ", LangSwitch("en"), "My car.", LangSwitch(None), " Gata."]


def test_several_languages_in_one_reply() -> None:
    out = run(
        ["На френски: [[lang:fr]]Ma voiture est au coin.[[/lang]] На унгарски: [[lang:hu]]Az autóm a sarkon van.[[/lang]]"],
        reply="bg",
    )
    assert out == [
        "На френски: ", LangSwitch("fr"), "Ma voiture est au coin.", LangSwitch(None),
        " На унгарски: ", LangSwitch("hu"), "Az autóm a sarkon van.", LangSwitch(None),
    ]


def test_unclosed_passage_closes_at_the_end() -> None:
    assert run(["[[lang:en]]My car."]) == [LangSwitch("en"), "My car.", LangSwitch(None)]


def test_new_opening_inside_a_passage_switches() -> None:
    out = run(["[[lang:fr]]Bonjour [[lang:hu]]Szia[[/lang]]"])
    assert out == [LangSwitch("fr"), "Bonjour ", LangSwitch("hu"), "Szia", LangSwitch(None)]


def test_unknown_code_is_dropped_and_text_stays_in_reply_language() -> None:
    assert run(["Ceva: [[lang:xx]]abc[[/lang]] gata"]) == ["Ceva: abc gata"]


def test_reply_language_tag_is_ignored() -> None:
    assert run(["[[lang:ro]]Bună[[/lang]] ziua"]) == ["Bună ziua"]


def test_stray_closing_tag_is_dropped() -> None:
    assert run(["Bună[[/lang]] ziua"]) == ["Bună ziua"]


def test_code_case_region_and_spaces() -> None:
    assert run(["[[ LANG : EN-gb ]]Hi[[/lang]]"]) == [LangSwitch("en"), "Hi", LangSwitch(None)]
    assert run(["[[lang:nb]]Hei[[/lang]]"], reply="en") == [LangSwitch("no"), "Hei", LangSwitch(None)]


def test_malformed_language_tag_keeps_its_words() -> None:
    assert run(["Spune [[lang:fr Bonjour]] tare"]) == ["Spune Bonjour tare"]


def test_other_tags_pass_through_for_display_filter() -> None:
    assert run(["Mareea e la [[17:39]] azi [[?]]"]) == ["Mareea e la [[17:39]] azi [[?]]"]


def test_single_brackets_and_long_text_are_not_held() -> None:
    assert run(["a [b] c [", "x"]) == ["a [b] c [x"]
    long = "[[lang" + "x" * 40
    assert run([long]) == [long]


def test_strip_tags_whole_text() -> None:
    assert strip_tags("Se spune [[lang:en]]car[[/lang]].") == "Se spune car."
    assert text_of([LangSwitch("en"), "a", LangSwitch(None), "b"]) == "ab"


def test_is_lang_tag() -> None:
    assert is_lang_tag("lang:fr") and is_lang_tag(" / lang ") and is_lang_tag("LANG : en")
    assert not is_lang_tag("17:39") and not is_lang_tag("?") and not is_lang_tag("language")


# --- DisplayTagFilter leaves language tags to LanguageTagFilter ---------------------------------


def display(deltas: list[str]) -> tuple[str, str | None, bool]:
    f = DisplayTagFilter()
    out = "".join(f.feed(d) for d in deltas) + f.flush()
    return out, f.value, f.asks


def test_display_filter_does_not_take_a_leading_language_tag_as_value() -> None:
    out, value, _ = display(["[[lang:fr]]Ma voiture[[/lang]] est là."])
    assert value is None
    assert out == "[[lang:fr]]Ma voiture[[/lang]] est là."


@pytest.mark.parametrize("size", [1, 3, 8])
def test_display_filter_keeps_inline_language_tags(size: int) -> None:
    text = "Se spune [[lang:en]]car[[/lang]], la [[17:39]]. [[?]]"
    out, value, asks = display([text[i : i + size] for i in range(0, len(text), size)])
    assert out.rstrip() == "Se spune [[lang:en]]car[[/lang]], la 17:39."  # (a split [[?]] may leave a space)
    assert value == "17:39" and asks


def test_display_value_then_passage() -> None:
    out, value, _ = display(["[[21°C]] ", "In French: [[lang:fr]]vingt et un degrés[[/lang]]."])
    assert value == "21°C"
    assert out == "In French: [[lang:fr]]vingt et un degrés[[/lang]]."
    f = LanguageTagFilter("en")
    assert text_of(f.feed(out) + f.flush()) == "In French: vingt et un degrés."
