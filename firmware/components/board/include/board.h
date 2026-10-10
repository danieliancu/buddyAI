/*
 * ola - board support for the Waveshare ESP32-S3-Touch-AMOLED-2.06
 *
 *  board_init()          I2C bus, PMU, RTC (must run first)
 *  board_display_init()  QSPI AMOLED + touch + esp_lvgl_port (LVGL task on core 0)
 *  board_audio_init()    I2S + ES8311 (speaker) + ES7210 (mics), fixed sample rate
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <time.h>
#include "esp_err.h"
#include "driver/i2c_master.h"
#include "esp_codec_dev.h"
#include "lvgl.h"
#include "board_pins.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Audio hardware runs at a single fixed rate (TX/RX share BCLK/WS on one I2S
 * port). Downlink Opus at 24 kHz is decoded straight to this rate by libopus. */
#define BOARD_AUDIO_SAMPLE_RATE     16000
#define BOARD_AUDIO_BITS            16

/* ---- core ---- */
esp_err_t board_init(void);
i2c_master_bus_handle_t board_i2c_bus(void);

/* ---- power (AXP2101) ---- */
typedef struct {
    int  battery_pct;       /* 0..100, -1 if no battery */
    int  battery_mv;
    bool charging;
    bool vbus_present;
    bool battery_present;
} board_power_status_t;

esp_err_t board_power_get_status(board_power_status_t *out);
/* PWR key since the last call: a short press, or held 2 s (the PMU switches the watch on again with a press
 * of about 1 s, and off by itself after a much longer hold). */
typedef enum { BOARD_POWER_KEY_NONE, BOARD_POWER_KEY_SHORT, BOARD_POWER_KEY_LONG } board_power_key_t;
board_power_key_t board_power_key_poll(void);
void      board_power_off(void);

/* ---- BOOT button (GPIO0) ---- */
/* Current level: true while the BOOT button is held (used for factory reset). */
bool      board_boot_button_down(void);
/* `cb` runs in the GPIO interrupt on every short press (released within 1.5 s): only post from it. */
typedef void (*board_button_cb_t)(void);
esp_err_t board_boot_button_on_tap(board_button_cb_t cb);

/* ---- RTC (PCF85063, stores UTC) ---- */
/* Accelerometer (QMI8658), in milli-g. Unavailable -> ESP_ERR_INVALID_STATE. */
bool      board_imu_available(void);
esp_err_t board_imu_read_accel_mg(int *x, int *y, int *z);

esp_err_t board_rtc_read(struct tm *utc);
esp_err_t board_rtc_write(const struct tm *utc);
/* Set the system clock from the RTC if the RTC holds a valid time. */
bool      board_rtc_restore_system_time(void);

/* ---- display / touch ---- */
lv_display_t *board_display_init(void);
/* Before board_display_init(): draw buffer height in lines (internal DMA RAM: 2 x 410 x lines x 2 bytes).
 * Wi-Fi setup mode uses fewer lines to leave room for the Bluetooth controller. */
void board_display_set_draw_lines(int lines);
esp_err_t board_display_set_brightness(int percent);   /* 0..100 */
esp_err_t board_display_power(bool on);                 /* panel on/off (keeps LVGL running) */
bool      board_display_lock(uint32_t timeout_ms);
void      board_display_unlock(void);

/* ---- audio ---- */
esp_err_t board_audio_init(void);
esp_codec_dev_handle_t board_audio_speaker(void);
esp_codec_dev_handle_t board_audio_mic(void);
esp_err_t board_audio_set_volume(int percent);          /* 0..100 */
/* Speaker power amplifier (NS4150B) enable - off when not playing to avoid hiss. */
void      board_audio_pa_enable(bool on);
/* Stop / restart the I2S port (automatic light sleep only, CONFIG_BUDDYAI_PM_LIGHT_SLEEP). Suspending is skipped
 * while the amplifier is on; resume is a no-op unless suspended. The audio component resumes before it plays or
 * records. */
void      board_audio_suspend(void);
void      board_audio_resume(void);

/* ---- CPU power management (board_pm.c) ---- */
/* Configures esp_pm (CONFIG_PM_ENABLE): full speed while awake, CONFIG_BUDDYAI_PM_MIN_FREQ_MHZ in standby.
 * ESP_ERR_NOT_SUPPORTED when power management is not built in: the CPU then stays at full speed. */
esp_err_t board_pm_init(void);
/* The screen is awake (or a conversation runs): hold the CPU at full speed. */
void      board_pm_set_awake(bool awake);
/* Development: print the power management locks and time per mode (CONFIG_PM_PROFILING). */
void      board_pm_dump(void);

#ifdef __cplusplus
}
#endif
