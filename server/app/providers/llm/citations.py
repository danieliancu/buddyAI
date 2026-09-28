"""Strip web-search citations from streamed LLM text before it is spoken.

Search-backed replies end sentences with markdown source links such as
"([bbc.co.uk](https://www.bbc.co.uk/weather?utm_source=openai))", even when the prompt asks for no
URLs. Links and bare URLs are removed; the text is streamed in pieces, so a possibly unfinished
link or URL at the end of the buffer is held back until the next piece decides it.
"""

from __future__ import annotations

import re

_LINK = re.compile(r"\s*\(?\[[^\]\n]*\]\([^)\s]*\)\)?")
_URL = re.compile(r"\s*\(?(?:https?://|www\.)\S+")
_EMPTY_PARENS = re.compile(r"\s*\(\s*\)")
MAX_HOLD = 300  # an opener without a closer this far on is ordinary text


def strip_citations(text: str) -> str:
    return _EMPTY_PARENS.sub("", _URL.sub("", _LINK.sub("", text)))


def _hold_from(buf: str) -> int:
    """Index from which the tail of `buf` may still become a link or URL (len(buf) = none)."""
    hold = len(buf)
    stack: list[int] = []  # start of each open group
    last_bracket: tuple[int, int] | None = None  # (start, end) of the last closed "[...]"
    for i, ch in enumerate(buf):
        if ch == "(" and last_bracket and last_bracket[1] == i - 1:
            stack.append(last_bracket[0])  # "[title](" -> the link starts at "["
        elif ch in "([":
            stack.append(i)
        elif ch in ")]" and stack:
            start = stack.pop()
            if ch == "]":
                last_bracket = (start, i)
                if i == len(buf) - 1:
                    hold = min(hold, start)  # "[title]" may be followed by "(url)"
    if stack:
        hold = min(hold, stack[0])
    # last word may be the start of a URL ("h", "htt", "https:", "www")
    word_start = max(buf.rfind(" "), buf.rfind("\n")) + 1
    word = buf[word_start:].lstrip("(")
    if word and ("https://".startswith(word) or "http://".startswith(word) or "www.".startswith(word)
                 or word.startswith(("http", "www."))):
        hold = min(hold, word_start)
    return hold


class CitationFilter:
    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> str:
        self._buf += text
        cut = _hold_from(self._buf)
        if len(self._buf) - cut > MAX_HOLD:
            cut = len(self._buf)
        # keep the whitespace before a held part with it (it is dropped if the part is a link)
        while cut > 0 and self._buf[cut - 1].isspace() and cut < len(self._buf):
            cut -= 1
        out, self._buf = self._buf[:cut], self._buf[cut:]
        return strip_citations(out)

    def flush(self) -> str:
        out, self._buf = self._buf, ""
        return strip_citations(out)
