/*
 * ola - CPU power management (ESP-IDF esp_pm).
 *
 * Three different things save power on this watch; this file is only the second:
 *   - Wi-Fi modem sleep: the radio sleeps between beacons (components/net, WIFI_PS_MIN_MODEM when idle).
 *   - CPU frequency scaling / automatic light sleep: here. While the screen is awake (and therefore during every
 *     conversation, which keeps it awake) a lock holds the CPU at full speed for LVGL, Opus and the TLS link;
 *     in standby the lock is released and the CPU drops to CONFIG_BUDDYAI_PM_MIN_FREQ_MHZ.
 *   - UI standby: the screen dims to its standby level (components/ui, apply_brightness).
 *
 * Automatic light sleep (CONFIG_BUDDYAI_PM_LIGHT_SLEEP, off by default) additionally needs the I2S port stopped
 * in standby (the I2S driver holds an APB lock while enabled) and the LVGL tick off the 5 ms esp_timer; both are
 * done here and in board_display.c. It is untested on hardware: see firmware/README.md "Measuring power".
 */
#include <stdio.h>
#include "esp_log.h"
#include "esp_pm.h"
#include "sdkconfig.h"
#include "board.h"

static const char *TAG = "board_pm";

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
#if CONFIG_BUDDYAI_PM_LIGHT_SLEEP
        board_audio_resume();
#endif
    } else {
#if CONFIG_BUDDYAI_PM_LIGHT_SLEEP
        board_audio_suspend();
#endif
        esp_pm_lock_release(s_awake_lock);
    }
#else
    (void)awake;
#endif
}

void board_pm_dump(void)
{
#if CONFIG_PM_ENABLE && CONFIG_PM_PROFILING
    esp_pm_dump_locks(stdout);
#endif
}
