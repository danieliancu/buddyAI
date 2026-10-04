/*
 * ola - UI internals: fonts, icon glyphs, strings, shared state.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "lvgl.h"
#include "ui.h"

/* ---- fonts (components/ui/fonts, generated with lv_font_conv 1.5.2) ----
 * buddy_font_20 / 28 : Noto Sans Medium: Latin-1, Latin Extended-A/B,
 *                      Latin Extended Additional, Greek, Cyrillic, punctuation
 *                      U+2010-2027, € + FontAwesome 5 symbols below
 *                      (regenerate with tools/gen_fonts.ps1)
 * buddy_font_clock   : Montserrat SemiBold 112 px, digits, ':' '-' ' '
 * buddy_font_clock_md: Montserrat SemiBold 76 px, the same glyphs (watchface clock)
 * buddy_font_mic     : FontAwesome 5, 64 px, the mic only (watchface mic button)
 * buddy_font_code    : Montserrat SemiBold 64 px, digits, '-' ' '
 * buddy_font_icon    : FontAwesome 5, 80 px, a few large icons              */
extern lv_font_t buddy_font_20;
extern lv_font_t buddy_font_28;
extern lv_font_t buddy_font_28b;      /* Noto Sans Bold 28 px, text only (watchface greeting) */
extern lv_font_t buddy_font_clock;
extern lv_font_t buddy_font_clock_md;
extern lv_font_t buddy_font_mic;
/* Watchface wave artwork (ui_wave_img.c, from wave.png): RGB565 + alpha per pixel, row by row. */
extern const uint16_t g_wave_img_w, g_wave_img_h, g_wave_img_low_y;
extern const uint16_t g_wave_img_rgb565[];
extern const uint8_t  g_wave_img_alpha[];
extern const uint16_t g_wave_white_rgb565[];   /* the white theme's wave (wave-white.png), same size */
extern const uint8_t  g_wave_white_alpha[];
extern lv_font_t buddy_font_shortcut; /* FontAwesome 5, 34 px: pen, calendar, gear (watchface shortcuts) */
extern lv_font_t buddy_font_code;
extern lv_font_t buddy_font_icon;
extern lv_font_t buddy_font_set;       /* FontAwesome 5, 24 px: the SET_ICON_* glyphs (settings, reminder) */
extern lv_font_t buddy_font_set_lg;    /* FontAwesome 5, 40 px: calendar (reminder screen tile) */
extern lv_font_t buddy_font_big;       /* Noto Sans Medium 56 px: ASCII, £ ° µ € (short answers) */
/* buddy_font_20 / _28 / _big fall back to buddy_math_20 / _28 / _56 for maths and science
 * symbols (x² H₂O √ π ≤ ≠ ∞ ∑ ∫ → ℃ Ω ⅓...); see tools/gen_fonts.ps1. */

/* ---- FontAwesome 5 glyphs (UTF-8) present in the fonts above ---- */
#define ICON_WIFI           "\xEF\x87\xAB"  /* U+F1EB */
#define ICON_BATTERY_FULL   "\xEF\x89\x80"  /* U+F240 */
#define ICON_BATTERY_3      "\xEF\x89\x81"
#define ICON_BATTERY_2      "\xEF\x89\x82"
#define ICON_BATTERY_1      "\xEF\x89\x83"
#define ICON_BATTERY_EMPTY  "\xEF\x89\x84"  /* U+F244 */
#define ICON_CHARGE         "\xEF\x83\xA7"  /* U+F0E7 */
#define ICON_SETTINGS       "\xEF\x80\x93"  /* U+F013 */
#define ICON_VOLUME         "\xEF\x80\xA8"  /* U+F028 */
#define ICON_BRIGHTNESS     "\xEF\x86\x85"  /* U+F185 sun */
#define ICON_THEME          "\xEF\x87\xBC"  /* U+F1FC brush */
#define ICON_LANGUAGE       "\xEF\x82\xAC"  /* U+F0AC globe */
#define ICON_WARNING        "\xEF\x81\xB1"  /* U+F071 */
#define ICON_REFRESH        "\xEF\x80\xA1"  /* U+F021 */
#define ICON_OK             "\xEF\x80\x8C"  /* U+F00C */
#define ICON_CLOSE          "\xEF\x80\x8D"  /* U+F00D */
#define ICON_UNLINK         "\xEF\x84\xA7"  /* U+F127 */
#define ICON_LOCK           "\xEF\x80\xA3"  /* U+F023 */
#define ICON_MIC            "\xEF\x84\xB0"  /* U+F130 */
#define ICON_LEFT           "\xEF\x81\x93"  /* U+F053 */
#define ICON_RIGHT          "\xEF\x81\x94"  /* U+F054 */
#define ICON_PEN            "\xEF\x8C\x84"  /* U+F304 */
#define ICON_CALENDAR       "\xEF\x81\xB3"  /* U+F073 calendar-alt */

