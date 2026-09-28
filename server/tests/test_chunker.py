import json
from pathlib import Path

from app.pipeline.chunker import ChunkerConfig, SemanticSpeechChunker

CFG = ChunkerConfig.from_dict(json.loads((Path(__file__).parent.parent / "config/providers.openai.json").read_text("utf-8"))["chunker"])


def run(text: str, lang: str = "ro", step: int = 3) -> list[str]:
    """Feed text in small token-like pieces (no idle flushes), then finish."""
    c = SemanticSpeechChunker(lang, CFG)
    out: list[str] = []
    t = 0.0
    for i in range(0, len(text), step):
        out += c.feed(text[i : i + step], t)
        t += 10
    return out + c.finish()


def test_example_from_spec():
    out = run("Sigur, mâine ai trei întâlniri, prima este la ora nouă.")
    assert out == ["Sigur,", "mâine ai trei întâlniri,", "prima este la ora nouă."]


def test_does_not_split_abbreviations_en():
    out = run("I spoke to Dr. Smith and Mr. Brown about it. Then we left.", "en")
    assert out == ["I spoke to Dr. Smith and Mr. Brown about it.", "Then we left."]


def test_does_not_split_etc_and_ro_abbreviations():
    out = run("Ia mere, pere etc. și mergi pe str. Lipscani nr. 5 acum.")
    assert "etc." in out[1] and not any(x.endswith("str.") or x.endswith("nr.") for x in out)


def test_prices_times_decimals_stay_intact():
    out = run("It costs £19.99 today. The train leaves at 10:30 sharp. Pi is 3.14 roughly.", "en")
    joined = " | ".join(out)
    assert "£19.99" in joined and "10:30" in joined and "3.14" in joined
    assert not any(x.endswith("£19.") or x.endswith("10:") for x in out)


def test_romanian_decimal_comma():
    out = run("Temperatura va fi de 3,5 grade dimineața, apoi crește.")
    assert any("3,5 grade" in x for x in out)


def test_length_flush_without_punctuation():
    text = "acesta este un text foarte lung fără nicio punctuație care continuă mult și tot continuă până la capăt"
    c = SemanticSpeechChunker("ro", CFG)
    first = c.feed(text, 0)
    assert first and len(first[0]) <= CFG.hard_max_chars
    assert not first[0].endswith(" ")


def test_idle_flush_cuts_at_word_boundary():
    c = SemanticSpeechChunker("ro", CFG)
    assert c.feed("Mâine dimineață vei avea o întâlnire imp", 0) == []
    assert c.poll(100) == []  # not idle long enough
    out = c.poll(1000)
    assert out == ["Mâine dimineață vei avea o întâlnire"]
    assert c.pending == "imp"


def test_first_fragment_is_short_for_low_ttfa():
    out = run("Da, desigur. Iată răspunsul complet pe care l-ai cerut.")
    assert out[0] == "Da, desigur."


def test_markdown_is_removed():
    out = run("**Sigur!** Iată `codul` tău.")
    assert out == ["Sigur!", "Iată codul tău."]


def test_initials_not_split():
    out = run("Cartea a fost scrisă de J. R. R. Tolkien demult.", "ro")
    assert out == ["Cartea a fost scrisă de J. R. R. Tolkien demult."]
