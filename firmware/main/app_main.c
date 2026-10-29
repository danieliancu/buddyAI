/*
 * ola watch firmware - application entry point.
 *
 * Wires the components together:
 *   board (HW) -> settings (NVS) -> ui (LVGL) -> audio -> net -> protocol_client
 * and runs the app loop (network events, battery / PWR key polling, RTC sync,
 * BOOT-button factory reset).
 *
 * Core usage: LVGL, Wi-Fi, WebSocket and protocol tasks on core 0;
 * audio capture / decode / playback tasks on core 1.
 */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "driver/usb_serial_jtag.h"
#include "hal/usb_serial_jtag_ll.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_attr.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "debug_snap.h"
#include "esp_secure_boot.h"
#include "esp_flash_encrypt.h"
#include "sdkconfig.h"
#if !CONFIG_BUDDYAI_RELEASE_BUILD
#include "test_question.h"
#endif

#include "board.h"
#include "settings.h"
#include "ui.h"
#include "audio.h"
#include "net.h"
#include "ble_prov.h"
#include "protocol_client.h"
#include "ota.h"

static const char *TAG = "app";

#define WIFI_CONNECT_GRACE_MS   20000
#define BATTERY_POLL_MS         10000
#define BATTERY_POLL_STANDBY_MS 60000   /* screen off: nobody sees the battery icon */
#define POWER_LOG_MS            (10 * 60 * 1000)    /* development builds: firmware/README.md "Measuring power" */
#define PWR_KEY_POLL_MS         200
#define PWR_KEY_POLL_STANDBY_MS 1000    /* screen off: the PMU latches the press, so it is only seen later */
#define FACTORY_RESET_HOLD_MS   8000    /* BOOT button hold time for the reset prompt */

typedef enum {
    APP_EV_NET,             /* arg = net_event_t */
    APP_EV_START_PORTAL,
    APP_EV_FACTORY_RESET,   /* user confirmed the reset screen */
    APP_EV_PROV_DONE,       /* Bluetooth setup: joined the new Wi-Fi and saved it */
    APP_EV_BOOT_TAP,        /* BOOT button pressed briefly: start talking */
    APP_EV_WAKE,            /* a touch or a BOOT press woke the watch from standby */
} app_ev_type_t;

typedef struct {
    app_ev_type_t type;
    int           arg;
} app_ev_t;

static QueueHandle_t s_app_q;
static bool s_audio_ready;   /* false in Wi-Fi setup mode (audio not started) */
static bool s_setup_mode;     /* defined with the setup code below */
static bool          s_wifi_configured;
static bool          s_server_error_shown;
static bool          s_low_batt_warned;
static bool          s_reply_started;
static esp_timer_handle_t s_typing_timer;   /* starts the typing clicks shortly into THINKING */
static volatile bool s_typing_wanted;
static int           s_batt_pct = -1;
static int           s_batt_mv;
static bool          s_charging;

/* ------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* ------------------------------------------------------------------------- */

static void post_app(app_ev_type_t type, int arg)
{
    app_ev_t ev = { .type = type, .arg = arg };
    xQueueSend(s_app_q, &ev, 0);
}

/* From a GPIO interrupt (board_boot_button_on_tap / board_set_wake_cb), the idle task right after a light sleep
 * (board_pm.c), or the app task when a BOOT tap ended while the watch was waking up: never blocks. */
static void post_from_any(app_ev_type_t type)
{
    app_ev_t ev = { .type = type };
    BaseType_t woken = pdFALSE;
    xQueueSendFromISR(s_app_q, &ev, &woken);
    if (woken && xPortInIsrContext()) {
        portYIELD_FROM_ISR();
    }
}

static void boot_tap_isr(void)
{
    post_from_any(APP_EV_BOOT_TAP);
}

static void wake_isr(void)
{
    post_from_any(APP_EV_WAKE);
}

static uint32_t s_standby_entries, s_wake_events;  /* touch diagnostics (development builds) */

/* Screen standby (LVGL task): the radio sleeps longer between beacons; the app loop slows down by itself. */
static void ui_standby(bool standby)
{
    net_wifi_set_standby(standby);
    if (standby) {
        s_standby_entries++;
    }
}

/* BOOT tap: the conversation screen with the mic on, from any screen, like the mic button (during a turn it
 * stops listening / interrupts the reply, as a second tap on the mic does). */
