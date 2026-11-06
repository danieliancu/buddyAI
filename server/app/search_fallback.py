"""When our own search (ddgs_brave) finds nothing: "I'll look a bit more and get back to you", then OpenAI hosted
search, in the same turn.

Our sources (Brave, DuckDuckGo, the pages they lead to) miss specific facts that a hosted search finds - a local
swimming gala's results, who took part in a competition. A longer deadline does not help there (measured: the
search stops on its own, out of sources, not out of time). So a question our search cannot answer goes once to the
hosted search; the user hears a short phrase first, because that adds several seconds. The hosted call is billed as
before (`web_search_call` + tokens) and only for these questions; when it finds nothing either, the model says so.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app import languages
from app.search import clean_answer

log = logging.getLogger(__name__)

# "I'll look a bit more and get back to you" in every language a profile declares; any other: English.
SEARCH_MORE: dict[str, str] = {
    "en": "I'll look a bit more and get back to you.",
    "ro": "Mai caut și revin.",
    "de": "Ich suche weiter und melde mich gleich.",
    "fr": "Je cherche encore et je reviens vers vous.",
    "es": "Sigo buscando y te digo enseguida.",
    "it": "Cerco ancora e ti faccio sapere.",
    "pt": "Vou procurar mais e já te digo.",
    "nl": "Ik zoek nog even verder en kom erop terug.",
    "ca": "Busco una mica més i et dic.",
    "gl": "Busco un pouco máis e dígoche.",
    "sv": "Jag letar lite till och återkommer.",
    "da": "Jeg leder lidt mere og vender tilbage.",
    "no": "Jeg leter litt til og kommer tilbake.",
    "is": "Ég leita aðeins betur og læt þig vita.",
    "fi": "Etsin vielä hetken ja palaan asiaan.",
    "et": "Otsin veel ja annan kohe teada.",
    "lv": "Vēl pameklēšu un tūlīt pateikšu.",
    "lt": "Dar paieškosiu ir tuoj pasakysiu.",
    "cy": "Mi chwilia i ychydig mwy a dod nôl atoch chi.",
    "pl": "Poszukam jeszcze i zaraz wrócę z odpowiedzią.",
    "cs": "Ještě se podívám a hned se ozvu.",
    "sk": "Ešte pohľadám a hneď sa ozvem.",
    "hu": "Még keresek egy kicsit, és visszatérek.",
    "sl": "Še malo poiščem in se oglasim.",
    "hr": "Još malo potražim i javim se.",
    "bs": "Još malo potražim i javim se.",
    "sr": "Још мало потражим и јавим се.",
    "el": "Ψάχνω λίγο ακόμα και επανέρχομαι.",
    "bg": "Ще потърся още малко и ще ти кажа.",
    "mk": "Ќе побарам уште малку и ќе ти кажам.",
    "uk": "Пошукаю ще трохи й повернуся.",
    # the Qwen profile's other languages
    "zh": "我再找找，马上回复你。",
    "ja": "もう少し調べて、すぐにお伝えします。",
    "ko": "조금 더 찾아보고 알려 드릴게요.",
    "ru": "Поищу ещё немного и вернусь.",
}


def search_more_phrase(language: str | None) -> str:
    code = (language or "").split("-")[0].lower()
    code = "no" if code == "nb" else code
    return SEARCH_MORE.get(code) or SEARCH_MORE[languages.DEFAULT_LANGUAGE]


SearchFn = Callable[..., Awaitable[tuple]]


def with_hosted_fallback(ours: SearchFn, hosted: SearchFn, on_fallback: Callable[[], Awaitable[None]],
                         skip_categories: frozenset[str] = frozenset()) -> SearchFn:
    """`ours` / `hosted`: (query, location, language, category=, local_query=) -> (answer, tokens in, tokens out,
    searches, complete, usage). When `ours` finds nothing: `on_fallback()` (the spoken phrase), then `hosted`. The
    result carries the usage of both. `skip_categories`: already answered by the hosted search (the hybrid)."""

    async def search(query: str, location: str, language: str, category: str = "other", local_query: str = "") -> tuple:
        out = await ours(query, location, language, category=category, local_query=local_query)
        if category in skip_categories or clean_answer(out[0] or "")[0]:
            return out
        await on_fallback()
        try:
            h = await hosted(query, location, language, category=category, local_query=local_query)
        except Exception as exc:  # noqa: BLE001 - the user still gets "couldn't find it"
            log.warning("hosted search fallback failed: %s", type(exc).__name__)
            return out
        found = bool(clean_answer(h[0] or "")[0])
        log.info("search fallback: own search found nothing, hosted search %s", "answered" if found else "found nothing")
        usage: list[Any] = [*(out[5] if len(out) > 5 and out[5] else []), *(h[5] if len(h) > 5 and h[5] else [])]
        return h[0], out[1] + h[1], out[2] + h[2], out[3] + h[3], h[4] if len(h) > 4 else True, usage

    return search


# "Let me check": said by the server when a web search has run CHECKING_DELAY_S and nothing was said yet (the model
# often calls the tool without the phrase). A quick search (cached answer, weather) is answered without it.
CHECKING_DELAY_S = 1.0
CHECKING: dict[str, str] = {
    "en": "Let me check.",
    "ro": "Stai să verific.",
    "de": "Moment, ich schaue nach.",
    "fr": "Je vérifie.",
    "es": "Déjame comprobarlo.",
    "it": "Controllo subito.",
    "pt": "Deixa-me verificar.",
    "nl": "Even kijken.",
    "ca": "Deixa'm comprovar-ho.",
    "gl": "Déixame comprobalo.",
    "sv": "Jag kollar.",
    "da": "Jeg tjekker lige.",
    "no": "Jeg sjekker.",
    "is": "Ég athuga það.",
    "fi": "Tarkistan.",
    "et": "Kohe vaatan.",
    "lv": "Tūlīt pārbaudīšu.",
    "lt": "Tuoj patikrinsiu.",
    "cy": "Gad i mi wirio.",
    "pl": "Już sprawdzam.",
    "cs": "Hned to ověřím.",
    "sk": "Hneď to overím.",
    "hu": "Megnézem.",
    "sl": "Takoj preverim.",
    "hr": "Odmah provjeravam.",
    "bs": "Odmah provjeravam.",
    "sr": "Одмах проверавам.",
    "el": "Μια στιγμή να ελέγξω.",
    "bg": "Момент да проверя.",
    "mk": "Момент да проверам.",
    "uk": "Зараз перевірю.",
    "zh": "我查一下。",
    "ja": "確認しますね。",
    "ko": "확인해 볼게요.",
    "ru": "Сейчас проверю.",
}


def checking_phrase(language: str | None) -> str:
    code = (language or "").split("-")[0].lower()
    code = "no" if code == "nb" else code
    return CHECKING.get(code) or CHECKING[languages.DEFAULT_LANGUAGE]
