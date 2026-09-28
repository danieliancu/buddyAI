/*
 * BuddyAI - persistent settings (NVS)
 */
#include "settings.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "settings";

#define NVS_NS              "buddyai"
#define KEY_WIFI_SSID       "wifi_ssid"
#define KEY_WIFI_PASS       "wifi_pass"
#define KEY_SERVER_URL      "server_url"
#define KEY_LAST_SERVER     "last_server"
#define KEY_TOKEN           "dev_token"
#define KEY_SETTINGS_JSON   "settings"
#define KEY_SETTINGS_VER    "settings_ver"

#define MAX_LISTENERS       6

typedef struct {
    settings_listener_t cb;
    void *ctx;
} listener_t;

static SemaphoreHandle_t s_lock;
static buddy_settings_t  s_settings;
static uint32_t          s_version;
static listener_t        s_listeners[MAX_LISTENERS];
static char              s_device_id[32];

/* ------------------------------------------------------------------------- */
/* Theme presets                                                              */
/* ------------------------------------------------------------------------- */

typedef struct {
    const char      *name;
    settings_theme_t theme;
} preset_t;

static const preset_t s_presets[] = {
    { "midnight", { "midnight", 0x4F8CFF, 0x000000, 0xFFFFFF, 0xB0B8C8 } },
    { "ocean",    { "ocean",    0x2EC4B6, 0x001219, 0xE0FBFC, 0x94D2BD } },
    { "sunset",   { "sunset",   0xFF7A45, 0x120A06, 0xFFE8D6, 0xE0B8A0 } },
    { "forest",   { "forest",   0x52B788, 0x050F0A, 0xD8F3DC, 0x95B8A2 } },
    { "mono",     { "mono",     0xFFFFFF, 0x000000, 0xFFFFFF, 0x9A9A9A } },
};

static const char *const s_preset_names[] = { "midnight", "ocean", "sunset", "forest", "mono", NULL };

bool settings_theme_preset(const char *name, settings_theme_t *out)
{
    for (size_t i = 0; i < sizeof(s_presets) / sizeof(s_presets[0]); i++) {
        if (name && strcmp(name, s_presets[i].name) == 0) {
            *out = s_presets[i].theme;
            return true;
        }
    }
    return false;
}

const char *const *settings_theme_preset_names(void)
{
    return s_preset_names;
}

/* ------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* ------------------------------------------------------------------------- */

static void set_default_quick_languages(buddy_settings_t *s)
{
    memset(s->quick_languages, 0, sizeof(s->quick_languages));
    strcpy(s->quick_languages[0].code, "auto");
    strcpy(s->quick_languages[0].label, "Auto");
    strcpy(s->quick_languages[1].code, "en");
    strcpy(s->quick_languages[1].label, "English");
    s->quick_language_count = 2;
}

static void set_defaults(buddy_settings_t *s)
{
    memset(s, 0, sizeof(*s));
    strcpy(s->language, "auto");
    set_default_quick_languages(s);
    s->volume = 70;
    s->brightness = 80;
    s->screen_timeout_s = 15;
    s->time_24h = true;
    strcpy(s->tz_posix, "EET-2EEST,M3.5.0/3,M10.5.0/4");
    settings_theme_preset("midnight", &s->theme);
    s->max_listen_s = 15;
}

bool settings_parse_color(const char *str, uint32_t *out)
{
    if (!str || str[0] != '#' || strlen(str) != 7) {
        return false;
    }
    char *end = NULL;
    unsigned long v = strtoul(str + 1, &end, 16);
    if (!end || *end != '\0') {
        return false;
    }
    *out = (uint32_t)v & 0xFFFFFF;
    return true;
}

static int clamp_int(int v, int lo, int hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

/* "auto" or a language code: 2..7 chars of [a-z-] (ISO 639-1, maybe a region). */
static bool valid_language(const char *v)
{
    if (!v) {
        return false;
    }
    size_t n = strlen(v);
    if (n < 2 || n >= SETTINGS_LANG_MAX) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        if (!((v[i] >= 'a' && v[i] <= 'z') || v[i] == '-')) {
            return false;
        }
    }
    return true;   /* "auto" matches the same pattern */
}

