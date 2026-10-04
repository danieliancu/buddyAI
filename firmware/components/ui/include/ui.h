/*
 * ola - LVGL 9 user interface (410x502 AMOLED)
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
    UI_ERR_SUBSCRIPTION,        /* subscription_required: no active ola Care */
    UI_ERR_LIMIT,               /* limit_reached: monthly allowance used up */
    UI_ERR_CONCURRENT,          /* busy_concurrent: other conversations of the account in progress */
    UI_ERR_ACCOUNT_INACTIVE,    /* account_inactive: owner account suspended/closed */
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
    /* User confirmed the factory reset screen (ui_show_reset_confirm). */
    void (*on_factory_reset)(void);
    /* Notes / reminders: open an item (server answers item_show) or delete it. */
    void (*on_item_open)(bool reminder, int number);
    void (*on_item_delete)(bool reminder, int number);
    /* Reminder completed (done = true) or opened again. */
    void (*on_item_done)(int number, bool done);
    /* The user closed the conversation screen (X / swipe). */
    void (*on_chat_closed)(void);
    /* note screen: open / close the note's edit mic; pin a note */
    void (*on_note_session)(bool open, int number);
    /* reminder screen: open / close the reminder's edit mic */
    void (*on_reminder_session)(bool open, int number);
    void (*on_item_pin)(int number, bool pinned);
} ui_callbacks_t;

esp_err_t ui_init(lv_display_t *disp, const ui_callbacks_t *cb);

void ui_apply_settings(const buddy_settings_t *s);
void ui_show_watchface(void);
void ui_set_conv_state(ui_conv_t st);
/* Conversation screen (opens when a turn starts): this turn's transcript
 * (partial or final, replaces the user bubble) and reply text as it streams. */
void ui_chat_user(const char *text);
void ui_chat_reply(const char *delta);
/* Short answer value ("21°C"): the reply bubble shows only this, in large type. */
void ui_chat_display(const char *text);
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
/* Factory reset: "Reset watch?" confirmation (auto-cancels) and the
 * "Resetting..." screen shown while NVS is erased. */
void ui_show_reset_confirm(void);
void ui_show_resetting(void);

/* Notes & reminders (PROTOCOL.md 3.3). Each takes the whole server message
 * as JSON: `items` snapshot, `item_show`, `reminder_fire` (wakes the screen
 * and keeps it on until closed or 60 s pass). */
void ui_items_set(const char *json);
void ui_item_show(const char *json);
void ui_reminder_alert(const char *json);
/* Short server notice (`notice` {level, text}, e.g. 80 % of the monthly AI usage). Kept until no
 * conversation is running, then shown for a few seconds - it never interrupts one. */
void ui_show_notice(const char *json);
/* Open the notes (false) or reminders (true) list (`items_open`). */
void ui_items_show_list(bool reminder);
/* Note edit mode (protocol events). state: ui_conv_t of the current sentence; text: the live
 * transcript (question = false) or the assistant's short question (question = true). */
void ui_note_session(bool open);
void ui_note_state(ui_conv_t state);
void ui_note_text(const char *text, bool question);
/* Every supported language (`languages` message), for the settings language picker. */
void ui_languages_set(const char *json);

/* Is the screen fully on (not dimmed / off)? */
bool ui_is_awake(void);
/* Wake the screen (PWR key, incoming reply). */
void ui_wake(void);
/* Keep the screen awake while a turn is active. */
bool ui_is_watchface(void);

/* Localized helper for other components (e.g. hints). */
const char *ui_text_connecting(void);

#ifdef __cplusplus
}
#endif
