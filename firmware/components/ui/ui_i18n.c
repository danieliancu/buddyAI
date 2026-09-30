/*
 * ola - UI strings
 *
 * All on-screen UI text is English. Only the date line (weekday / month
 * names) is localized, for the languages that have a table below:
 *  - language setting = a code with a table -> that language;
 *  - language setting = "auto" -> the language of the last reply
 *    (stt_result.language / tts_start.language) if it has a table;
 *  - otherwise English.
 * Style: the weekday starts the line and is capitalized; month names follow
 * each language's running-text convention (capitalized in en/de, lowercase
 * elsewhere). Romanian uses the comma-below letters ș ț (U+0219 / U+021B).
 */
#include <stdio.h>
#include <string.h>
#include "ui_priv.h"

static const char *const s_en[STR__COUNT] = {
    [STR_SETTINGS]        = "Settings",
    [STR_VOLUME]          = "Volume",
    [STR_BRIGHTNESS]      = "Brightness",
    [STR_LANGUAGE]        = "Language",
    [STR_THEME]           = "Theme",
    [STR_WIFI_SETUP]      = "Wi-Fi setup",
    [STR_BACK]            = "Back",
    [STR_RETRY]           = "Retry",
    [STR_CONNECTING]      = "Connecting…",
    [STR_OFFLINE]         = "Offline",
    [STR_NO_SERVER_HINT]  = "No server",
    [STR_PAIR_TITLE]      = "Pair this watch",
    [STR_PAIR_BODY]       = "Open the ola web app, choose\n\"Add watch\" and enter this code:",
    [STR_WIFI_TITLE]      = "Wi-Fi setup",
    [STR_WIFI_BODY]       = "On your phone, join the Wi-Fi network",
    [STR_ERR_NO_WIFI_T]   = "No Wi-Fi",
    [STR_ERR_NO_WIFI_B]   = "Reconnecting automatically.\nTap setup to change the network.",
    [STR_ERR_SERVER_T]    = "Server unreachable",
    [STR_ERR_SERVER_B]    = "Retrying automatically.",
    [STR_ERR_NO_SERVER_T] = "No server found",
    [STR_ERR_NO_SERVER_B] = "Open Wi-Fi setup and enter the\nserver address (ws://…).",
    [STR_ERR_UNPAIRED_T]  = "Watch not paired",
    [STR_ERR_UNPAIRED_B]  = "Access was revoked.\nA new pairing code follows.",
    [STR_ERR_AI_T]        = "Something went wrong",
    [STR_ERR_AI_B]        = "The assistant could not answer.\nPlease try again.",
    [STR_ERR_PROTO_T]     = "Update required",
    [STR_ERR_PROTO_B]     = "This firmware is not compatible\nwith the server.",
    [STR_ERR_BATT_T]      = "Low battery",
    [STR_ERR_BATT_B]      = "Please charge the watch.",
    [STR_ERR_BUSY_T]      = "Server busy",
    [STR_ERR_BUSY_B]      = "Please try again in a moment.",
    [STR_ERR_SUB_T]       = "Subscription needed",
    [STR_ERR_SUB_B]       = "Open the ola app to renew\nola Care.",
    [STR_ERR_LIMIT_T]     = "Monthly usage reached",
    [STR_ERR_LIMIT_B]     = "Ola answers again when it resets.\nExtra usage: ola app.",
    [STR_ERR_INACTIVE_T]  = "Account inactive",
    [STR_ERR_INACTIVE_B]  = "Contact ola support.",
    [STR_RESET_T]         = "Reset watch?",
    [STR_RESET_B]         = "Tap Reset to confirm.\nErases Wi-Fi, server and pairing.\nCancels in 10 s.",
    [STR_RESET_BTN]       = "Reset",
    [STR_RESETTING]       = "Resetting…",
    [STR_OTA_T]           = "Updating firmware",
    [STR_OTA_FAIL]        = "Update failed",
    [STR_LISTENING]       = "Listening…",
    [STR_THINKING]        = "Thinking…",
    [STR_TAP_TO_TALK]     = "Tap to talk",
    [STR_NOTES]           = "Notes",
    [STR_REMINDERS]       = "Reminders",
    [STR_NOTE]            = "Note",
    [STR_REMINDER]        = "Reminder",
    [STR_OVERDUE]         = "Overdue",
    [STR_NO_NOTES]        = "No notes yet.\nAsk Ola: \"note that…\"",
    [STR_NO_REMINDERS]    = "No reminders yet.\nAsk Ola:\n\"remind me at 9 to…\"",
    [STR_LOADING]         = "Loading…",
    [STR_DELETE]          = "Delete",
    [STR_DELETE_CONFIRM]  = "Tap again to delete",
    [STR_OTHER]           = "Other",
    [STR_SEARCH]          = "Search",
    [STR_NO_MATCH]        = "No language found",
    [STR_TODAY]           = "Today",
    [STR_TOMORROW]        = "Tomorrow",
    [STR_COMPLETE]        = "Complete",
    [STR_CARE]            = "ola Care",
    [STR_COMPLETED]       = "Completed",
    [STR_REOPEN]          = "Reopen",
};