/* Copy a UTF-8 string, truncating at a code point boundary. */
static void utf8_copy(char *dst, size_t len, const char *src)
{
    strlcpy(dst, src, len);
    size_t n = strlen(dst);
    if (n == strlen(src)) {
        return;
    }
    /* Truncated: drop a partial trailing sequence. */
    size_t i = n;
    while (i > 0 && (((unsigned char)dst[i - 1] & 0xC0) == 0x80)) {
        i--;
    }
    if (i > 0 && ((unsigned char)dst[i - 1] & 0x80)) {
        unsigned char lead = (unsigned char)dst[i - 1];
        size_t need = (lead & 0xE0) == 0xC0 ? 2 : (lead & 0xF0) == 0xE0 ? 3 : (lead & 0xF8) == 0xF0 ? 4 : 1;
        if (n - (i - 1) < need) {
            dst[i - 1] = '\0';
        }
    }
}

/* quick_languages: [{"code": "auto"|"en"|..., "label": "Auto"|"English"|...}], max 3.
 * Invalid entries are skipped; an empty result falls back to the default. */
static void merge_quick_languages(buddy_settings_t *s, const cJSON *arr)
{
    uint8_t n = 0;
    settings_quick_lang_t out[SETTINGS_QUICK_LANG_MAX];
    memset(out, 0, sizeof(out));
    const cJSON *e;
    cJSON_ArrayForEach(e, arr) {
        if (n >= SETTINGS_QUICK_LANG_MAX) {
            break;
        }
        const cJSON *code = cJSON_GetObjectItemCaseSensitive(e, "code");
        const cJSON *label = cJSON_GetObjectItemCaseSensitive(e, "label");
        if (!cJSON_IsString(code) || !valid_language(code->valuestring)) {
            continue;
        }
        strlcpy(out[n].code, code->valuestring, sizeof(out[n].code));
        if (cJSON_IsString(label) && label->valuestring[0]) {
            utf8_copy(out[n].label, sizeof(out[n].label), label->valuestring);
        } else {
            strlcpy(out[n].label, code->valuestring, sizeof(out[n].label));
        }
        n++;
    }
    if (n == 0) {
        set_default_quick_languages(s);
        return;
    }
    memcpy(s->quick_languages, out, sizeof(out));
    s->quick_language_count = n;
}

/* Merge the keys present in `j` into `s`. Unknown keys are ignored. */
static void merge_json(buddy_settings_t *s, const cJSON *j)
{
    const cJSON *it;

    it = cJSON_GetObjectItemCaseSensitive(j, "language");
    if (cJSON_IsString(it) && valid_language(it->valuestring)) {
        strlcpy(s->language, it->valuestring, sizeof(s->language));
    }
    it = cJSON_GetObjectItemCaseSensitive(j, "quick_languages");
    if (cJSON_IsArray(it)) {
        merge_quick_languages(s, it);
    }
    it = cJSON_GetObjectItemCaseSensitive(j, "volume");
    if (cJSON_IsNumber(it)) {
        s->volume = (uint8_t)clamp_int(it->valueint, 0, 100);
    }
    it = cJSON_GetObjectItemCaseSensitive(j, "brightness");
    if (cJSON_IsNumber(it)) {
        s->brightness = (uint8_t)clamp_int(it->valueint, 1, 100);
    }
    it = cJSON_GetObjectItemCaseSensitive(j, "screen_timeout_s");
    if (cJSON_IsNumber(it)) {
        s->screen_timeout_s = (uint16_t)clamp_int(it->valueint, 5, 3600);
    }
    it = cJSON_GetObjectItemCaseSensitive(j, "time_24h");
    if (cJSON_IsBool(it)) {
        s->time_24h = cJSON_IsTrue(it);
    }
    it = cJSON_GetObjectItemCaseSensitive(j, "tz_posix");
    if (cJSON_IsString(it) && it->valuestring[0]) {
        strlcpy(s->tz_posix, it->valuestring, sizeof(s->tz_posix));
    }
    it = cJSON_GetObjectItemCaseSensitive(j, "max_listen_s");
    if (cJSON_IsNumber(it)) {
        s->max_listen_s = (uint16_t)clamp_int(it->valueint, 3, 120);
    }
    const cJSON *theme = cJSON_GetObjectItemCaseSensitive(j, "theme");
    if (cJSON_IsObject(theme)) {
        it = cJSON_GetObjectItemCaseSensitive(theme, "preset");
        if (cJSON_IsString(it)) {
            /* A preset name alone expands to the preset colors; explicit colors win. */
            settings_theme_t p;
            if (settings_theme_preset(it->valuestring, &p)) {
                s->theme = p;
            }
            strlcpy(s->theme.preset, it->valuestring, sizeof(s->theme.preset));
        }
        uint32_t c;
        it = cJSON_GetObjectItemCaseSensitive(theme, "accent");
        if (cJSON_IsString(it) && settings_parse_color(it->valuestring, &c)) {
            s->theme.accent = c;
        }
        it = cJSON_GetObjectItemCaseSensitive(theme, "background");
        if (cJSON_IsString(it) && settings_parse_color(it->valuestring, &c)) {
            s->theme.background = c;
        }
        it = cJSON_GetObjectItemCaseSensitive(theme, "clock");
        if (cJSON_IsString(it) && settings_parse_color(it->valuestring, &c)) {
            s->theme.clock = c;
        }
        it = cJSON_GetObjectItemCaseSensitive(theme, "text");
        if (cJSON_IsString(it) && settings_parse_color(it->valuestring, &c)) {
            s->theme.text = c;
        }
    }
}

