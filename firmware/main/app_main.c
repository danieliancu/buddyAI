/*
 * BuddyAI watch firmware - application entry point.
 *
 * Wires the components together:
 *   board (HW) -> settings (NVS) -> ui (LVGL) -> audio -> net -> protocol_client
 * and runs the app loop (network events, battery / PWR key polling, RTC sync).
 *
 * Core usage: LVGL, Wi-Fi, WebSocket and protocol tasks on core 0;
 * audio capture / decode / playback tasks on core 1.
 */
#include <string.h>
#include <time.h>
#include <sys/time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "sdkconfig.h"

#include "board.h"
#include "settings.h"
#include "ui.h"
#include "audio.h"
#include "net.h"
#include "protocol_client.h"
#include "ota.h"

static const char *TAG = "app";

#define WIFI_CONNECT_GRACE_MS   20000
#define BATTERY_POLL_MS         10000
#define PWR_KEY_POLL_MS         200

typedef enum {
    APP_EV_NET,             /* arg = net_event_t */
    APP_EV_START_PORTAL,
} app_ev_type_t;

typedef struct {
    app_ev_type_t type;
    int           arg;
} app_ev_t;

static QueueHandle_t s_app_q;
static bool          s_wifi_configured;
static bool          s_server_error_shown;
static bool          s_low_batt_warned;
static bool          s_reply_started;
static int           s_batt_pct = -1;
static bool          s_charging;

/* ------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* ------------------------------------------------------------------------- */

static void post_app(app_ev_type_t type, int arg)
{
    app_ev_t ev = { .type = type, .arg = arg };
    xQueueSend(s_app_q, &ev, 0);
}

static ui_link_t current_link(void)
{
    if (proto_session_ready()) {
        return UI_LINK_ONLINE;
    }
    return net_wifi_connected() ? UI_LINK_WIFI : UI_LINK_NO_WIFI;
}

static void refresh_status(void)
{
    board_power_status_t ps;
    if (board_power_get_status(&ps) == ESP_OK) {
        s_batt_pct = ps.battery_pct;
        s_charging = ps.charging;
    }
    ui_set_status(s_batt_pct, s_charging, current_link());

    /* Low battery warning (once per discharge). */
    if (s_batt_pct >= 0 && s_batt_pct <= CONFIG_BUDDYAI_LOW_BATTERY_PCT && !s_charging) {
        if (!s_low_batt_warned) {
            s_low_batt_warned = true;
            ui_show_error(UI_ERR_LOW_BATTERY, NULL);
        }
    } else if (s_charging || s_batt_pct > CONFIG_BUDDYAI_LOW_BATTERY_PCT + 5) {
        s_low_batt_warned = false;
    }
}

static void sync_rtc_from_system(void)
{
    time_t now = time(NULL);
    struct tm utc;
    gmtime_r(&now, &utc);
    if (board_rtc_write(&utc) == ESP_OK) {
        ESP_LOGI(TAG, "RTC updated from system clock");
    }
}

/* ------------------------------------------------------------------------- */
/* Callbacks                                                                  */
/* ------------------------------------------------------------------------- */

/* Settings changed (server or local): apply to UI, audio and timezone. */
static void on_settings(const buddy_settings_t *s, void *ctx)
{
    net_set_timezone(s->tz_posix);
    audio_set_volume(s->volume);
    ui_apply_settings(s);
}

static void on_net_event(net_event_t ev, void *ctx)
{
    post_app(APP_EV_NET, ev);   /* runs in the event loop task - defer */
}

static void on_status_request(int *battery_pct, bool *charging)
{
    *battery_pct = s_batt_pct;
    *charging = s_charging;
}

