"""Passages in another language inside a reply: [[lang:fr]]Ma voiture est au coin.[[/lang]]

The reply model marks text in a language other than the reply's ("how do you say it in French?"), so the
voice can read that passage in its own language instead of the reply's. The tags never reach the screen,
the history or the voice; the filter turns them into `LangSwitch` events in the reply's text stream.

Anything unexpected falls back to today's behaviour (the passage read in the reply language): an unknown
code is dropped, a tag for the reply's own language is dropped, an unclosed passage ends with the reply, a
stray closing tag is dropped, a new opening tag inside a passage simply switches to the new language.
Other [[...]] tags ([[21°C]], [[?]]) pass through untouched for DisplayTagFilter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app import languages

LANGUAGE_RULE = (
    "Foreign passages rule: when your reply contains words, a phrase or a sentence in a language other than the "
    "reply's language (for example the translation the user asked for, or how something sounds in another "
    "language), wrap only that passage like this: [[lang:fr]]Ma voiture est juste au coin.[[/lang]] - use the "
    "two-letter ISO 639-1 code of the passage's language. Never wrap text that is in the reply's language, and "
    "do not wrap single names or brands."
)

_MAX_WAIT = 24  # give up looking for "]]" after this many characters (not a tag after all)
_OPEN = re.compile(r"\[\[\s*lang\s*:\s*([A-Za-z]{2,3})(?:[-_][A-Za-z0-9]{1,8})?\s*\]\]", re.IGNORECASE)
_CLOSE = re.compile(r"\[\[\s*/\s*lang\s*\]\]", re.IGNORECASE)
_PREFIXES = ("[[lang", "[[/lang")
_MALFORMED = re.compile(r"^\s*/?\s*lang\s*:?\s*([A-Za-z]{2,3}\b)?", re.IGNORECASE)


@dataclass(frozen=True)
class LangSwitch:
    """In the reply's text stream: what follows is in `code` (None: back to the reply language)."""

    code: str | None


def is_lang_tag(inner: str) -> bool:
    """`inner` (the text between [[ and ]]) is a language tag: DisplayTagFilter leaves it alone."""
    s = inner.strip().lower().replace(" ", "")
    return s.startswith("lang:") or s == "/lang"


def _could_be_tag(s: str) -> bool:
    """`s` (starting with "[") may still turn into a language tag as more text arrives."""
    key = s.lower().replace(" ", "")
    return any(p.startswith(key) or key.startswith(p) for p in _PREFIXES)


class LanguageTagFilter:
    def __init__(self, reply_language: str) -> None:
        self.reply_language = reply_language
        self.current: str | None = None  # language of the open passage (None: the reply language)
        self._tail = ""

    def _code(self, raw: str) -> str | None:
        code = raw.lower()
        code = "no" if code == "nb" else code
        if code == self.reply_language or languages.get(code) is None:
            return None
        return code

    def feed(self, delta: str) -> list[str | LangSwitch]:
        text, self._tail = self._tail + delta, ""
        out: list[str | LangSwitch] = []
        while True:
            start = text.find("[")
            if start < 0:
                self._text(out, text)
                return out
            rest = text[start:]
            if not _could_be_tag(rest):
                self._text(out, text[: start + 1])
                text = text[start + 1 :]
                continue
            end = rest.find("]]")
            if end > _MAX_WAIT:
                end = -1  # "]]" much later belongs to something else
            if end < 0:
                if len(rest) < _MAX_WAIT:
                    self._text(out, text[:start])
                    self._tail = rest
                    return out
                self._text(out, text[: start + 1])  # never closed: plain text
                text = text[start + 1 :]
                continue
            tag = rest[: end + 2]
            self._text(out, text[:start])
            self._tag(out, tag)
            text = rest[end + 2 :]

    def flush(self) -> list[str | LangSwitch]:
        """End of the reply: what was held back; an open passage is closed."""
        out: list[str | LangSwitch] = []
        self._text(out, self._tail)
        self._tail = ""
        if self.current is not None:
            self.current = None
            out.append(LangSwitch(None))
        return out

    def _tag(self, out: list[str | LangSwitch], tag: str) -> None:
        if _CLOSE.fullmatch(tag):
            if self.current is not None:
                self.current = None
                out.append(LangSwitch(None))
            return
        m = _OPEN.fullmatch(tag)
        if not m:
            if not is_lang_tag(tag[2:-2]):
                self._text(out, tag)  # another [[...]] tag: DisplayTagFilter's business
            else:  # a malformed language tag ("[[lang:fr Bonjour]]"): its words stay, the tag goes
                self._text(out, _MALFORMED.sub("", tag[2:-2]).strip())
            return
        code = self._code(m.group(1))
        if code != self.current:
            self.current = code
            out.append(LangSwitch(code))

    @staticmethod
    def _text(out: list[str | LangSwitch], text: str) -> None:
        if not text:
            return
        if out and isinstance(out[-1], str):
            out[-1] += text
        else:
            out.append(text)


def text_of(items: list[str | LangSwitch]) -> str:
    return "".join(x for x in items if isinstance(x, str))


def strip_tags(text: str) -> str:
    """A whole reply without its language tags (e.g. a reply that was not streamed)."""
    f = LanguageTagFilter("")
    return text_of(f.feed(text) + f.flush())
