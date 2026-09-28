/*
 * BuddyAI - persistent settings (NVS)
 *
 * Holds:
 *  - Wi-Fi credentials, configured server_url, last-known server, device token
 *  - the device-facing settings object of PROTOCOL.md section 5 + its version
 *
 * All getters return copies and are thread-safe.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"
#include "cJSON.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SETTINGS_SSID_MAX       33
#define SETTINGS_PASS_MAX       65
#define SETTINGS_URL_MAX        192
#define SETTINGS_TOKEN_MAX      128
#define SETTINGS_TZ_MAX         64
#define SETTINGS_LANG_MAX       8       /* "auto" or an ISO 639-1 code (up to 7 chars, [a-z-]) */
#define SETTINGS_QUICK_LANG_MAX 3       /* entries in quick_languages */
#define SETTINGS_LANG_LABEL_MAX 25      /* label, UTF-8, up to 24 bytes + NUL */

typedef struct {
    char     preset[16];
    uint32_t accent;        /* 0xRRGGBB */
    uint32_t background;
    uint32_t clock;
    uint32_t text;
} settings_theme_t;

/* One option of the quick-settings language control. */
typedef struct {
    char code[SETTINGS_LANG_MAX];         /* "auto" | "en" | "de" ... */
    char label[SETTINGS_LANG_LABEL_MAX];  /* "Auto" | "English" | "Deutsch" ... (renderable text) */
} settings_quick_lang_t;

/* Device-facing settings (PROTOCOL.md section 5). */
typedef struct {
    /* "auto" (default: the server detects the spoken language) or an ISO 639-1
     * code ("en", "ro", "de", ...). Sent in listen_start; also picks the date
     * language (see ui_i18n.c). All other UI text is English. */
    char             language[SETTINGS_LANG_MAX];
    /* Options offered by the quick-settings language control (from the
     * server; default Auto + English). */
    settings_quick_lang_t quick_languages[SETTINGS_QUICK_LANG_MAX];
    uint8_t          quick_language_count;
    uint8_t          volume;            /* 0..100 */
    uint8_t          brightness;        /* 0..100 */
    uint16_t         screen_timeout_s;
    bool             time_24h;
    char             tz_posix[SETTINGS_TZ_MAX];
    settings_theme_t theme;
    uint16_t         max_listen_s;
} buddy_settings_t;

/* Change notification: called after the device-facing settings changed
 * (from the server or locally). Runs in the caller's context - keep it short. */
typedef void (*settings_listener_t)(const buddy_settings_t *s, void *ctx);

esp_err_t settings_init(void);

/* ---- device-facing settings ---- */
void      settings_get(buddy_settings_t *out);
uint32_t  settings_get_version(void);
/* Apply a full settings object from the server (hello_ack / settings_update). */
esp_err_t settings_apply_server(const cJSON *settings, uint32_t version);
/* Apply a partial local change (same keys as section 5). Does not bump the
 * version - the server does that and answers with settings_update. */
esp_err_t settings_apply_local(const cJSON *changes);
/* Serialize to a new cJSON object (caller deletes). */
cJSON    *settings_to_json(const buddy_settings_t *s);
void      settings_add_listener(settings_listener_t cb, void *ctx);

/* Theme presets shared by UI and settings. Returns false if unknown. */
bool      settings_theme_preset(const char *name, settings_theme_t *out);
/* Name list terminated by NULL. */
const char *const *settings_theme_preset_names(void);

/* ---- provisioning / connection ---- */
bool      settings_get_wifi(char ssid[SETTINGS_SSID_MAX], char pass[SETTINGS_PASS_MAX]);
esp_err_t settings_set_wifi(const char *ssid, const char *pass);
bool      settings_get_server_url(char *out, size_t len);   /* configured (portal) */
esp_err_t settings_set_server_url(const char *url);         /* "" clears */
bool      settings_get_last_server(char *out, size_t len);  /* last successful */
esp_err_t settings_set_last_server(const char *url);
bool      settings_get_token(char *out, size_t len);
esp_err_t settings_set_token(const char *token);
esp_err_t settings_erase_token(void);

/* Factory reset: erase Wi-Fi credentials, server URLs, device token and the
 * device-facing settings (the whole "buddyai" NVS namespace). The caller
 * restarts the device afterwards; it then boots into the setup portal. */
esp_err_t settings_factory_reset(void);

/* Stable device id derived from the factory MAC, e.g. "buddy-a1b2c3d4e5f6". */
const char *settings_device_id(void);

/* Parse "#RRGGBB" into 0xRRGGBB; returns false on bad input. */
bool      settings_parse_color(const char *str, uint32_t *out);

#ifdef __cplusplus
}
#endif
