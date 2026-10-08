"""Is the user's answer a confirmation? Deterministic word lists, no AI involved.

A deletion asked by voice runs only after a later utterance that is *purely* a confirmation ("da",
"yes, delete it", "sigur, șterge"): any other word makes it "other" (a new request, a correction, a
remark), never a yes. In the edit modes the microphone stays open, so a bare "da" from a TV or another
person is not enough there: `strict` also needs a delete / confirm verb ("da, șterge", "yes, delete").

Words are compared without diacritics (app.item_search.normalize). The tables cover the watch's
languages; any language falls back to English, which is always accepted too.

While a deletion waits, an answer that starts with a clear yes ("da, ...", "yes, ...") is never handed to the
model as a new request: either every other word is a known companion of a yes (an affirmation, a delete verb, a
filler, or a word naming the item asked about) and it is a yes, or it is "unclear" and the server asks again,
saying why. (Handed to the model, a yes made it call the delete tool again, which asked a second time.) A contrast
or question word ("da, dar mută-l", "ok, ce vreme e") still makes it a new request.
"""

from __future__ import annotations

import re

from typing import Literal

from app.item_search import normalize

Answer = Literal["yes", "no", "unclear", "other"]

_YES: dict[str, set[str]] = {
    "en": {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct", "right", "absolutely", "definitely", "please"},
    "ro": {"da", "sigur", "desigur", "ok", "bine", "corect", "exact", "confirm", "confirmat", "evident", "absolut", "normal"},
    "de": {"ja", "jawohl", "genau", "klar", "sicher", "okay", "richtig", "bitte"},
    "fr": {"oui", "ouais", "daccord", "exactement", "bien", "sur", "volontiers"},
    "es": {"si", "claro", "vale", "exacto", "correcto", "seguro", "dale"},
    "it": {"si", "certo", "esatto", "va", "bene", "sicuro", "giusto"},
    "pt": {"sim", "claro", "certo", "exato", "pode"},
    "nl": {"ja", "zeker", "prima", "goed", "klopt", "oke"},
    "pl": {"tak", "jasne", "pewnie", "dobrze", "oczywiscie"},
    "cs": {"ano", "jo", "jasne", "urcite", "dobre"},
    "sk": {"ano", "hej", "jasne", "urcite", "dobre"},
    "hu": {"igen", "persze", "jo", "rendben", "biztos"},
    "sv": {"ja", "javisst", "visst", "absolut", "okej"},
    "da": {"ja", "jo", "selvfolgelig", "okay"},
    "no": {"ja", "jo", "selvfolgelig", "greit"},
    "fi": {"kylla", "joo", "selva", "ok"},
    "el": {"nai", "malista", "vevaia", "entaxei"},
    "bg": {"da", "razbira", "dobre", "sigurno"},
    "ru": {"da", "konechno", "khorosho", "ugu"},
    "uk": {"tak", "zvisno", "dobre"},
    "tr": {"evet", "tamam", "olur", "tabii"},
    "hr": {"da", "naravno", "dobro", "moze"},
    "sr": {"da", "naravno", "dobro", "moze"},
    "bs": {"da", "naravno", "dobro", "moze"},
    "sl": {"ja", "seveda", "dobro", "lahko"},
    "lt": {"taip", "gerai", "zinoma"},
    "lv": {"ja", "labi", "protams"},
    "et": {"jah", "jaa", "muidugi", "hea"},
    "ca": {"si", "clar", "dacord", "exacte"},
    "gl": {"si", "claro", "vale"},
    "is": {"ja", "endilega", "allt", "lagi"},
}
_NO: dict[str, set[str]] = {
    "en": {"no", "nope", "nah", "cancel", "stop", "dont", "not", "wait", "never"},
    "ro": {"nu", "anuleaza", "stai", "lasa", "opreste", "renunt", "nicidecum"},
    "de": {"nein", "nicht", "abbrechen", "stopp", "warte"},
    "fr": {"non", "annule", "annuler", "attends", "pas"},
    "es": {"no", "cancela", "cancelar", "espera"},
    "it": {"no", "annulla", "aspetta"},
    "pt": {"nao", "cancela", "cancelar", "espera"},
    "nl": {"nee", "niet", "annuleer", "wacht"},
    "pl": {"nie", "anuluj", "czekaj"},
    "cs": {"ne", "zrusit", "pockej"},
    "sk": {"nie", "zrusit", "pockaj"},
    "hu": {"nem", "megse", "varj"},
    "sv": {"nej", "avbryt", "vanta"},
    "da": {"nej", "annuller", "vent"},
    "no": {"nei", "avbryt", "vent"},
    "fi": {"ei", "peru", "odota"},
    "el": {"ochi", "akyro"},
    "bg": {"ne", "otkazhi"},
    "ru": {"net", "otmena", "stoy"},
    "uk": {"ni", "skasuvaty"},
    "tr": {"hayir", "iptal", "dur"},
    "hr": {"ne", "odustani"}, "sr": {"ne", "odustani"}, "bs": {"ne", "odustani"},
    "sl": {"ne", "preklici"}, "lt": {"ne", "atsaukti"}, "lv": {"ne", "atcelt"}, "et": {"ei", "tuhista"},
    "ca": {"no", "cancella"}, "gl": {"non", "cancela"}, "is": {"nei", "haetta"},
}
# Delete / confirm verbs: a strict yes needs one ("da, șterge"); in chat they also count as yes.
_CONFIRM_VERBS: dict[str, set[str]] = {
    "en": {"delete", "remove", "erase", "confirm", "confirmed", "go", "ahead", "do"},
    "ro": {"sterge", "stergeo", "sterg", "stergel", "stergele", "stergei", "elimina", "scoate", "confirm", "confirmat", "fa"},
    "de": {"loschen", "losch", "entfernen", "bestatigen", "mach"},
    "fr": {"supprime", "supprimer", "efface", "confirme", "vas", "y"},
    "es": {"borra", "borralo", "borrala", "elimina", "confirmo", "hazlo"},
    "it": {"cancella", "elimina", "confermo", "fallo"},
    "pt": {"apaga", "apague", "elimina", "confirmo"},
    "nl": {"verwijder", "wis", "bevestig"},
    "pl": {"usun", "skasuj", "potwierdzam"},
    "cs": {"smaz", "smazat", "potvrzuji"}, "sk": {"zmaz", "vymaz", "potvrdzujem"},
    "hu": {"torold", "torles", "megerositem"}, "sv": {"radera", "ta", "bort", "bekrafta"},
    "da": {"slet", "bekraeft"}, "no": {"slett", "bekreft"}, "fi": {"poista", "vahvistan"},
    "el": {"diagrapse", "svise"}, "bg": {"iztrii", "potvarzhdavam"}, "ru": {"udali", "udalyay", "podtverzhdayu"},
    "uk": {"vydaly", "pidtverdzhuyu"}, "tr": {"sil", "onayliyorum"},
    "hr": {"obrisi", "izbrisi", "potvrdi"}, "sr": {"obrisi", "izbrisi", "potvrdi"}, "bs": {"obrisi", "izbrisi", "potvrdi"},
    "sl": {"izbrisi", "potrdi"}, "lt": {"istrink", "patvirtinu"}, "lv": {"dzest", "apstiprinu"}, "et": {"kustuta", "kinnitan"},
    "ca": {"esborra", "confirmo"}, "gl": {"borra", "confirmo"}, "is": {"eyda", "stadfesti"},
}
# Words that may come with an answer without changing it ("da, te rog, șterge-o").
_FILLER = {
    "te", "rog", "va", "please", "it", "them", "that", "this", "one", "o", "il", "le", "pe", "ea", "el", "asta",
    "aia", "acum", "now", "just", "doar", "the", "linia", "line",
    "bitte", "es", "das", "sil", "vous", "plait", "por", "favor", "per", "lo", "la", "si", "mai", "all", "tot",
    "toate", "toti", "si", "and", "thanks", "multumesc", "mersi",
}
_HESITATION = {"hmm", "hm", "eh", "ah", "uh", "um", "poate", "maybe", "perhaps", "stiu", "know", "sure?", "oare"}
# Words that come with a yes without making it something else ("yes, I'm sure", "da, sigur că da", "da, poți s-o
# ștergi", "da, șterge-l"). normalize() splits "I'm", "that's", "s-o", "șterge-l" at the apostrophe / hyphen.
_AFFIRM_EXTRA: dict[str, set[str]] = {
    "en": {"i", "m", "am", "s", "sure", "of", "course", "go", "on", "ahead", "certainly", "indeed", "fine", "can", "you",
           "want", "to", "thats", "right", "really", "totally", "yes", "do", "so", "is", "please", "thank", "thanks"},
    "ro": {"sunt", "sigur", "sigura", "ca", "bineinteles", "vreau", "poti", "puteti", "s", "sa", "fa", "fao", "stergi",
           "stergeti", "sterg", "stearga", "l", "o", "i", "le", "ul", "lui", "frumos", "chiar", "deja", "tot", "asa",
           "e", "este", "da", "inainte"},
}
# Words naming the kind of item being deleted ("yes, delete the meeting", "da, șterge evenimentul din calendar") -
# only that kind's words: "da, șterge lista" while a meeting waits is about something else.
_KIND_WORDS: dict[str, set[str]] = {
    "reminder": {"reminder", "meeting", "event", "calendar", "appointment", "memento", "mementoul", "eveniment",
                 "evenimentul", "intalnire", "intalnirea", "sedinta", "programare", "programarea"},
    "note": {"note", "list", "nota", "notita", "lista", "listele"},
}
_GENERIC_ITEM_WORDS = {"item", "of", "from", "my", "din", "de", "la", "cu", "mea", "meu"}
# A yes followed by one of these is a new request, not an answer ("da, dar mută-l mâine", "ok, ce vreme e mâine").
_CONTRAST = {"but", "however", "instead", "except", "what", "when", "where", "who", "why", "how", "which", "dar",
             "insa", "ci", "totusi", "ce", "cum", "cand", "unde", "cine", "care", "decat", "aber", "mais", "pero", "ma"}
# Yes words strong enough to start an answer; weak ones ("please", "ok", "bine") also start new requests.
_WEAK_YES = {"please", "ok", "okay", "bine", "normal", "exact", "right", "correct", "bitte", "bien", "va", "bene",
             "goed", "prima", "dobre", "dobro", "hea", "jo", "pode", "certo", "fine", "volontiers", "olur"}


def _squash(w: str) -> str:
    """'daaa' -> 'da', 'yesss' -> 'yes' (stretched words in transcripts)."""
    return re.sub(r"(.)\1+", r"\1", w)


def _union(table: dict[str, set[str]], langs: list[str]) -> set[str]:
    out: set[str] = set(table.get("en", set()))
    for lang in langs:
        out |= table.get((lang or "")[:2], set())
    return out


def classify_answer(text: str, languages: list[str], strict: bool = False, target_words: str = "",
                    kind: str | None = None) -> Answer:
    """yes | no | unclear | other. yes and no only for an answer made of nothing else. `target_words`: how the
    pending deletion was described (its item's words may be repeated in a yes); `kind`: the kind of item it deletes
    (note | reminder) - only that kind's words fit a yes (None: either kind)."""
    words = normalize(text).split()
    if not words:
        return "unclear"
    yes, no, verbs = _union(_YES, languages), _union(_NO, languages), _union(_CONFIRM_VERBS, languages)
    known = yes | no | verbs
    words = [_squash(w) if w not in known and _squash(w) in known else w for w in words]  # "daaa" -> "da" only
    has_yes = any(w in yes for w in words)
    has_verb = any(w in verbs for w in words)
    has_no = any(w in no for w in words)
    kind_words = _KIND_WORDS.get(kind or "") if kind else set().union(*_KIND_WORDS.values())
    target = set(normalize(target_words).split()) | (kind_words or set()) | _GENERIC_ITEM_WORDS
    allowed_yes = yes | verbs | _FILLER | _union(_AFFIRM_EXTRA, languages) | target
    allowed_no = no | _FILLER | verbs  # "nu, nu șterge"
    if has_no and (has_yes or has_verb) and all(w in allowed_no | yes | _HESITATION for w in words):
        # "nu șterge" is a no; "da nu" is unclear
        return "no" if not has_yes else "unclear"
    if has_no and all(w in allowed_no for w in words):
        return "no"
    if has_no and has_yes:
        return "unclear"  # "yes, but not now", "I'm not sure": never a yes, never a new request
    if (has_yes or has_verb) and not has_no and len(words) <= 10 and all(w in allowed_yes for w in words):
        if strict and not has_verb:
            return "unclear"  # edit mode: "da" alone is not enough, "da, șterge" is
        return "yes"
    if all(w in _HESITATION | _FILLER for w in words):
        return "unclear"
    if words[0] in yes and words[0] not in _WEAK_YES and not has_no and not any(w in _CONTRAST for w in words):
        return "unclear"  # "da, ..." with words we cannot place: ask again (and say why), never a second question
    return "other"
