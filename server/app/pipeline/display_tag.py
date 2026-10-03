"""Short on-screen answer: the model starts its reply with "[[21°C]]".

The tag is removed from the spoken/stored reply and its value is shown large on the watch
(`llm_display`), e.g. the spoken reply "In Chelmsford it's 21 degrees and windy." shows "21°C".
When the model forgets the tag and the question asked for a value (`asks_for_value`),
`guess_value` picks the number out of a short reply ("Four, Daniel." -> "4").
"""

from __future__ import annotations

import re
import unicodedata

from pylatexenc.latex2text import LatexNodes2Text

DISPLAY_RULE = (
    "Watch screen rule: when the user asks for a specific piece of information and one short value IS the "
    "answer (a calculation, how many, how much, what time, a temperature, a price, a distance, a score, a "
    "percentage), start your reply with that value in digits inside double square "
    "brackets, even if you say it in words, e.g. 'two plus two' -> [[4]] Four., weather -> [[21°C]], "
    "[[14:30]], [[£3.50]], [[2-1]]. For a formula or equation the brackets hold it written with Unicode "
    "symbols (never LaTeX), e.g. [[E = mc²]], [[√16 = 4]], [[H₂O]], [[a² + b² = c²]], [[x ≤ 5]], [[F = m·a]], "
    "while the spoken reply says it in words. At most 30 characters inside the brackets. Do NOT add it for jokes, "
    "stories, small talk, opinions, notes or reminders, confirmations, lists, names of roads, buses or "
    "products (M25, X30), anything the user already said, or numbers that are just part of the text."
)

# Questions that ask for a value: digits / arithmetic, or "how many / what time / cât costă ..." (no diacritics).
_VALUE_QUESTION = re.compile(
    r"\d\s*[-+*/x×÷]\s*\d|\b(plus|minus|times|divided|ori|impartit|"
    r"how (many|much|old|far|long|tall|big|hot|cold|fast)|what time|what('s| is) the (temperature|time|price|date)|"
    r"temperature|degrees|cost|price|percent|"
    r"cat|cata|cati|cate|ce ora|la ce ora|cand|grade|costa|pret|procent)\b"
)

GUESS_MAX_REPLY = 120  # only short replies are scanned for a number
_UNIT = r"(?:\s?(?:°\s?[CF]|°|%|km/h|mph|km|kg|cm|mm|ml|m|g|l))?"
# Not glued to letters: "M25", "A12", "X30" are names, not values.
_NUMBER_RE = re.compile(r"(?<![\w])[£$€]?\d+(?:[.,:]\d+)*" + _UNIT + r"(?![\w])")

_UNITS_EN = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
             "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS_EN = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
            "ninety": 90}
# Romanian without diacritics; "un" / "o" are left out (also articles).
_UNITS_RO = {"zero": 0, "unu": 1, "una": 1, "doi": 2, "doua": 2, "trei": 3, "patru": 4, "cinci": 5, "sase": 6,
             "sapte": 7, "opt": 8, "noua": 9, "zece": 10, "unsprezece": 11, "doisprezece": 12,
             "douasprezece": 12, "treisprezece": 13, "paisprezece": 14, "cincisprezece": 15,
             "saisprezece": 16, "saptesprezece": 17, "optsprezece": 18, "nouasprezece": 19}
_TENS_RO = {"douazeci": 20, "treizeci": 30, "patruzeci": 40, "cincizeci": 50, "saizeci": 60, "saptezeci": 70,
            "optzeci": 80, "nouazeci": 90}
_UNIT_WORDS = {**{w: i for i, w in enumerate(_UNITS_EN)}, **_UNITS_RO}
_TEN_WORDS = {**_TENS_EN, **_TENS_RO}


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def _word_numbers(text: str) -> list[int]:
    """Numbers written as English / Romanian words (0-99): 'four', 'twenty-one', 'douazeci si unu'."""
    words = re.findall(r"[a-z]+", _plain(text))
    found: list[int] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in _TEN_WORDS:
            value = _TEN_WORDS[w]
            j = i + 1
            if j < len(words) and words[j] == "si":  # douazeci si unu
                j += 1
            if j < len(words) and words[j] in _UNIT_WORDS and 0 < _UNIT_WORDS[words[j]] < 10:
                value += _UNIT_WORDS[words[j]]
                i = j
            found.append(value)
        elif w in _UNIT_WORDS:
            found.append(_UNIT_WORDS[w])
        i += 1
    return found


def echoes_question(value: str, question: str) -> bool:
    """The value is something the user said themselves ("M25", "bus 42"): not worth showing."""
    core = re.sub(r"[^\w]", "", _plain(value))
    return bool(core) and core in re.sub(r"[^\w]", "", _plain(question))


