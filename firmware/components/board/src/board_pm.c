/*
 * ola - CPU power management (ESP-IDF esp_pm).
 *
 * Three different things save power on this watch; this file is only the second:
 *   - Wi-Fi modem sleep: the radio sleeps between beacons (components/net, WIFI_PS_MIN_MODEM when idle).
 *   - CPU frequency scaling / automatic light sleep: here. While the screen is awake (and therefore during every
 *     conversation, which keeps it awake) a lock holds the CPU at full speed for LVGL, Opus and the TLS link;
 *     in standby the lock is released and the CPU drops to CONFIG_BUDDYAI_PM_MIN_FREQ_MHZ.
 *   - UI standby: the screen switches off after its timeout (components/ui, apply_brightness -> board_standby).
 *
 * Standby (board_standby): the panel sleeps, the audio codecs and the I2S port are closed (the I2S driver holds an
 * APB lock while enabled), the touch INT and the BOOT key become light-sleep wake-up sources, and the CPU lock is
 * released. With CONFIG_BUDDYAI_PM_LIGHT_SLEEP the chip then sleeps between Wi-Fi beacons (automatic light sleep;
 * the LVGL tick reads the clock instead of a 5 ms timer, board_display.c). See firmware/README.md §10.
 */
#include <stdio.h>
#include "esp_log.h"
#include "esp_pm.h"
#include "driver/usb_serial_jtag.h"
#include "esp_sleep.h"
#include "sdkconfig.h"
#include "board.h"

static const char *TAG = "board_pm";

static board_button_cb_t s_wake_cb;

void board_set_wake_cb(board_button_cb_t cb)
{
    s_wake_cb = cb;
}

void board_wake_from_isr(void)
{
    if (s_wake_cb) {
        s_wake_cb();
    }
}

static volatile bool     s_standby;
static volatile uint32_t s_gpio_wakes;
static volatile uint32_t s_light_sleeps;

/* The FT3168 reports a touch with a short INT pulse. It is enough to wake the chip from light sleep, but by the
 * time the CPU runs again the pulse is often over, so the level interrupt never fires and the screen stayed dark
 * (4 taps in 5 missed on battery, none with the CPU awake). Asking why the chip woke catches those: a GPIO
 * wake-up in standby (touch or BOOT) wakes the screen. Runs in the idle task, right after the sleep. */
#if CONFIG_PM_LIGHT_SLEEP_CALLBACKS
static esp_err_t light_sleep_exit_cb(int64_t sleep_time_us, void *arg)
{
    s_light_sleeps++;
    if (s_standby && esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_GPIO) {
        s_gpio_wakes++;
        board_wake_from_isr();
    }
    return ESP_OK;
}
#endif

void board_pm_diag(uint32_t *gpio_wakes)
{
    *gpio_wakes = s_gpio_wakes;
}

#if CONFIG_PM_ENABLE
static esp_pm_lock_handle_t s_awake_lock;
static bool s_awake = true;     /* board_pm_init() takes the lock: the screen starts awake */
#endif

uint32_t board_pm_light_sleeps(void)
{
    return s_light_sleeps;
}

#if CONFIG_USJ_NO_AUTO_LS_ON_CONNECTION
/* One light sleep kills the USB port until the cable is pulled out, and ESP-IDF's connection monitor is not enough to
 * prevent it: it counts a port as gone after 3 ms without a start-of-frame and releases its lock at once. Measured on
 * the watch: 3 such false disconnections in the first minute with a computer attached, one light sleep in one of them,
 * and no more logs or flashing from there. So a port only counts as gone when it stays gone for this long. A charger
 * has no USB host: the first light sleep comes this much later, nothing else changes. */
#define USB_GONE_MS  5000

static esp_pm_lock_handle_t s_usb_lock;
static bool s_usb_held;

static void usb_guard_start(void)
{
    if (esp_pm_lock_create(ESP_PM_NO_LIGHT_SLEEP, 0, "usb_guard", &s_usb_lock) != ESP_OK) {
        ESP_LOGW(TAG, "USB guard lock not created: the USB port may stop after a light sleep");
        return;
    }
    esp_pm_lock_acquire(s_usb_lock);
    s_usb_held = true;
}
#endif