static void color_str(uint32_t c, char out[8])
{
    snprintf(out, 8, "#%06lX", (unsigned long)(c & 0xFFFFFF));
}

cJSON *settings_to_json(const buddy_settings_t *s)
{
    char buf[8];
    cJSON *j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "language", s->language);
    cJSON_AddNumberToObject(j, "volume", s->volume);
    cJSON_AddNumberToObject(j, "brightness", s->brightness);
    cJSON_AddNumberToObject(j, "screen_timeout_s", s->screen_timeout_s);
    cJSON_AddBoolToObject(j, "time_24h", s->time_24h);
    cJSON_AddStringToObject(j, "tz_posix", s->tz_posix);
    cJSON *t = cJSON_AddObjectToObject(j, "theme");
    cJSON_AddStringToObject(t, "preset", s->theme.preset);
    color_str(s->theme.accent, buf);
    cJSON_AddStringToObject(t, "accent", buf);
    color_str(s->theme.background, buf);
    cJSON_AddStringToObject(t, "background", buf);
    color_str(s->theme.clock, buf);
    cJSON_AddStringToObject(t, "clock", buf);
    color_str(s->theme.text, buf);
    cJSON_AddStringToObject(t, "text", buf);
    cJSON_AddNumberToObject(j, "max_listen_s", s->max_listen_s);
    cJSON *ql = cJSON_AddArrayToObject(j, "quick_languages");
    for (int i = 0; i < s->quick_language_count && i < SETTINGS_QUICK_LANG_MAX; i++) {
        cJSON *o = cJSON_CreateObject();
        cJSON_AddStringToObject(o, "code", s->quick_languages[i].code);
        cJSON_AddStringToObject(o, "label", s->quick_languages[i].label);
        cJSON_AddItemToArray(ql, o);
    }
    return j;
}

static esp_err_t nvs_set_string(const char *key, const char *value)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    if (value && value[0]) {
        err = nvs_set_str(h, key, value);
    } else {
        err = nvs_erase_key(h, key);
        if (err == ESP_ERR_NVS_NOT_FOUND) {
            err = ESP_OK;
        }
    }
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

static bool nvs_get_string(const char *key, char *out, size_t len)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        return false;
    }
    size_t sz = len;
    esp_err_t err = nvs_get_str(h, key, out, &sz);
    nvs_close(h);
    if (err != ESP_OK) {
        out[0] = '\0';
        return false;
    }
    return out[0] != '\0';
}

/* Persist current settings; caller holds s_lock. */
static void persist_locked(void)
{
    cJSON *j = settings_to_json(&s_settings);
    char *str = cJSON_PrintUnformatted(j);
    cJSON_Delete(j);
    if (!str) {
        return;
    }
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_str(h, KEY_SETTINGS_JSON, str);
        nvs_set_u32(h, KEY_SETTINGS_VER, s_version);
        nvs_commit(h);
        nvs_close(h);
    }
    cJSON_free(str);
}

static void notify_listeners(void)
{
    buddy_settings_t copy;
    settings_get(&copy);
    for (int i = 0; i < MAX_LISTENERS; i++) {
        if (s_listeners[i].cb) {
            s_listeners[i].cb(&copy, s_listeners[i].ctx);
        }
    }
}

/* ------------------------------------------------------------------------- */
/* Public API                                                                 */
/* ------------------------------------------------------------------------- */