def asks_for_value(question: str) -> bool:
    """True if the user's question asks for a number / value (so a number in the reply is worth showing)."""
    return bool(_VALUE_QUESTION.search(_plain(question)))


def guess_value(reply: str) -> str | None:
    """The one number in a short reply ('Four, Daniel.' -> '4', 'It is 21 °C.' -> '21°C'), else None."""
    if not reply or len(reply) > GUESS_MAX_REPLY:
        return None
    digits = {m.group(0).replace(" ", "") for m in _NUMBER_RE.finditer(reply)}
    if digits:
        return digits.pop() if len(digits) == 1 else None
    words = set(_word_numbers(reply))
    return str(words.pop()) if len(words) == 1 else None


MAX_VALUE = 40  # longer values are dropped (the watch shows the reply text instead)

# --- formulas: LaTeX-ish text -> Unicode for the watch screen ---------------------------------

_SUP = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUB = str.maketrans("0123456789+-=()aeoxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ")
_LATEX = LatexNodes2Text()


def _script(m: re.Match, table: dict) -> str:
    body = m.group(1) if m.group(1) is not None else m.group(2)
    out = body.translate(table)
    # Only when every character has a superscript / subscript form; else leave it for pylatexenc.
    return out if all(c != o or c == " " for c, o in zip(body, out)) else m.group(0)


def to_display(value: str) -> str:
    """Formula text for the watch: x^2 -> x², H_2O -> H₂O, \\sqrt{16} -> √(16), \\leq -> ≤ (plain text unchanged)."""
    if not any(c in value for c in "\\^_"):
        return value
    s = re.sub(r"\^\{([^{}]*)\}|\^([0-9a-z+\-=()])", lambda m: _script(m, _SUP), value)
    s = re.sub(r"_\{([^{}]*)\}|_([0-9a-z+\-=()])", lambda m: _script(m, _SUB), s)
    if "\\" in s:
        s = re.sub(r"(\\[a-zA-Z]+) ", r"\1{} ", s)  # keep the space after a command ("a \\leq b")
        s = _LATEX.latex_to_text(s)
    return " ".join(s.split())


_MAX_WAIT = 60  # give up looking for "]]" after this many characters


class DisplayTagFilter:
    """Streaming filter: strips a leading [[value]] from the reply deltas and captures the value.

    A tag later in the reply ("high tide is at [[17:39]] today") keeps its value in the text without
    the brackets; the first value found this way is still shown large if the reply had none."""

    def __init__(self) -> None:
        self._buf = ""
        self._tail = ""  # held back after the start: a "[" or an unfinished "[[…" tag
        self._done = False
        self._trim = False
        self.value: str | None = None

    def feed(self, delta: str) -> str:
        """Text to pass on (possibly empty while the start of the reply is still undecided)."""
        if self._done:
            if self._trim:  # the spoken reply starts after the tag: drop the space in between
                delta = delta.lstrip()
                self._trim = not delta
            return self._inline(delta)
        self._buf += delta
        s = self._buf.lstrip()
        if not s or s == "[":
            return ""  # undecided yet
        if not s.startswith("[["):
            return self._release()
        end = s.find("]]")
        if end < 0:
            return "" if len(s) < _MAX_WAIT else self._release()
        self._done = True
        self._capture(s[2:end])
        rest = s[end + 2 :].lstrip()
        self._trim = not rest
        return self._inline(rest)

    def flush(self) -> str:
        """End of the reply: whatever was held back (an unterminated tag is passed through)."""
        if not self._done:
            self._done = True
            out, self._buf = self._buf, ""
            return out
        out, self._tail = self._tail, ""
        return out

    def _release(self) -> str:
        self._done = True
        out, self._buf = self._buf, ""
        return self._inline(out)

    def _capture(self, inner: str) -> None:
        if self.value is None:
            value = to_display(inner.strip())
            if value and len(value) <= MAX_VALUE:
                self.value = value

    def _inline(self, delta: str) -> str:
        """Brackets removed from tags inside the reply; an incomplete tag waits for the next delta."""
        text, self._tail = self._tail + delta, ""
        out: list[str] = []
        while True:
            start = text.find("[[")
            if start < 0:
                if text.endswith("["):
                    text, self._tail = text[:-1], "["
                out.append(text)
                break
            out.append(text[:start])
            end = text.find("]]", start + 2)
            if end < 0:
                if len(text) - start < _MAX_WAIT:
                    self._tail = text[start:]
                else:
                    out.append(text[start:])  # not a tag after all
                break
            inner = text[start + 2 : end]
            self._capture(inner)
            out.append(inner.strip())
            text = text[end + 2 :]
        return "".join(out)