/* From the app loop, never a tick hook: usb_serial_jtag_is_connected() is in flash, so calling it from an interrupt
 * panics during a flash write. The monitor's own lock covers a real reconnection until the next call here. */
void board_pm_usb_guard_poll(uint32_t now_ms)
{
#if CONFIG_USJ_NO_AUTO_LS_ON_CONNECTION
    static uint32_t last_seen_ms;
    if (!s_usb_lock) {
        return;
    }
    if (usb_serial_jtag_is_connected()) {
        last_seen_ms = now_ms;
        if (!s_usb_held) {
            s_usb_held = true;
            esp_pm_lock_acquire(s_usb_lock);
        }
    } else if (s_usb_held && now_ms - last_seen_ms >= USB_GONE_MS) {
        s_usb_held = false;
        esp_pm_lock_release(s_usb_lock);
    }
#else
    (void)now_ms;
#endif
}

esp_err_t board_pm_init(void)
{
#if CONFIG_PM_ENABLE
    esp_pm_config_t cfg = {
        .max_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .min_freq_mhz = CONFIG_BUDDYAI_PM_MIN_FREQ_MHZ,
#if CONFIG_BUDDYAI_PM_LIGHT_SLEEP
        .light_sleep_enable = true,
#else
        .light_sleep_enable = false,
#endif
    };
    esp_err_t err = esp_pm_lock_create(ESP_PM_CPU_FREQ_MAX, 0, "awake", &s_awake_lock);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "pm lock: %s - running at full speed", esp_err_to_name(err));
        return err;
    }
    esp_pm_lock_acquire(s_awake_lock);
#if CONFIG_USJ_NO_AUTO_LS_ON_CONNECTION
    usb_guard_start();      /* before light sleep is allowed */
#endif
    err = esp_pm_configure(&cfg);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "esp_pm_configure: %s - running at full speed", esp_err_to_name(err));
        return err;
    }
#if CONFIG_BUDDYAI_PM_LIGHT_SLEEP
    /* GPIO wake-up as a light-sleep source: without it the pins armed by gpio_wakeup_enable() (touch INT, BOOT)
     * do not wake the chip, and a touch was only noticed if it happened while the chip was awake anyway. */
    if (esp_sleep_enable_gpio_wakeup() != ESP_OK) {
        ESP_LOGW(TAG, "GPIO wake-up not enabled: touch and BOOT may not wake the watch");
    }
#endif
#if CONFIG_PM_LIGHT_SLEEP_CALLBACKS
    esp_pm_sleep_cbs_register_config_t cbs = { .exit_cb = light_sleep_exit_cb };
    if (esp_pm_light_sleep_register_cbs(&cbs) != ESP_OK) {
        ESP_LOGW(TAG, "light sleep exit callback not registered: quick taps may not wake the screen");
    }
#endif
    ESP_LOGI(TAG, "power management: %d-%d MHz, light sleep %s", cfg.min_freq_mhz, cfg.max_freq_mhz,
             cfg.light_sleep_enable ? "on" : "off");
    return ESP_OK;
#else
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

void board_pm_set_awake(bool awake)
{
#if CONFIG_PM_ENABLE
    if (!s_awake_lock || awake == s_awake) {
        return;
    }
    s_awake = awake;
    if (awake) {
        esp_pm_lock_acquire(s_awake_lock);
    } else {
        esp_pm_lock_release(s_awake_lock);
    }
#else
    (void)awake;
#endif
}

void board_standby(bool on, bool keep_panel)
{
    s_standby = on;
    if (on) {
        board_display_standby(true, keep_panel);
        board_boot_button_standby(true);
        board_audio_suspend();
        board_pm_set_awake(false);
    } else {
        board_pm_set_awake(true);           /* full speed first: the panel comes back sooner */
        board_boot_button_standby(false);
        board_display_standby(false, false);
        board_audio_resume();               /* before the user can speak: a conversation may follow */
    }
}

void board_pm_dump(void)
{
#if CONFIG_PM_ENABLE && CONFIG_PM_PROFILING
    esp_pm_dump_locks(stdout);
#endif
}
