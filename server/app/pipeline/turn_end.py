"""Does what the user has said so far look unfinished? Used by the end-of-speech detector (vad.py) to wait
longer through a thinking pause ("set a reminder for... um... tomorrow at 5") while a finished sentence still
ends quickly.

Pure text logic on the live (partial) transcript: the sentence looks unfinished when it ends with a comma,
an ellipsis, a dash or a colon, or with a word that cannot end a sentence - a conjunction, preposition,
article or hesitation sound. A wrong "unfinished" only costs a slightly later reply, so the lists keep to
words that almost never end a request (no "o" in Romanian: "pune-o"; no "so" / "like" in English).
"""

from __future__ import annotations

import re

from app.item_search import normalize

# Without diacritics (normalize() folds them): "și" -> "si", "für" -> "fur", "ähm" -> "ahm".
_WORDS: dict[str, set[str]] = {
    "en": {"and", "or", "but", "because", "to", "the", "a", "an", "of", "for", "with", "at", "my", "your",
           "if", "than", "um", "uh", "erm", "er", "hmm", "uhm"},
    "ro": {"si", "sau", "dar", "ca", "pentru", "de", "la", "cu", "in", "pe", "din", "prin", "spre", "catre",
           "despre", "care", "sa", "un", "unui", "unei", "deci", "adica", "daca", "cand", "a", "aa", "aaa",
           "ee", "eee", "mm", "mmm", "hmm"},
    "de": {"und", "oder", "aber", "weil", "dass", "der", "die", "das", "den", "dem", "ein", "eine", "einen",
           "zu", "mit", "fur", "von", "im", "am", "ah", "ahm", "hm"},
    "fr": {"et", "ou", "mais", "parce", "que", "de", "du", "des", "le", "la", "les", "un", "une", "pour",
           "avec", "dans", "euh", "heu"},
    "es": {"y", "o", "pero", "porque", "que", "de", "del", "el", "la", "los", "las", "un", "una", "para",
           "con", "eh", "em"},
    "it": {"e", "o", "ma", "perche", "che", "di", "del", "il", "lo", "la", "un", "una", "per", "con", "ehm",
           "cioe"},
    "pt": {"e", "ou", "mas", "porque", "que", "de", "do", "da", "o", "a", "um", "uma", "para", "com", "hum"},
    "nl": {"en", "of", "maar", "omdat", "dat", "de", "het", "een", "van", "voor", "met", "eh", "ehm", "uh"},
    "pl": {"i", "a", "ale", "bo", "ze", "w", "z", "na", "do", "dla", "od", "ktory", "yyy", "eee"},
}
_ALL = set().union(*_WORDS.values())
# A sound drawn out while thinking: "aaaa", "eeeh", "mmmm", "hmmm", "ummm".
_HESITATION = re.compile(r"(a{2,}h*|e{2,}h*|m{2,}|h+m+|u+h*m+|u+h+|e+r+m*)")
_OPEN_END = re.compile(r"(,|;|:|-|–|—|…|\.\.\.)\s*$")


def looks_unfinished(text: str, langs: list[str | None] | None = None) -> bool:
    """True if `text` (the transcript so far) looks like the start of a longer sentence.

    `langs`: the languages that may be spoken (the turn's language, or the owner's languages on "auto");
    English is always added. No known language -> every list. Empty text -> False (nothing to go on)."""
    t = text.strip()
    if not t:
        return False
    if _OPEN_END.search(t):
        return True
    if t[-1] in "?!":
        return False
    words = normalize(t).split()
    if not words:
        return False
    last = words[-1]
    if _HESITATION.fullmatch(last):
        return True
    known = [_WORDS[code] for code in (langs or []) if code in _WORDS]
    vocab = set().union(_WORDS["en"], *known) if known else _ALL
    return last in vocab
