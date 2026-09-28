/*
 * BuddyAI - UI strings
 *
 * All on-screen UI text is English. Only the date (weekday / month names)
 * follows the device language setting (en default, ro optional); Romanian
 * uses the correct comma-below letters ș ț (U+0219 / U+021B). The fonts keep
 * Romanian diacritics because reply captions may be Romanian.
 */
#include <string.h>
#include "ui_priv.h"

static bool s_ro = false;

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
    [STR_PAIR_BODY]       = "Open the BuddyAI web app, choose\n\"Add watch\" and enter this code:",
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
    [STR_OTA_T]           = "Updating firmware",
    [STR_OTA_FAIL]        = "Update failed",
    [STR_LISTENING]       = "Listening…",
    [STR_THINKING]        = "Thinking…",
    [STR_TAP_TO_TALK]     = "Tap to talk",
};

static const char *const s_wday_en[7] = {
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
};
static const char *const s_wday_ro[7] = {
    "Duminică", "Luni", "Marți", "Miercuri", "Joi", "Vineri", "Sâmbătă",
};
static const char *const s_mon_en[12] = {
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
};
static const char *const s_mon_ro[12] = {
    "Ianuarie", "Februarie", "Martie", "Aprilie", "Mai", "Iunie",
    "Iulie", "August", "Septembrie", "Octombrie", "Noiembrie", "Decembrie",
};

void ui_i18n_set_language(const char *lang)
{
    s_ro = (lang && strcmp(lang, "ro") == 0);   /* date names only */
}

bool ui_i18n_is_ro(void)
{
    return s_ro;
}

const char *ui_str(ui_str_t id)
{
    if (id >= STR__COUNT) {
        return "";
    }
    const char *s = s_en[id];
    return s ? s : "";
}

const char *ui_weekday(int wday)
{
    return (wday >= 0 && wday < 7) ? (s_ro ? s_wday_ro[wday] : s_wday_en[wday]) : "";
}

const char *ui_month(int mon)
{
    return (mon >= 0 && mon < 12) ? (s_ro ? s_mon_ro[mon] : s_mon_en[mon]) : "";
}