static void boot_tap(void)
{
    if (s_setup_mode || !s_audio_ready) {
        return;
    }
    ESP_LOGI(TAG, "BOOT tap - talk");
    ui_wake();
    proto_mic_tap();
}

/* PWR held 2 s: "Turning off…", then the PMU cuts the power. A press of about 1 s switches the watch on again. */
static void power_off(void)
{
    ESP_LOGW(TAG, "PWR held - powering off");
    ui_wake();
    ui_show_power_off();
    proto_network_down();       /* ends the session on purpose: nothing to report at the next start */
    vTaskDelay(pdMS_TO_TICKS(1500));
    board_display_power(false);
    board_power_off();
    vTaskDelay(portMAX_DELAY);  /* the power goes within milliseconds */
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
        s_batt_mv = ps.battery_mv;
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
    if (s_audio_ready) {
        audio_set_volume(s->volume);
    }
    ui_apply_settings(s);
}

static void on_net_event(net_event_t ev, void *ctx)
{
    post_app(APP_EV_NET, ev);   /* runs in the event loop task - defer */
}

static int on_battery_mv(void)
{
    return s_batt_mv;
}

static void on_status_request(int *battery_pct, bool *charging)
{
    *battery_pct = s_batt_pct;
    *charging = s_charging;
}

/* ---- Typing clicks while a reply is awaited ----
 * They start TYPING_DELAY_US into THINKING, so a quick answer does not leave a
 * lone click, and stop on any other state (the voice, a new turn, idle, errors). */
#define TYPING_DELAY_US     (400 * 1000)

static void typing_timer_cb(void *arg)
{
    if (s_typing_wanted) {
        audio_typing_start();
    }
}

static void typing_begin(void)
{
    if (!s_typing_timer) {
        const esp_timer_create_args_t args = { .callback = typing_timer_cb, .name = "typing" };
        if (esp_timer_create(&args, &s_typing_timer) != ESP_OK) {
            return;
        }
    }
    s_typing_wanted = true;
    esp_timer_stop(s_typing_timer);
    esp_timer_start_once(s_typing_timer, TYPING_DELAY_US);
}

static void typing_end(void)
{
    s_typing_wanted = false;
    if (s_typing_timer) {
        esp_timer_stop(s_typing_timer);
    }
    audio_typing_stop();
}