/* ---- settings screen icons, in buddy_font_set only ---- */
#define SET_ICON_PALETTE    "\xEF\x94\xBF"  /* U+F53F */
#define SET_ICON_POWER      "\xEF\x80\x91"  /* U+F011 */
#define SET_ICON_SUN        "\xEF\x86\x85"  /* U+F185 */
#define SET_ICON_VOLUME     "\xEF\x80\xA8"  /* U+F028 */
#define SET_ICON_GLOBE      "\xEF\x82\xAC"  /* U+F0AC */
#define SET_ICON_WIFI       "\xEF\x87\xAB"  /* U+F1EB */
#define SET_ICON_CHEVRON    "\xEF\x81\x94"  /* U+F054 */
#define SET_ICON_BELL       "\xEF\x83\xB3"  /* U+F0F3: reminder with an advance notice */
#define SET_ICON_PIN        "\xEF\x8F\x85"  /* U+F3C5 map-marker-alt: location */
#define SET_ICON_DOC        "\xEF\x85\x9C"  /* U+F15C file-alt: description */
#define SET_ICON_USERS      "\xEF\x83\x80"  /* U+F0C0: participants */
#define SET_ICON_CALENDAR   "\xEF\x81\xB3"  /* U+F073, in buddy_font_set_lg */
#define SET_ICON_TRASH      "\xEF\x87\xB8"  /* U+F1F8 */
#define SET_ICON_PIN_NOTE   "\xEF\x82\x8D"  /* U+F08D thumbtack: pinned note */

/* ---- strings ---- */
typedef enum {
    STR_SETTINGS,
    STR_VOLUME,
    STR_BRIGHTNESS,
    STR_LANGUAGE,
    STR_THEME,
    STR_WIFI_SETUP,
    STR_BACK,
    STR_RETRY,
    STR_CONNECTING,
    STR_OFFLINE,
    STR_NO_SERVER_HINT,
    STR_PAIR_TITLE,
    STR_PAIR_BODY,
    STR_WIFI_TITLE,
    STR_WIFI_BODY,
    STR_ERR_NO_WIFI_T,
    STR_ERR_NO_WIFI_B,
    STR_ERR_SERVER_T,
    STR_ERR_SERVER_B,
    STR_ERR_NO_SERVER_T,
    STR_ERR_NO_SERVER_B,
    STR_ERR_UNPAIRED_T,
    STR_ERR_UNPAIRED_B,
    STR_ERR_AI_T,
    STR_ERR_AI_B,
    STR_ERR_PROTO_T,
    STR_ERR_PROTO_B,
    STR_ERR_BATT_T,
    STR_ERR_BATT_B,
    STR_ERR_BUSY_T,
    STR_ERR_BUSY_B,
    STR_ERR_SUB_T,
    STR_ERR_SUB_B,
    STR_ERR_LIMIT_T,
    STR_ERR_LIMIT_B,
    STR_ERR_INACTIVE_T,
    STR_ERR_INACTIVE_B,
    STR_RESET_T,
    STR_RESET_B,
    STR_RESET_BTN,
    STR_RESETTING,
    STR_OTA_T,
    STR_OTA_FAIL,
    STR_LISTENING,
    STR_THINKING,
    STR_TAP_TO_TALK,
    STR_NOTES,
    STR_REMINDERS,
    STR_NOTE,
    STR_REMINDER,
    STR_OVERDUE,
    STR_NO_NOTES,
    STR_NO_REMINDERS,
    STR_LOADING,
    STR_DELETE,
    STR_DELETE_CONFIRM,
    STR_OTHER,
    STR_SEARCH,
    STR_NO_MATCH,
    STR_TODAY,
    STR_TOMORROW,
    STR_COMPLETE,
    STR_CARE,
    STR_COMPLETED,
    STR_REOPEN,
    STR_SCREEN_TIMEOUT,
    STR_NOTICE_FMT,     /* "%s before": %s = "15 min" */
    STR_UNIT_S,
    STR_UNIT_MIN,
    STR_UNIT_H,
    STR_LOCATION,
    STR_DESCRIPTION,
    STR_NOTICE_LABEL,   /* reminder screen: the advance-alert row */
    STR_PARTICIPANTS,
    STR_DONE,
    STR_STARTS_IN_FMT,  /* "Starts in %s" (advance alert) */
    STR_NOTE_EMPTY,
    STR_NOTE_HELP,      /* note mic open: how to dictate / edit */
    STR_REMINDER_HELP,  /* reminder mic open: how to change it */
    STR_HELLO,          /* watchface greeting: "Hi there!" */
    STR_HELP_PROMPT,    /* watchface, under the greeting: "How can I help you today?" */
    STR__COUNT,
} ui_str_t;