typedef struct {
    const char *code;
    const char *wday[7];        /* 0 = Sunday */
    const char *mon[12];        /* 0 = January */
    const char *after_wday;     /* between weekday and day number */
    const char *after_day;      /* between day number and month */
} date_lang_t;

static const date_lang_t s_date_langs[] = {
    { "en",
      { "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday" },
      { "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December" },
      ", ", " " },                                  /* Monday, 28 September */
    { "ro",
      { "Duminică", "Luni", "Marți", "Miercuri", "Joi", "Vineri", "Sâmbătă" },
      { "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
        "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie" },
      ", ", " " },                                  /* Luni, 28 septembrie */
    { "de",
      { "Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag" },
      { "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember" },
      ", ", ". " },                                 /* Montag, 28. September */
    { "fr",
      { "Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi" },
      { "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre" },
      " ", " " },                                   /* Lundi 28 septembre */
    { "es",
      { "Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado" },
      { "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre" },
      ", ", " de " },                               /* Lunes, 28 de septiembre */
    { "it",
      { "Domenica", "Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato" },
      { "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre" },
      " ", " " },                                   /* Lunedì 28 settembre */
    { "pt",
      { "Domingo", "Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado" },
      { "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro" },
      ", ", " de " },                               /* Segunda-feira, 28 de setembro */
    { "nl",
      { "Zondag", "Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag" },
      { "januari", "februari", "maart", "april", "mei", "juni",
        "juli", "augustus", "september", "oktober", "november", "december" },
      " ", " " },                                   /* Maandag 28 september */
    { "pl",
      { "Niedziela", "Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota" },
      { "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",   /* genitive */
        "lipca", "sierpnia", "września", "października", "listopada", "grudnia" },
      ", ", " " },                                  /* Poniedziałek, 28 września */
};

#define DATE_LANG_COUNT (sizeof(s_date_langs) / sizeof(s_date_langs[0]))

static char s_setting_lang[SETTINGS_LANG_MAX] = "auto";
static char s_reply_lang[SETTINGS_LANG_MAX];
static const date_lang_t *s_date = &s_date_langs[0];

/* Table for a code ("de", also "de-at" / "pt-br"), or NULL. */
static const date_lang_t *find_date_lang(const char *code)
{
    if (!code || !code[0]) {
        return NULL;
    }
    size_t n = strcspn(code, "-_");
    for (size_t i = 0; i < DATE_LANG_COUNT; i++) {
        if (strlen(s_date_langs[i].code) == n && strncmp(s_date_langs[i].code, code, n) == 0) {
            return &s_date_langs[i];
        }
    }
    return NULL;
}

/* Recompute the date language; returns true if it changed. */
static bool update_date_lang(void)
{
    const date_lang_t *d = NULL;
    if (strcmp(s_setting_lang, "auto") == 0) {
        d = find_date_lang(s_reply_lang);
    } else {
        d = find_date_lang(s_setting_lang);
    }
    if (!d) {
        d = &s_date_langs[0];
    }
    bool changed = d != s_date;
    s_date = d;
    return changed;
}

bool ui_i18n_set_language(const char *lang)
{
    strlcpy(s_setting_lang, (lang && lang[0]) ? lang : "auto", sizeof(s_setting_lang));
    return update_date_lang();
}

bool ui_i18n_set_reply_language(const char *lang)
{
    if (!lang || !lang[0]) {
        return false;
    }
    strlcpy(s_reply_lang, lang, sizeof(s_reply_lang));
    return update_date_lang();
}

const char *ui_str(ui_str_t id)
{
    if (id >= STR__COUNT) {
        return "";
    }
    const char *s = s_en[id];
    return s ? s : "";
}

void ui_format_date(char *buf, size_t len, int wday, int mday, int mon, bool with_weekday)
{
    const date_lang_t *d = s_date;
    const char *month = (mon >= 0 && mon < 12) ? d->mon[mon] : "";
    if (with_weekday && wday >= 0 && wday < 7) {
        snprintf(buf, len, "%s%s%d%s%s", d->wday[wday], d->after_wday, mday, d->after_day, month);
    } else {
        snprintf(buf, len, "%d%s%s", mday, d->after_day, month);
    }
}