static void on_proto_event(const proto_event_t *ev, void *ctx)
{
    switch (ev->type) {
    case PROTO_EVT_CONNECTING:
        ui_set_hint(ui_text_connecting());
        break;
    case PROTO_EVT_SERVER_UNREACHABLE:
        ui_set_status(s_batt_pct, s_charging, current_link());
        /* Not over a conversation: it waits for the link ("Still thinking…") and shows the reconnect screen
         * itself if the link does not come back in time. */
        if (!s_server_error_shown && !ui_in_conversation()) {
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
        ui_show_connected();    /* a conversation or a notes screen stays where it is */
        break;
    case PROTO_EVT_CONV_STATE:
        if ((proto_conv_state_t)ev->num == PROTO_CONV_THINKING) {
            typing_begin();
        } else {
            typing_end();
        }
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
            ui_chat_user(ev->str);
        }
        break;
    case PROTO_EVT_REPLY_DELTA:
        s_reply_started = true;
        ui_chat_reply(ev->str);
        break;
    case PROTO_EVT_REPLY_DISPLAY:
        ui_chat_display(ev->str);
        break;
    case PROTO_EVT_STILL_THINKING:
        ui_chat_still_thinking(ev->num != 0);
        break;
    case PROTO_EVT_ITEM_EMAILED:
        ui_item_emailed(ev->num, ev->str && strcmp(ev->str, "sent") == 0);
        break;
    case PROTO_EVT_REPLY_RESET:
        s_reply_started = false;
        ui_chat_reply_reset();
        break;
    case PROTO_EVT_REPLY_LANGUAGE:
        ui_set_reply_language(ev->str);
        break;
    case PROTO_EVT_ERROR:
        typing_end();
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
        case PROTO_ERR_SUBSCRIPTION_REQUIRED:
            ui_show_error(UI_ERR_SUBSCRIPTION, NULL);
            break;
        case PROTO_ERR_LIMIT_REACHED:
            ui_show_error(UI_ERR_LIMIT, NULL);
            break;
        case PROTO_ERR_CONCURRENT:
            ui_show_error(UI_ERR_CONCURRENT, NULL);
            break;
        case PROTO_ERR_ACCOUNT_INACTIVE:
            ui_show_error(UI_ERR_ACCOUNT_INACTIVE, NULL);
            break;
        case PROTO_ERR_AI:
        case PROTO_ERR_NO_ANSWER:
        default:
            if (!ui_chat_no_answer()) {     /* in the conversation: a line there; elsewhere the screen */
                ui_show_error(UI_ERR_AI, ev->str);
            }
            break;
        }
        break;
    case PROTO_EVT_TIME_SET:
        sync_rtc_from_system();
        break;
    case PROTO_EVT_OTA:
        ui_show_ota(ev->num);
        break;
    case PROTO_EVT_ITEMS:
        ui_items_set(ev->str);
        break;
    case PROTO_EVT_LANGUAGES:
        ui_languages_set(ev->str);
        break;
    case PROTO_EVT_ITEMS_OPEN:
        ui_items_show_list(ev->num != 0);
        break;
    case PROTO_EVT_ITEM_SHOW:
        ui_item_show(ev->str);
        break;
    case PROTO_EVT_REMINDER:
        ui_reminder_alert(ev->str);
        audio_beep();
        break;
    case PROTO_EVT_NOTICE:
        ui_show_notice(ev->str);
        break;
    /* note edit mode: its own screen, never the chat screen */
    case PROTO_EVT_NOTE_SESSION:
        ui_note_session(ev->num != 0);
        break;
    case PROTO_EVT_NOTE_STATE:
        ui_note_state(ev->num == PROTO_CONV_LISTENING  ? UI_CONV_LISTENING
                      : ev->num == PROTO_CONV_THINKING ? UI_CONV_THINKING
                                                       : UI_CONV_IDLE);
        break;
    case PROTO_EVT_NOTE_TEXT:
        ui_note_text(ev->str, ev->num != 0);
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

/* The speaker icon on the text screens: mute / unmute (the volume setting stays as it is). */
static void ui_mute(bool muted)
{
    if (s_audio_ready) {
        audio_set_muted(muted);
    }
}

static void ui_chat_closed(void)
{
    proto_end_conversation();
}

static void ui_item_open(bool reminder, int number)
{
    proto_item_open(reminder, number);
}

static void ui_item_delete(bool reminder, int number)
{
    proto_item_delete(reminder, number);
}

static void ui_item_done(int number, bool done)
{
    proto_item_done(number, done);
}

static void ui_note_session_cb(bool open, int number)
{
    proto_note_session(open, number);
}

static void ui_reminder_session_cb(bool open, int number)
{
    proto_reminder_session(open, number);
}

static void ui_item_email(int number)
{
    proto_item_email(number);
}

static void ui_item_pin(int number, bool pinned)
{
    proto_item_pin(number, pinned);
}

static void ui_factory_reset(void)
{
    post_app(APP_EV_FACTORY_RESET, 0);
}

static int ui_audio_level(void)
{
    return audio_capture_active() ? audio_capture_level() : audio_playback_level();
}

/* ------------------------------------------------------------------------- */
/* App loop                                                                   */
/* ------------------------------------------------------------------------- */

/* Wi-Fi setup mode: entered at boot (no Wi-Fi saved, or "Wi-Fi setup" was chosen, which restarts into
 * it). Bluetooth starts first, before the display and audio take internal RAM; audio is not started
 * (setup always ends with a restart). RTC_NOINIT survives a software restart only. */
#define SETUP_BOOT_MAGIC 0x5E7B0070u
static RTC_NOINIT_ATTR uint32_t s_setup_boot;
static bool s_setup_mode;

static void on_ble_prov_done(void)
{
    post_app(APP_EV_PROV_DONE, 0);
}

/* Wi-Fi setup: the "ola-XXXX" network (iPhone, and recovery for Android) and Bluetooth setup (Android,
 * from the ola account) run together, both protected by the setup password shown on the watch. Bluetooth
 * only advertises while the watch is in setup. */
static void start_portal(void)
{
    char pass[SETTINGS_SETUP_PASS_MAX];
    if (!settings_get_setup_pass(pass)) {
        ESP_LOGE(TAG, "no setup password - Wi-Fi setup unavailable");
        return;
    }
    /* Bluetooth first: its controller needs a large block of internal RAM, which the setup network's
     * HTTP server and scan would otherwise fragment. It refuses to start (instead of crashing) if RAM is short. */
    if (!ble_prov_active() && ble_prov_start(net_portal_ssid(), pass, on_ble_prov_done) != ESP_OK) {
        ESP_LOGW(TAG, "Bluetooth setup unavailable - the setup network still works");
    }
    if (!net_portal_active()) {
        proto_network_down();
        if (net_portal_start(pass) != ESP_OK) {
            memset(pass, 0, sizeof(pass));
            return;
        }
    }
    ui_show_wifi_setup(net_portal_ssid(), pass);
    memset(pass, 0, sizeof(pass));
}

/* Erase Wi-Fi, server URL, token and settings, then restart into the setup
 * portal. This unpairs the watch locally; the owner removes it in the app. */
static void factory_reset(void)
{
    ESP_LOGW(TAG, "factory reset confirmed");
    ui_show_resetting();
    proto_network_down();
    esp_err_t err = settings_factory_reset();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "factory reset failed (%s) - erasing the whole NVS partition", esp_err_to_name(err));
        nvs_flash_deinit();
        nvs_flash_erase();
    }
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();
}