esp_err_t settings_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS partition needs erase (%s)", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    s_lock = xSemaphoreCreateMutex();
    set_defaults(&s_settings);
    s_version = 0;

    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        size_t sz = 0;
        if (nvs_get_str(h, KEY_SETTINGS_JSON, NULL, &sz) == ESP_OK && sz > 0) {
            char *buf = malloc(sz);
            if (buf && nvs_get_str(h, KEY_SETTINGS_JSON, buf, &sz) == ESP_OK) {
                cJSON *j = cJSON_Parse(buf);
                if (j) {
                    merge_json(&s_settings, j);
                    cJSON_Delete(j);
                }
            }
            free(buf);
        }
        nvs_get_u32(h, KEY_SETTINGS_VER, &s_version);
        nvs_close(h);
    }

    uint8_t mac[6] = {0};
    esp_efuse_mac_get_default(mac);
    snprintf(s_device_id, sizeof(s_device_id), "buddy-%02x%02x%02x%02x%02x%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    ESP_LOGI(TAG, "loaded settings v%lu (lang=%s vol=%u bri=%u theme=%s) id=%s",
             (unsigned long)s_version, s_settings.language, s_settings.volume,
             s_settings.brightness, s_settings.theme.preset, s_device_id);
    return ESP_OK;
}

void settings_get(buddy_settings_t *out)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    *out = s_settings;
    xSemaphoreGive(s_lock);
}

uint32_t settings_get_version(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    uint32_t v = s_version;
    xSemaphoreGive(s_lock);
    return v;
}

esp_err_t settings_apply_server(const cJSON *settings, uint32_t version)
{
    if (!cJSON_IsObject(settings)) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    /* Server is the source of truth: start from defaults, then merge. */
    buddy_settings_t fresh;
    set_defaults(&fresh);
    merge_json(&fresh, settings);
    s_settings = fresh;
    s_version = version;
    persist_locked();
    xSemaphoreGive(s_lock);
    ESP_LOGI(TAG, "server settings applied, version %lu", (unsigned long)version);
    notify_listeners();
    return ESP_OK;
}

esp_err_t settings_apply_local(const cJSON *changes)
{
    if (!cJSON_IsObject(changes)) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    merge_json(&s_settings, changes);
    persist_locked();
    xSemaphoreGive(s_lock);
    notify_listeners();
    return ESP_OK;
}

void settings_add_listener(settings_listener_t cb, void *ctx)
{
    for (int i = 0; i < MAX_LISTENERS; i++) {
        if (!s_listeners[i].cb) {
            s_listeners[i].cb = cb;
            s_listeners[i].ctx = ctx;
            return;
        }
    }
    ESP_LOGE(TAG, "too many listeners");
}

bool settings_get_wifi(char ssid[SETTINGS_SSID_MAX], char pass[SETTINGS_PASS_MAX])
{
    bool ok = nvs_get_string(KEY_WIFI_SSID, ssid, SETTINGS_SSID_MAX);
    if (!nvs_get_string(KEY_WIFI_PASS, pass, SETTINGS_PASS_MAX)) {
        pass[0] = '\0'; /* open network */
    }
    return ok;
}

esp_err_t settings_set_wifi(const char *ssid, const char *pass)
{
    esp_err_t err = nvs_set_string(KEY_WIFI_SSID, ssid);
    if (err == ESP_OK) {
        err = nvs_set_string(KEY_WIFI_PASS, pass);
    }
    return err;
}

bool settings_get_server_url(char *out, size_t len)
{
    return nvs_get_string(KEY_SERVER_URL, out, len);
}

esp_err_t settings_set_server_url(const char *url)
{
    return nvs_set_string(KEY_SERVER_URL, url);
}

bool settings_get_last_server(char *out, size_t len)
{
    return nvs_get_string(KEY_LAST_SERVER, out, len);
}

esp_err_t settings_set_last_server(const char *url)
{
    char cur[SETTINGS_URL_MAX];
    if (nvs_get_string(KEY_LAST_SERVER, cur, sizeof(cur)) && strcmp(cur, url) == 0) {
        return ESP_OK; /* avoid needless flash writes */
    }
    return nvs_set_string(KEY_LAST_SERVER, url);
}

bool settings_get_token(char *out, size_t len)
{
    return nvs_get_string(KEY_TOKEN, out, len);
}

esp_err_t settings_set_token(const char *token)
{
    return nvs_set_string(KEY_TOKEN, token);
}

esp_err_t settings_erase_token(void)
{
    return nvs_set_string(KEY_TOKEN, NULL);
}

esp_err_t settings_factory_reset(void)
{
    /* Everything BuddyAI persists lives in this one namespace (Wi-Fi driver
     * storage is RAM-only, see net_wifi.c). */
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK;  /* nothing stored yet */
    }
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_erase_all(h);
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    ESP_LOGW(TAG, "factory reset: namespace '%s' erased (%s)", NVS_NS, esp_err_to_name(err));
    return err;
}

const char *settings_device_id(void)
{
    return s_device_id;
}