static void on_proto_event(const proto_event_t *ev, void *ctx)
{
    switch (ev->type) {
    case PROTO_EVT_CONNECTING:
        ui_set_hint(ui_text_connecting());
        break;
    case PROTO_EVT_SERVER_UNREACHABLE:
        ui_set_status(s_batt_pct, s_charging, current_link());
        if (!s_server_error_shown) {
            s_server_error_shown = true;
            ui_show_error(ev->num ? UI_ERR_NO_SERVER : UI_ERR_SERVER_UNREACHABLE, NULL);
        }
        break;
    case PROTO_EVT_DISCONNECTED:
        ui_set_status(s_batt_pct, s_charging, current_link());
        ui_set_hint(ui_text_connecting());
        break;
    case PROTO_EVT_PAIRING:
        ui_show_pairing(ev->str);
        break;
    case PROTO_EVT_PAIRED:
        ui_show_watchface();
        break;
    case PROTO_EVT_SESSION_READY:
        s_server_error_shown = false;
        ui_set_hint(NULL);
        ui_set_status(s_batt_pct, s_charging, UI_LINK_ONLINE);
        ui_show_watchface();
        break;
    case PROTO_EVT_CONV_STATE:
        switch ((proto_conv_state_t)ev->num) {
        case PROTO_CONV_LISTENING:
            s_reply_started = false;
            ui_set_conv_state(UI_CONV_LISTENING);
            break;
        case PROTO_CONV_THINKING:
            ui_set_conv_state(UI_CONV_THINKING);
            break;
        case PROTO_CONV_SPEAKING:
            ui_set_conv_state(UI_CONV_SPEAKING);
            break;
        default:
            ui_set_conv_state(UI_CONV_IDLE);
            break;
        }
        break;
    case PROTO_EVT_TRANSCRIPT:
        if (!s_reply_started) {
            ui_caption_set(ev->str);
        }
        break;
    case PROTO_EVT_REPLY_DELTA:
        if (!s_reply_started) {
            s_reply_started = true;
            ui_caption_set(ev->str);
        } else {
            ui_caption_append(ev->str);
        }
        break;
    case PROTO_EVT_REPLY_LANGUAGE:
        ui_set_reply_language(ev->str);
        break;
    case PROTO_EVT_ERROR:
        switch ((proto_error_t)ev->num) {
        case PROTO_ERR_NOT_CONNECTED:
            if (!net_wifi_connected()) {
                ui_show_error(UI_ERR_NO_WIFI, NULL);
            } else {
                ui_show_error(UI_ERR_SERVER_UNREACHABLE, NULL);
            }
            break;
        case PROTO_ERR_UNAUTHORIZED:
            ui_show_error(UI_ERR_UNPAIRED, NULL);
            break;
        case PROTO_ERR_PROTOCOL_UNSUPPORTED:
            ui_show_error(UI_ERR_PROTOCOL, ev->str);
            break;
        case PROTO_ERR_BUSY:
            ui_show_error(UI_ERR_BUSY, NULL);
            break;
        case PROTO_ERR_AI:
        default:
            ui_show_error(UI_ERR_AI, ev->str);
            break;
        }
        break;
    case PROTO_EVT_TIME_SET:
        sync_rtc_from_system();
        break;
    case PROTO_EVT_OTA:
        ui_show_ota(ev->num);
        break;
    }
}

/* ---- UI callbacks (LVGL task) ---- */

static void ui_mic_tap(void)
{
    proto_mic_tap();
}

static void ui_settings_change(const cJSON *changes)
{
    proto_settings_changed(changes);
}

static void ui_wifi_setup(void)
{
    post_app(APP_EV_START_PORTAL, 0);
}

static void ui_retry(void)
{
    s_server_error_shown = false;
    proto_reconnect();
}

static int ui_audio_level(void)
{
    return audio_capture_active() ? audio_capture_level() : audio_playback_level();
}

/* ------------------------------------------------------------------------- */
/* App loop                                                                   */
/* ------------------------------------------------------------------------- */

static void start_portal(void)
{
    if (net_portal_active()) {
        ui_show_wifi_setup(net_portal_ssid());
        return;
    }
    proto_network_down();
    if (net_portal_start() == ESP_OK) {
        ui_show_wifi_setup(net_portal_ssid());
    }
}

