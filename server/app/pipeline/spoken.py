"""Text sent to TTS, made easier to say: clock times written in words for languages where the voices read
"14:30" badly. Only the spoken text changes; the screen keeps the digits.

Romanian: "14:30" -> "paisprezece și treizeci", "09:00" -> "nouă", "9:30–10:00" -> "nouă și treizeci până
la zece" (hours and minutes in the feminine forms used for the clock: "două", "douăsprezece", "douăzeci și
două").
"""

from __future__ import annotations

import re

# HH:MM (00-23 : 00-59), not part of a longer number, ratio or score.
_TIME = r"(?<![\d:.,])([01]?\d|2[0-3]):([0-5]\d)(?![\d:]|[.,]\d)"
_RANGE = re.compile(_TIME + r"\s*[–—-]\s*" + _TIME)
_SINGLE = re.compile(_TIME)

_RO_UNITS = ["zero", "unu", "două", "trei", "patru", "cinci", "șase", "șapte", "opt", "nouă"]
_RO_TEENS = ["zece", "unsprezece", "douăsprezece", "treisprezece", "paisprezece", "cincisprezece",
             "șaisprezece", "șaptesprezece", "optsprezece", "nouăsprezece"]
_RO_TENS = {2: "douăzeci", 3: "treizeci", 4: "patruzeci", 5: "cincizeci"}


def _ro_number(n: int) -> str:
    """0..59, feminine (as on the clock)."""
    if n < 10:
        return _RO_UNITS[n]
    if n < 20:
        return _RO_TEENS[n - 10]
    tens, unit = divmod(n, 10)
    return _RO_TENS[tens] + (f" și {_RO_UNITS[unit]}" if unit else "")


def _ro_time(h: str, m: str) -> str:
    hour, minute = int(h), int(m)
    if not minute:
        return _ro_number(hour)
    # 22:25 -> "douăzeci și două și douăzeci și cinci de minute": "de minute" keeps the two apart
    unit = " de minute" if hour > 20 and minute > 20 and minute % 10 else ""
    return f"{_ro_number(hour)} și {_ro_number(minute)}{unit}"


def _ro(text: str) -> str:
    text = _RANGE.sub(lambda x: f"{_ro_time(x[1], x[2])} până la {_ro_time(x[3], x[4])}", text)
    return _SINGLE.sub(lambda x: _ro_time(x[1], x[2]), text)


_BY_LANGUAGE = {"ro": _ro}


def for_speech(text: str, language: str | None) -> str:
    """`text` as the TTS should say it in `language` (unchanged where nothing is needed)."""
    fix = _BY_LANGUAGE.get((language or "").split("-")[0].lower())
    return fix(text) if fix else text
