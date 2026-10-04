"""Server-written short texts for the note / reminder edit modes (shown on the watch, not spoken).

A deletion question must say exactly what will be deleted, so it is built here from the real data rather
than written by the model. Other languages fall back to English.
"""

from __future__ import annotations

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "delete_item": "Delete {what}? Say “yes, delete”.",
        "delete_lines": "Delete {what}? Say “yes, delete”.",
        "remove_fields": "Remove {what}? Say “yes, delete”.",
        "say_confirm": "To delete, say “yes, delete”.",
        "cancelled": "Cancelled. Nothing was deleted.",
        "changed": "It changed meanwhile. Nothing was deleted.",
        "gone": "It no longer exists. Nothing else was deleted.",
        "conflict": "It changed meanwhile. Say it again.",
        "other_item": "Close this one to work with another item.",
        "deleted_lines": "Deleted.",
        "error": "That didn't work. Nothing was changed.",
        "line": "line {n}",
        "or": "or",
        "the_note": "this note",
        "the_reminder": "this reminder",
        "location": "the place",
        "participants": "{names}",
        "end": "the end time",
        "notify": "the advance alert",
    },
    "ro": {
        "delete_item": "Șterg {what}? Spune „da, șterge”.",
        "delete_lines": "Șterg {what}? Spune „da, șterge”.",
        "remove_fields": "Scot {what}? Spune „da, șterge”.",
        "say_confirm": "Ca să șterg, spune „da, șterge”.",
        "cancelled": "Am anulat. Nu am șters nimic.",
        "changed": "S-a schimbat între timp. Nu am șters nimic.",
        "gone": "Nu mai există. Nu am șters altceva.",
        "conflict": "S-a schimbat între timp. Spune din nou.",
        "other_item": "Închide-l pe acesta ca să lucrezi cu altul.",
        "deleted_lines": "Am șters.",
        "error": "Nu a mers. Nu am schimbat nimic.",
        "line": "rândul {n}",
        "or": "sau",
        "the_note": "această notiță",
        "the_reminder": "această programare",
        "location": "locul",
        "participants": "{names}",
        "end": "ora de final",
        "notify": "alerta din timp",
    },
    "de": {
        "delete_item": "{what} löschen? Sag „ja, löschen“.",
        "delete_lines": "{what} löschen? Sag „ja, löschen“.",
        "remove_fields": "{what} entfernen? Sag „ja, löschen“.",
        "say_confirm": "Zum Löschen sag „ja, löschen“.",
        "cancelled": "Abgebrochen. Nichts gelöscht.",
        "changed": "Inzwischen geändert. Nichts gelöscht.",
        "gone": "Gibt es nicht mehr. Sonst nichts gelöscht.",
        "conflict": "Inzwischen geändert. Bitte noch einmal.",
        "other_item": "Schließe dies, um etwas anderes zu bearbeiten.",
        "deleted_lines": "Gelöscht.",
        "error": "Das ging nicht. Nichts geändert.",
        "line": "Zeile {n}", "or": "oder", "the_note": "diese Notiz", "the_reminder": "diese Erinnerung",
        "location": "den Ort", "participants": "{names}", "end": "die Endzeit", "notify": "die Vorab-Erinnerung",
    },
    "fr": {
        "delete_item": "Supprimer {what} ? Dis « oui, supprime ».",
        "delete_lines": "Supprimer {what} ? Dis « oui, supprime ».",
        "remove_fields": "Retirer {what} ? Dis « oui, supprime ».",
        "say_confirm": "Pour supprimer, dis « oui, supprime ».",
        "cancelled": "Annulé. Rien n'a été supprimé.",
        "changed": "Modifié entre-temps. Rien supprimé.",
        "gone": "N'existe plus. Rien d'autre supprimé.",
        "conflict": "Modifié entre-temps. Répète.",
        "other_item": "Ferme celui-ci pour en modifier un autre.",
        "deleted_lines": "Supprimé.",
        "error": "Ça n'a pas marché. Rien changé.",
        "line": "la ligne {n}", "or": "ou", "the_note": "cette note", "the_reminder": "ce rappel",
        "location": "le lieu", "participants": "{names}", "end": "l'heure de fin", "notify": "l'alerte anticipée",
    },
    "es": {
        "delete_item": "¿Borro {what}? Di «si, borra».",
        "delete_lines": "¿Borro {what}? Di «si, borra».",
        "remove_fields": "¿Quito {what}? Di «si, borra».",
        "say_confirm": "Para borrar, di «si, borra».",
        "cancelled": "Cancelado. No se borró nada.",
        "changed": "Cambió entretanto. No se borró nada.",
        "gone": "Ya no existe. No se borró nada más.",
        "conflict": "Cambió entretanto. Repítelo.",
        "other_item": "Cierra este para editar otro.",
        "deleted_lines": "Borrado.",
        "error": "No funcionó. No cambió nada.",
        "line": "la línea {n}", "or": "o", "the_note": "esta nota", "the_reminder": "este recordatorio",
        "location": "el lugar", "participants": "{names}", "end": "la hora de fin", "notify": "el aviso previo",
    },
    "it": {
        "delete_item": "Cancello {what}? Dì «sì, cancella».",
        "delete_lines": "Cancello {what}? Dì «sì, cancella».",
        "remove_fields": "Tolgo {what}? Dì «sì, cancella».",
        "say_confirm": "Per cancellare, dì «sì, cancella».",
        "cancelled": "Annullato. Non ho cancellato nulla.",
        "changed": "È cambiato nel frattempo. Nulla cancellato.",
        "gone": "Non esiste più. Nient'altro cancellato.",
        "conflict": "È cambiato nel frattempo. Ripeti.",
        "other_item": "Chiudi questo per modificarne un altro.",
        "deleted_lines": "Cancellato.",
        "error": "Non ha funzionato. Nulla cambiato.",
        "line": "la riga {n}", "or": "o", "the_note": "questa nota", "the_reminder": "questo promemoria",
        "location": "il luogo", "participants": "{names}", "end": "l'ora di fine", "notify": "l'avviso anticipato",
    },
}


def text(lang: str | None, key: str, **kw: str) -> str:
    table = TEXTS.get((lang or "")[:2], TEXTS["en"])
    return (table.get(key) or TEXTS["en"][key]).format(**kw)


def which_of(lang: str | None, options: list[str]) -> str:
    """'Line 1 «Milk» or line 10 «Whole milk»?' (the watch shows it; at most ~80 characters)."""
    joined = (", ".join(options[:-1]) + f" {text(lang, 'or')} " + options[-1]) if len(options) > 1 else options[0]
    out = joined[:1].upper() + joined[1:] + "?"
    return out if len(out) <= 80 else out[:79] + "?"
