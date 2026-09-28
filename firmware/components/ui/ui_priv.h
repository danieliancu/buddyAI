/*
 * BuddyAI - UI internals: fonts, icon glyphs, strings, shared state.
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
 * buddy_font_code    : Montserrat SemiBold 64 px, digits, '-' ' '
 * buddy_font_icon    : FontAwesome 5, 80 px, a few large icons              */
extern lv_font_t buddy_font_20;
extern lv_font_t buddy_font_28;
extern lv_font_t buddy_font_clock;
extern lv_font_t buddy_font_code;
extern lv_font_t buddy_font_icon;

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
    STR__COUNT,
} ui_str_t;

const char *ui_str(ui_str_t id);
/* Localized date line, e.g. "Monday, 28 September" / "Montag, 28. September".
 * wday 0 = Sunday, mon 0 = January. Without weekday: "28 September". */
void        ui_format_date(char *buf, size_t len, int wday, int mday, int mon, bool with_weekday);
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
void ui_msg_show(const char *icon, lv_color_t icon_color, const char *title, const char *body,
                 const char *code, const char *button, void (*button_cb)(void),
                 bool dismissable, uint32_t auto_close_ms);
void ui_msg_refresh_theme(void);
void ui_go_watchface(void);

/* ---- core (ui.c) ---- */
lv_obj_t *ui_watch_screen(void);
lv_style_t *ui_style_screen(void);
lv_style_t *ui_style_accent_bg(void);
void      ui_load_screen(lv_obj_t *scr, bool to_left);
void      ui_note_activity(void);
bool      ui_consume_wake_tap(void);