static void handle_net_event(net_event_t ev)
{
    switch (ev) {
    case NET_EVT_STA_CONNECTED:
        ui_set_hint(ui_text_connecting());
        ui_set_status(s_batt_pct, s_charging, UI_LINK_WIFI);
        net_sntp_start();
        if (!net_portal_active()) {
            proto_network_up();
        }
        break;
    case NET_EVT_STA_DISCONNECTED:
        proto_network_down();
        ui_set_status(s_batt_pct, s_charging, UI_LINK_NO_WIFI);
        break;
    case NET_EVT_PORTAL_STARTED:
        ESP_LOGI(TAG, "provisioning portal running: %s", net_portal_ssid());
        break;
    case NET_EVT_PORTAL_SAVED:
        ESP_LOGI(TAG, "new configuration saved - restarting");
        vTaskDelay(pdMS_TO_TICKS(1500));  /* let the HTTP response go out */
        esp_restart();
        break;
    case NET_EVT_TIME_SYNCED:
        sync_rtc_from_system();
        break;
    }
}

static void app_loop(void)
{
    uint32_t boot_ms = esp_log_timestamp();
    uint32_t last_batt = 0;
    bool no_wifi_shown = false;

    for (;;) {
        app_ev_t ev;
        if (xQueueReceive(s_app_q, &ev, pdMS_TO_TICKS(PWR_KEY_POLL_MS)) == pdTRUE) {
            if (ev.type == APP_EV_NET) {
                handle_net_event((net_event_t)ev.arg);
            } else if (ev.type == APP_EV_START_PORTAL) {
                start_portal();
            }
        }

        uint32_t now = esp_log_timestamp();
        if (board_power_key_pressed()) {
            ui_wake();
        }
        if (now - last_batt >= BATTERY_POLL_MS || last_batt == 0) {
            last_batt = now;
            refresh_status();
        }
        /* Wi-Fi configured but never connected: show the error once. */
        if (s_wifi_configured && !no_wifi_shown && !net_wifi_connected() && !net_portal_active() &&
            now - boot_ms > WIFI_CONNECT_GRACE_MS) {
            no_wifi_shown = true;
            ui_set_hint(ui_text_connecting());
            ui_show_error(UI_ERR_NO_WIFI, NULL);
        }
        if (net_wifi_connected()) {
            no_wifi_shown = false;
            boot_ms = now;  /* re-arm the grace period for the next outage */
        }
    }
}

void app_main(void)
{
    const esp_app_desc_t *app = esp_app_get_description();
    ESP_LOGI(TAG, "BuddyAI watch fw %s (IDF %s)", app->version, app->idf_ver);

    s_app_q = xQueueCreate(16, sizeof(app_ev_t));

    ESP_ERROR_CHECK(settings_init());
    ESP_ERROR_CHECK(board_init());

    buddy_settings_t st;
    settings_get(&st);
    net_set_timezone(st.tz_posix);
    board_rtc_restore_system_time();

    lv_display_t *disp = board_display_init();
    if (!disp) {
        ESP_LOGE(TAG, "display init failed - continuing headless");
    } else {
        const ui_callbacks_t ui_cb = {
            .on_mic_tap = ui_mic_tap,
            .on_settings_change = ui_settings_change,
            .on_wifi_setup = ui_wifi_setup,
            .on_retry = ui_retry,
            .get_audio_level = ui_audio_level,
        };
        ESP_ERROR_CHECK(ui_init(disp, &ui_cb));
    }

    if (board_audio_init() == ESP_OK) {
        ESP_ERROR_CHECK(audio_init());
        audio_set_volume(st.volume);
    } else {
        ESP_LOGE(TAG, "audio init failed");
    }

    settings_add_listener(on_settings, NULL);

    ESP_ERROR_CHECK(net_init(on_net_event, NULL));

    const proto_config_t pcfg = {
        .on_event = on_proto_event,
        .ctx = NULL,
        .get_status = on_status_request,
        .fw_version = app->version,
        .hw_model = CONFIG_BUDDYAI_HW_MODEL,
    };
    ESP_ERROR_CHECK(proto_init(&pcfg));

    char ssid[SETTINGS_SSID_MAX], pass[SETTINGS_PASS_MAX];
    if (settings_get_wifi(ssid, pass)) {
        s_wifi_configured = true;
        ui_set_hint(ui_text_connecting());
        net_wifi_connect(ssid, pass);
    } else {
        ESP_LOGI(TAG, "no Wi-Fi credentials - starting provisioning portal");
        start_portal();
    }
    memset(pass, 0, sizeof(pass));

    ESP_LOGI(TAG, "free heap: internal %u KB, PSRAM %u KB",
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));

    app_loop();
}
