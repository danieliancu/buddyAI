"""What may be remembered: text cleaning, duplicate keys and the block-list (deterministic, no model).

Never kept, whatever the user or the model says: payment card numbers, bank account numbers (IBAN),
passwords / PINs / codes, government id numbers and secret keys. Special-category facts (health and the
like) are kept only when the user asked explicitly, marked "special", and never learned by inference.
"""

from __future__ import annotations

import hashlib
import re

from app.item_search import normalize

KINDS = ("preference", "profile", "person", "routine", "goal", "project", "other")
CONTENT_MAX = 300
KEY_MAX = 60

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_DIGITS = re.compile(r"(?:\d[ -]?){13,19}")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")
_SECRET_WORDS = re.compile(
    r"\b(password|passcode|passwd|pin|pin code|security code|cvv|cvc|one[- ]time code|otp|"
    r"parola|parolă|codul pin|cod pin|codul de securitate|"
    r"passwort|mot de passe|contraseña|contrasena|senha|wachtwoord)\b",
    re.IGNORECASE,
)
_KEYS = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|sk_(live|test)_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,})\b")
_GOV_ID = re.compile(
    r"\b(national insurance|ni number|passport number|social security|ssn|cnp|"
    r"driving licen[cs]e number|tax id|numar de pasaport|număr de pașaport)\b",
    re.IGNORECASE,
)
# Special-category hints (GDPR art. 9): health, religion, politics, sexuality, ethnicity, union, criminal.
_SPECIAL = re.compile(
    r"\b(allerg\w*|diabet\w*|cancer|asthma|astm\w*|epilep\w*|dementia|demen\w*|alzheimer\w*|parkinson\w*|"
    r"depress\w*|anxiety|medication|medicine|medicament\w*|pill\w*|pastil\w*|tablet\w*|insulin\w*|"
    r"blood pressure|tensiune|heart condition|surgery|operat\w* la|pregnan\w*|insarcinat\w*|hiv|disabilit\w*|"
    r"religio\w*|church|biserica|mosque|synagogue|vote[sd]? for|party member|gay|lesbian|bisexual|transgender|"
    r"trade union|sindicat|criminal record|convicted|cazier)\b",
    re.IGNORECASE,
)


class MemoryRefused(ValueError):
    """The text may not be stored (the reason is safe to tell the user)."""


def clean(text: str) -> str:
    """One line, no control characters, single spaces, at most CONTENT_MAX characters."""
    t = " ".join(_CONTROL.sub(" ", text or "").split())
    if len(t) > CONTENT_MAX:
        t = t[:CONTENT_MAX].rsplit(" ", 1)[0]
    return t


def clean_key(value: str | None) -> str | None:
    t = normalize(value or "").replace(" ", "_")[:KEY_MAX]
    return t or None


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0


def refusal(text: str) -> str | None:
    """Why this text can never be stored, or None."""
    for m in _DIGITS.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return "payment card numbers are never stored"
    if _IBAN.search(text.upper()) and re.search(r"\d{6,}", text.replace(" ", "")):
        return "bank account numbers are never stored"
    if _SECRET_WORDS.search(text):
        return "passwords, PINs and security codes are never stored"
    if _KEYS.search(text):
        return "secret keys are never stored"
    if _GOV_ID.search(text):
        return "identity document numbers are never stored"
    return None


def is_special(text: str) -> bool:
    return bool(_SPECIAL.search(text or ""))


def check(text: str) -> str:
    """The cleaned text, or MemoryRefused."""
    t = clean(text)
    if len(t) < 3:
        raise MemoryRefused("there is nothing to remember")
    why = refusal(t)
    if why:
        raise MemoryRefused(why)
    return t
