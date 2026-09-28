/*
 * BuddyAI - LVGL 9 user interface (410x502 AMOLED)
 *
 * Screens: watchface (clock, date, mic button, battery / Wi-Fi), conversation
 * states on the watchface (listening / thinking / speaking + caption), quick
 * settings (swipe left/up), pairing code, Wi-Fi setup, error screens.
 *
 * Every public function is thread-safe (takes the LVGL port lock).
 * Callbacks in ui_callbacks_t run in the LVGL task - keep them short.
 */
#pragma once

#include <stdbool.h>
#include "esp_err.h"
#include "lvgl.h"
#include "cJSON.h"
#include "settings.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    UI_CONV_IDLE,
    UI_CONV_LISTENING,
    UI_CONV_THINKING,
    UI_CONV_SPEAKING,
} ui_conv_t;

typedef enum {
    UI_ERR_NO_WIFI,
    UI_ERR_SERVER_UNREACHABLE,
    UI_ERR_NO_SERVER,           /* nothing configured / discovered: manual setup */
    UI_ERR_UNPAIRED,
    UI_ERR_AI,
    UI_ERR_PROTOCOL,            /* protocol_unsupported: update required */
    UI_ERR_LOW_BATTERY,
    UI_ERR_BUSY,
} ui_error_t;

typedef enum {
    UI_LINK_NO_WIFI,
    UI_LINK_WIFI,               /* Wi-Fi up, no server session */
    UI_LINK_ONLINE,             /* session established */
} ui_link_t;

typedef struct {
    void (*on_mic_tap)(void);
    /* Local settings change (subset of PROTOCOL.md section 5). */
    void (*on_settings_change)(const cJSON *changes);
    /* User asked for the Wi-Fi / server provisioning portal. */
    void (*on_wifi_setup)(void);
    void (*on_retry)(void);
    /* Audio level 0..100 used by the listening / speaking animations. */
    int  (*get_audio_level)(void);
} ui_callbacks_t;

esp_err_t ui_init(lv_display_t *disp, const ui_callbacks_t *cb);

void ui_apply_settings(const buddy_settings_t *s);
void ui_show_watchface(void);
void ui_set_conv_state(ui_conv_t st);
void ui_caption_clear(void);
void ui_caption_set(const char *text);
void ui_caption_append(const char *delta);
/* Detected language of the current reply (stt_result / tts_start "language").
 * With language = "auto" the date follows it when a table exists. */
void ui_set_reply_language(const char *lang);
void ui_set_hint(const char *text);        /* small status line, NULL clears */
void ui_set_status(int battery_pct, bool charging, ui_link_t link);

void ui_show_pairing(const char *code);
void ui_show_wifi_setup(const char *ap_ssid);
void ui_show_error(ui_error_t err, const char *detail);
void ui_show_ota(int pct);                  /* -1 = failed */

/* Wake the screen (PWR key, incoming reply). */
void ui_wake(void);
/* Keep the screen awake while a turn is active. */
bool ui_is_watchface(void);

/* Localized helper for other components (e.g. hints). */
const char *ui_text_connecting(void);

#ifdef __cplusplus
}
#endif