/* BOOT button held >= FACTORY_RESET_HOLD_MS -> confirmation screen (once per hold). */
static void poll_factory_reset_button(uint32_t now)
{
    static uint32_t down_since;
    static bool     prompted;

    if (!board_boot_button_down()) {
        down_since = 0;
        prompted = false;
        return;
    }
    if (down_since == 0) {
        down_since = now ? now : 1;
    } else if (!prompted && now - down_since >= FACTORY_RESET_HOLD_MS) {
        prompted = true;
        ESP_LOGW(TAG, "BOOT held %d s - asking for factory reset", FACTORY_RESET_HOLD_MS / 1000);
        ui_wake();
        ui_show_reset_confirm();
    }
}

static void handle_net_event(net_event_t ev)
{
    switch (ev) {
    case NET_EVT_STA_CONNECTED:
        ui_set_hint(ui_text_connecting());
        ui_set_status(s_batt_pct, s_charging, UI_LINK_WIFI);
        net_sntp_start();
        debug_snap_start();     /* development builds: screen snapshots over Wi-Fi */
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

#if !CONFIG_BUDDYAI_RELEASE_BUILD
/* Touch wake test: the counts survive a test on battery and are printed once a computer is connected again
 * (the USB port does not work after a light sleep without it), then on every change. */
static void touch_diag_log(void)
{
    static bool was_connected;
    static uint32_t last_irqs = UINT32_MAX, last_wakes, last_standby;
    bool connected = usb_serial_jtag_is_connected();
    uint32_t irqs, gpio_wakes;
    board_touch_diag(&irqs);
    board_pm_diag(&gpio_wakes);
    irqs += gpio_wakes;     /* a change in either prints the line */
    if (connected && (!was_connected || irqs != last_irqs || s_wake_events != last_wakes ||
                      s_standby_entries != last_standby)) {
        ESP_LOGI(TAG, "touch-diag: standby entries %lu, wake irqs + light-sleep GPIO wakes %lu (GPIO wakes %lu), "
                 "wake events %lu", (unsigned long)s_standby_entries, (unsigned long)irqs,
                 (unsigned long)gpio_wakes, (unsigned long)s_wake_events);
        last_irqs = irqs;
        last_wakes = s_wake_events;
        last_standby = s_standby_entries;
    }
    was_connected = connected;
}

/* One line every POWER_LOG_MS: the battery voltage over an idle night compares firmware versions (the PMU has no
 * current sensor). `awake_s` / `standby_s` count screen time since the previous line. */
static void power_log(uint32_t now)
{
    static uint32_t last_log, last_tick, awake_ms, standby_ms;
    if (last_tick) {
        uint32_t dt = now - last_tick;
        if (ui_is_awake()) {
            awake_ms += dt;
        } else {
            standby_ms += dt;
        }
    }
    last_tick = now;
    if (now - last_log < POWER_LOG_MS) {
        return;
    }
    last_log = now;
    proto_stats_t ps;
    proto_get_stats(&ps);
    ESP_LOGI(TAG, "power: up %lu s, battery %d mV %d%%%s, awake %lu s, standby %lu s, rssi %d, "
             "pings %lu, status %lu, sessions %lu, pong timeouts %lu",
             (unsigned long)(now / 1000), s_batt_mv, s_batt_pct, s_charging ? " charging" : "",
             (unsigned long)(awake_ms / 1000), (unsigned long)(standby_ms / 1000), net_wifi_rssi(),
             (unsigned long)ps.pings, (unsigned long)ps.statuses, (unsigned long)ps.sessions,
             (unsigned long)ps.pong_timeouts);
    awake_ms = standby_ms = 0;
    board_pm_dump();
}
#endif

#if !CONFIG_BUDDYAI_RELEASE_BUILD
/* Development test commands typed on the USB serial port (one per line), read straight from the port's
 * receive FIFO by the app loop: no driver, no task, no RAM (internal RAM is too tight for either).
 *   ask       a recorded question (test_question.h) instead of the mic
 *   tap       the mic button
 *   wifi [N]  the Wi-Fi screen (N: the password screen for network N)
 *   drop N    Wi-Fi off for N seconds (a lost link), then it reconnects */
static void test_console_poll(void)
{
    static char line[32];
    static size_t n;
    uint8_t buf[16];
    int got;
    while (usb_serial_jtag_ll_rxfifo_data_available() &&
           (got = usb_serial_jtag_ll_read_rxfifo(buf, sizeof(buf))) > 0) {
        for (int i = 0; i < got; i++) {
            char c = (char)buf[i];
            if (c != '\r' && c != '\n') {
                if (n < sizeof(line) - 1) {
                    line[n++] = c;
                }
                continue;
            }
            line[n] = '\0';
            n = 0;
            int secs = 0;
            if (strcmp(line, "ask") == 0) {
                ESP_LOGW(TAG, "test: ask");
                ui_wake();
                proto_test_ask(TEST_QUESTION_OPUS, TEST_QUESTION_LENS,
                               sizeof(TEST_QUESTION_LENS) / sizeof(TEST_QUESTION_LENS[0]));
            } else if (strcmp(line, "tap") == 0) {
                ESP_LOGW(TAG, "test: tap");
                boot_tap();
            } else if (strncmp(line, "wifi", 4) == 0 && (line[4] == '\0' || line[4] == ' ')) {
                ui_wake();
                ui_show_wifi(line[4] ? line + 5 : NULL);
            } else if (sscanf(line, "drop %d", &secs) == 1 && secs > 0) {
                net_wifi_test_drop(secs);
            }
        }
    }
}
#endif

static void app_loop(void)
{
    uint32_t boot_ms = esp_log_timestamp();
    uint32_t last_batt = 0;
    bool no_wifi_shown = false;

    for (;;) {
        app_ev_t ev;
        uint32_t poll_ms = ui_is_awake() ? PWR_KEY_POLL_MS : PWR_KEY_POLL_STANDBY_MS;
#if !CONFIG_BUDDYAI_RELEASE_BUILD
        test_console_poll();
#endif
        if (xQueueReceive(s_app_q, &ev, pdMS_TO_TICKS(poll_ms)) == pdTRUE) {
            if (ev.type == APP_EV_NET) {
                handle_net_event((net_event_t)ev.arg);
            } else if (ev.type == APP_EV_START_PORTAL) {
                if (s_setup_mode) {
                    start_portal();
                } else {
                    ESP_LOGI(TAG, "restarting into Wi-Fi setup mode");
                    s_setup_boot = SETUP_BOOT_MAGIC;
                    proto_network_down();
                    vTaskDelay(pdMS_TO_TICKS(200));
                    esp_restart();
                }
            } else if (ev.type == APP_EV_FACTORY_RESET) {
                factory_reset();
            } else if (ev.type == APP_EV_BOOT_TAP) {
                boot_tap();
            } else if (ev.type == APP_EV_WAKE) {
                s_wake_events++;
                ui_wake();
            } else if (ev.type == APP_EV_PROV_DONE) {
                ESP_LOGI(TAG, "Bluetooth setup finished - restarting");
                vTaskDelay(pdMS_TO_TICKS(4000));   /* the phone reads "connected" first */
                ble_prov_stop();
                esp_restart();
            }
        }

        uint32_t now = esp_log_timestamp();
        board_power_key_t key = board_power_key_poll();
        if (key == BOARD_POWER_KEY_LONG) {
            power_off();
        } else if (key == BOARD_POWER_KEY_SHORT) {
            ui_wake();
        }
        poll_factory_reset_button(now);
#if !CONFIG_BUDDYAI_RELEASE_BUILD
        power_log(now);
        touch_diag_log();
#endif
        uint32_t batt_ms = ui_is_awake() ? BATTERY_POLL_MS : BATTERY_POLL_STANDBY_MS;
        if (now - last_batt >= batt_ms || last_batt == 0) {
            last_batt = now;
            refresh_status();
        }
        /* Wi-Fi configured but never connected: show the error once. */
        if (s_wifi_configured && !no_wifi_shown && !net_wifi_connected() && !net_portal_active() &&
            now - boot_ms > WIFI_CONNECT_GRACE_MS && !ui_in_conversation()) {
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
    ESP_LOGI(TAG, "ola watch fw %s (IDF %s)", app->version, app->idf_ver);
#if CONFIG_BUDDYAI_RELEASE_BUILD
    /* WARN level so it is visible with the release log level. */
    ESP_LOGW(TAG, "release build %s: secure boot %s, flash encryption %s", app->version,
             esp_secure_boot_enabled() ? "ON" : "off", esp_flash_encryption_enabled() ? "ON" : "off");
#endif

    s_app_q = xQueueCreate(16, sizeof(app_ev_t));

    ESP_ERROR_CHECK(settings_init());
    ESP_ERROR_CHECK(board_init());
    board_pm_init();    /* frequency scaling in standby; logs and runs at full speed if unavailable */

    buddy_settings_t st;
    settings_get(&st);
    net_set_timezone(st.tz_posix);
    board_rtc_restore_system_time();

    wifi_saved_list_t saved;
    bool have_wifi = settings_get_wifi_list(&saved) > 0;
    memset(&saved, 0, sizeof(saved));
    s_setup_mode = !have_wifi || s_setup_boot == SETUP_BOOT_MAGIC;
    s_setup_boot = 0;
    if (s_setup_mode) {
        ESP_LOGI(TAG, "Wi-Fi setup mode (internal RAM free %u KB)",
                 (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024));
        char setup_pass[SETTINGS_SETUP_PASS_MAX];
        if (settings_get_setup_pass(setup_pass) &&
            ble_prov_start(net_portal_ssid(), setup_pass, on_ble_prov_done) != ESP_OK) {
            ESP_LOGW(TAG, "Bluetooth setup unavailable - the setup network still works");
        }
        memset(setup_pass, 0, sizeof(setup_pass));
        board_display_set_draw_lines(10);
    } else {
        ble_prov_release_memory();  /* Bluetooth serves Wi-Fi setup only, which always starts with a restart */
    }

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
            .on_factory_reset = ui_factory_reset,
            .on_item_open = ui_item_open,
            .on_item_delete = ui_item_delete,
            .on_item_done = ui_item_done,
            .on_chat_closed = ui_chat_closed,
            .on_note_session = ui_note_session_cb,
            .on_reminder_session = ui_reminder_session_cb,
            .on_item_pin = ui_item_pin,
            .on_item_email = ui_item_email,
            .on_standby = ui_standby,
            .on_mute = ui_mute,
        };
        ESP_ERROR_CHECK(ui_init(disp, &ui_cb));
        board_set_wake_cb(wake_isr);
        if (board_boot_button_on_tap(boot_tap_isr) != ESP_OK) {
            ESP_LOGE(TAG, "BOOT button interrupt failed - BOOT only opens the reset prompt");
        }
    }

    if (s_setup_mode) {
        ESP_LOGI(TAG, "audio not started in Wi-Fi setup mode");
    } else if (board_audio_init() == ESP_OK) {
        ESP_ERROR_CHECK(audio_init());
        audio_set_volume(st.volume);
        s_audio_ready = true;
    } else {
        ESP_LOGE(TAG, "audio init failed");
    }

    settings_add_listener(on_settings, NULL);

    ESP_ERROR_CHECK(net_init(on_net_event, NULL));

    const proto_config_t pcfg = {
        .on_event = on_proto_event,
        .ctx = NULL,
        .get_status = on_status_request,
        .get_battery_mv = on_battery_mv,
        .fw_version = app->version,
        .hw_model = CONFIG_BUDDYAI_HW_MODEL,
    };
    ESP_ERROR_CHECK(proto_init(&pcfg));

    if (!s_setup_mode) {
        s_wifi_configured = true;
        ui_set_hint(ui_text_connecting());
        net_wifi_connect_saved();
    } else {
        ESP_LOGI(TAG, "no Wi-Fi credentials - starting provisioning portal");
        start_portal();
    }

    ESP_LOGI(TAG, "free heap: internal %u KB, PSRAM %u KB",
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));

    app_loop();
}
