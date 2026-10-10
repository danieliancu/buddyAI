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

#if CONFIG_PM_ENABLE
static esp_pm_lock_handle_t s_awake_lock;
static bool s_awake = true;     /* board_pm_init() takes the lock: the screen starts awake */
#endif

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
    err = esp_pm_configure(&cfg);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "esp_pm_configure: %s - running at full speed", esp_err_to_name(err));
        return err;
    }
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

void board_standby(bool on)
{
    if (on) {
        board_display_standby(true);
        board_boot_button_standby(true);
        board_audio_suspend();
        board_pm_set_awake(false);
    } else {
        board_pm_set_awake(true);           /* full speed first: the panel comes back sooner */
        board_boot_button_standby(false);
        board_display_standby(false);
        board_audio_resume();               /* before the user can speak: a conversation may follow */
    }
}

void board_pm_dump(void)
{
#if CONFIG_PM_ENABLE && CONFIG_PM_PROFILING
    esp_pm_dump_locks(stdout);
#endif
}