/* One UI language (ui_i18n_tables.c, generated by tools/i18n/gen_i18n.py). */
typedef struct {
    const char *code;           /* ISO 639-1 */
    const char *wday[7];        /* 0 = Sunday, capitalized */
    const char *mon[12];        /* 0 = January, as used after a day number */
    const char *date_wd;        /* date line with weekday: {w} {d} {m} */
    const char *date;           /* without weekday: {d} {m} */
    const char *str[STR__COUNT];    /* NULL = use English */
} ui_lang_t;

typedef struct {
    const char *code;
    const char *lang;
} ui_lang_alias_t;

extern const ui_lang_t       ui_langs[];        /* [0] = English */
extern const size_t          ui_lang_count;
extern const ui_lang_alias_t ui_lang_aliases[];  /* ends with { NULL, NULL } */

const char *ui_str(ui_str_t id);
/* Localized date line, e.g. "Monday, 28 September" / "Montag, 28. September".
 * wday 0 = Sunday, mon 0 = January. Without weekday: "28 September". */
void        ui_format_date(char *buf, size_t len, int wday, int mday, int mon, bool with_weekday);
/* Weekday name in the date language, e.g. "Monday" / "Montag". wday 0 = Sunday. */
const char *ui_weekday_name(int wday);
/* Language setting ("auto" or a code). Returns true if the date language changed. */
bool        ui_i18n_set_language(const char *lang);
/* Detected language of the last reply (used for the date in "auto" mode).
 * Returns true if the date language changed. */
bool        ui_i18n_set_reply_language(const char *lang);

/* ---- shared theme state ---- */
typedef struct {
    lv_color_t accent;
    lv_color_t background;
    lv_color_t clock;
    lv_color_t text;
} ui_theme_t;

extern ui_theme_t      g_ui_theme;
extern buddy_settings_t g_ui_settings;
extern ui_callbacks_t  g_ui_cb;

/* ---- screens (ui_screens.c) ---- */
void ui_screens_init(void);
void ui_settings_open(void);
void ui_settings_refresh(void);
/* Settings header: battery text / colour and Wi-Fi icon colour, mirrored from the watchface. */
void ui_settings_status(const char *batt, lv_color_t batt_color, lv_color_t wifi_color, lv_opa_t wifi_opa);
/* The same for the reminder screen header (ui_items.c). */
void ui_items_status(const char *batt, lv_color_t batt_color);
void ui_msg_show(const char *icon, lv_color_t icon_color, const char *title, const char *body,
                 const char *code, const char *button, void (*button_cb)(void),
                 bool dismissable, uint32_t auto_close_ms);
void ui_msg_refresh_theme(void);
bool ui_msg_is_active(void);           /* pairing / Wi-Fi setup / error / OTA screen on display */
void ui_go_watchface(void);

/* ---- core (ui.c) ---- */
lv_obj_t *ui_watch_screen(void);
lv_style_t *ui_style_screen(void);
lv_style_t *ui_style_accent_bg(void);       /* accent fill + readable text colour on it */
/* Text / icon colour readable on `bg`: near-black on light fills (e.g. the white "mono" accent), else white. */
lv_color_t  ui_on_color(lv_color_t bg);
/* The X that closes a screen: the same place, size and colour on every screen (white, top right, 72 px
 * plus a wide touch margin). Floating: outside layouts and scrolling. Bring it to the front once the
 * screen is built (lv_obj_move_foreground), so nothing covers its touch area. */
lv_obj_t   *ui_add_close_x(lv_obj_t *scr, lv_event_cb_t cb);
lv_style_t *ui_style_accent_border(void);  /* border + arc in the accent color */
void      ui_load_screen(lv_obj_t *scr, bool to_left);
void      ui_note_activity(void);
bool      ui_consume_wake_tap(void);
bool      ui_screen_awake(void);        /* false while dimmed or off */
void      ui_hold_awake(bool hold);     /* keep the screen on (reminder alert) */

/* ---- language picker (ui_lang.c); caller holds the lock ---- */
void        ui_lang_init(void);
void        ui_lang_open(void);
const char *ui_lang_label(const char *code);

/* ---- conversation screen (ui_chat.c); caller holds the lock ---- */
void ui_chat_init(void);
void ui_chat_set_state(ui_conv_t st);
void ui_chat_anim(int level, uint32_t phase);

void ui_chat_refresh_theme(void);
void ui_chat_refresh_title(void);
bool ui_chat_is_active(void);

/* ---- notes & reminders (ui_items.c) ---- */
void ui_items_init(void);
void ui_items_open(bool reminder);
void ui_items_refresh_theme(void);
/* Counters on the watchface shortcuts (0 = hidden). Caller holds the lock. */
void ui_shortcut_counts(int notes, int reminders);
