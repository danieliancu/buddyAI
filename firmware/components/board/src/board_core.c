/*
 * ola - board core: shared I2C bus, AXP2101 PMU, PCF85063 RTC.
 *
 * AXP2101 register usage follows the XPowersLib register map (MIT, used by the
 * Waveshare 01_AXP2101 example) and the xiaozhi-esp32 board definition (MIT):
 * only the charger and ADC/gauge are configured - power rails are left at the
 * factory OTP defaults, as the official Waveshare BSP does.
 */
#include <string.h>
#include <sys/time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_timer.h"
#include "driver/gpio.h"
#include "board.h"

static const char *TAG = "board";

static i2c_master_bus_handle_t s_i2c_bus;
static i2c_master_dev_handle_t s_pmu;
static i2c_master_dev_handle_t s_rtc;
static i2c_master_dev_handle_t s_imu;

#define I2C_TIMEOUT_MS  50

/* ------------------------------------------------------------------------- */
/* I2C helpers                                                                */
/* ------------------------------------------------------------------------- */

static esp_err_t reg_read(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t *data, size_t len)
{
    return i2c_master_transmit_receive(dev, &reg, 1, data, len, I2C_TIMEOUT_MS);
}

static esp_err_t reg_write(i2c_master_dev_handle_t dev, uint8_t reg, const uint8_t *data, size_t len)
{
    uint8_t buf[16];
    if (len + 1 > sizeof(buf)) {
        return ESP_ERR_INVALID_SIZE;
    }
    buf[0] = reg;
    memcpy(buf + 1, data, len);
    return i2c_master_transmit(dev, buf, len + 1, I2C_TIMEOUT_MS);
}

static esp_err_t reg_write8(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t val)
{
    return reg_write(dev, reg, &val, 1);
}

static esp_err_t reg_update8(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t mask, uint8_t val)
{
    uint8_t cur;
    ESP_RETURN_ON_ERROR(reg_read(dev, reg, &cur, 1), TAG, "read 0x%02x", reg);
    cur = (cur & ~mask) | (val & mask);
    return reg_write8(dev, reg, cur);
}

static esp_err_t add_device(uint8_t addr, i2c_master_dev_handle_t *out)
{
    i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = addr,
        .scl_speed_hz = BOARD_I2C_FREQ_HZ,
    };
    return i2c_master_bus_add_device(s_i2c_bus, &cfg, out);
}

i2c_master_bus_handle_t board_i2c_bus(void)
{
    return s_i2c_bus;
}

/* ------------------------------------------------------------------------- */
/* AXP2101                                                                    */
/* ------------------------------------------------------------------------- */

#define AXP_STATUS1             0x00    /* b5 VBUS good, b3 battery present */
#define AXP_STATUS2             0x01    /* b6:5 current direction 01=charge 10=discharge */
#define AXP_IC_TYPE             0x03
#define AXP_COMMON_CONFIG       0x10    /* b0 soft power off */
#define AXP_GAUGE_CTRL          0x18    /* b3 fuel gauge enable */
#define AXP_ADC_CTRL            0x30    /* b0 VBAT, b2 VBUS, b3 VSYS, b4 die temp */
#define AXP_ADC_VBAT_H          0x34
#define AXP_IRQ_OFF_ON_LEVEL    0x27    /* b5:4 long-press IRQ time, b3:2 hardware power-off, b1:0 power-on */
#define AXP_INTEN2              0x41    /* b3 PWRON short press, b2 long press */
#define AXP_INTSTS1             0x48
#define AXP_INTSTS2             0x49
#define AXP_INTSTS3             0x4A
#define AXP_IPRECHG             0x61
#define AXP_ICC_CHG             0x62
#define AXP_ITERM_CHG           0x63
#define AXP_CV_CHG              0x64
#define AXP_BAT_DET_CTRL        0x68    /* b0 battery detection enable */
#define AXP_BAT_PERCENT         0xA4

#define AXP_PKEY_SHORT_BIT      (1 << 3)
#define AXP_PKEY_LONG_BIT       (1 << 2)
#define AXP_IRQ_LEVEL_2S        (2 << 4)    /* long press after 2 s (0: 1 s, 1: 1.5 s, 3: 2.5 s) */

static esp_err_t pmu_init(void)
{
    ESP_RETURN_ON_ERROR(add_device(BOARD_I2C_ADDR_AXP2101, &s_pmu), TAG, "pmu dev");
    uint8_t id = 0;
    ESP_RETURN_ON_ERROR(reg_read(s_pmu, AXP_IC_TYPE, &id, 1), TAG, "AXP2101 not responding");
    ESP_LOGI(TAG, "AXP2101 chip id 0x%02x", id);

    /* Charger: same parameters as the official Waveshare AXP2101 example. */
    reg_write8(s_pmu, AXP_IPRECHG, 0x02);                  /* precharge 50 mA */
    reg_write8(s_pmu, AXP_ICC_CHG, 0x0A);                  /* constant current 400 mA */
    reg_write8(s_pmu, AXP_ITERM_CHG, 0x01 | 0x10);         /* termination 25 mA, enabled */
    reg_update8(s_pmu, AXP_CV_CHG, 0x07, 0x03);            /* 4.2 V */

    /* ADC: battery, VBUS, system voltage, die temperature. Fuel gauge on. */
    reg_update8(s_pmu, AXP_ADC_CTRL, 0x1D, 0x1D);
    reg_update8(s_pmu, AXP_GAUGE_CTRL, 0x08, 0x08);
    /* Battery detection: without it STATUS1 never reports a battery and the gauge stays idle. */
    reg_update8(s_pmu, AXP_BAT_DET_CTRL, 0x01, 0x01);

    /* PWRON short- and long-press IRQs (polled, no IRQ GPIO needed): short wakes the screen, long (2 s)
     * switches the watch off. Power-on and the hardware power-off (longer hold) keep the PMU defaults.
     * Clear stale status. */
    reg_update8(s_pmu, AXP_IRQ_OFF_ON_LEVEL, 0x30, AXP_IRQ_LEVEL_2S);
    reg_update8(s_pmu, AXP_INTEN2, AXP_PKEY_SHORT_BIT | AXP_PKEY_LONG_BIT, AXP_PKEY_SHORT_BIT | AXP_PKEY_LONG_BIT);
    reg_write8(s_pmu, AXP_INTSTS1, 0xFF);
    reg_write8(s_pmu, AXP_INTSTS2, 0xFF);
    reg_write8(s_pmu, AXP_INTSTS3, 0xFF);
    return ESP_OK;
}

esp_err_t board_power_get_status(board_power_status_t *out)
{
    memset(out, 0, sizeof(*out));
    out->battery_pct = -1;
    if (!s_pmu) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t st[2];
    ESP_RETURN_ON_ERROR(reg_read(s_pmu, AXP_STATUS1, st, 2), TAG, "status");
    out->vbus_present = (st[0] >> 5) & 1;
    out->battery_present = (st[0] >> 3) & 1;
    out->charging = ((st[1] >> 5) & 0x03) == 0x01;

    uint8_t v[2];
    if (reg_read(s_pmu, AXP_ADC_VBAT_H, v, 2) == ESP_OK) {
        out->battery_mv = ((v[0] & 0x3F) << 8) | v[1];
    }
    if (out->battery_present) {
        uint8_t pct = 0;
        if (reg_read(s_pmu, AXP_BAT_PERCENT, &pct, 1) == ESP_OK) {
            out->battery_pct = pct > 100 ? 100 : pct;
        }
    } else if (out->battery_mv >= 3000 && out->battery_mv <= 4400) {
        /* Detection not settled yet but a cell is clearly there: rough estimate from voltage. */
        out->battery_present = true;
        int pct = (out->battery_mv - 3300) * 100 / (4150 - 3300);
        out->battery_pct = pct < 0 ? 0 : pct > 100 ? 100 : pct;
    }
    static int s_logged_present = -1;
    if (s_logged_present != (int)out->battery_present) {
        s_logged_present = out->battery_present;
        ESP_LOGI(TAG, "battery: status 0x%02x 0x%02x, present %d, %d mV, %d%%, vbus %d, charging %d",
                 st[0], st[1], out->battery_present, out->battery_mv, out->battery_pct, out->vbus_present,
                 out->charging);
    }
    return ESP_OK;
}

board_power_key_t board_power_key_poll(void)
{
    if (!s_pmu) {
        return BOARD_POWER_KEY_NONE;
    }
    uint8_t sts = 0;
    if (reg_read(s_pmu, AXP_INTSTS2, &sts, 1) != ESP_OK) {
        return BOARD_POWER_KEY_NONE;
    }
    uint8_t keys = sts & (AXP_PKEY_SHORT_BIT | AXP_PKEY_LONG_BIT);
    if (keys) {
        reg_write8(s_pmu, AXP_INTSTS2, keys);   /* write-1-to-clear */
    }
    return (keys & AXP_PKEY_LONG_BIT) ? BOARD_POWER_KEY_LONG
         : (keys & AXP_PKEY_SHORT_BIT) ? BOARD_POWER_KEY_SHORT : BOARD_POWER_KEY_NONE;
}

void board_power_off(void)
{
    if (s_pmu) {
        ESP_LOGW(TAG, "power off");
        reg_update8(s_pmu, AXP_COMMON_CONFIG, 0x01, 0x01);
    }
}

/* ------------------------------------------------------------------------- */
/* PCF85063 RTC (UTC, BCD registers)                                          */
/* ------------------------------------------------------------------------- */

#define RTC_CTRL1       0x00
#define RTC_SECONDS     0x04    /* b7 = OS (oscillator stopped -> time invalid) */

static uint8_t bcd2bin(uint8_t v) { return (v >> 4) * 10 + (v & 0x0F); }
static uint8_t bin2bcd(uint8_t v) { return ((v / 10) << 4) | (v % 10); }

static esp_err_t rtc_init(void)
{
    ESP_RETURN_ON_ERROR(add_device(BOARD_I2C_ADDR_PCF85063, &s_rtc), TAG, "rtc dev");
    uint8_t ctrl;
    ESP_RETURN_ON_ERROR(reg_read(s_rtc, RTC_CTRL1, &ctrl, 1), TAG, "PCF85063 not responding");
    /* Ensure the clock runs and 24 h mode is selected. */
    if (ctrl & ((1 << 5) | (1 << 1))) {
        reg_write8(s_rtc, RTC_CTRL1, ctrl & ~((1 << 5) | (1 << 1)));
    }
    return ESP_OK;
}

esp_err_t board_rtc_read(struct tm *utc)
{
    if (!s_rtc) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t r[7];
    ESP_RETURN_ON_ERROR(reg_read(s_rtc, RTC_SECONDS, r, sizeof(r)), TAG, "rtc read");
    if (r[0] & 0x80) {
        return ESP_ERR_INVALID_STATE; /* oscillator stopped: time not trustworthy */
    }
    memset(utc, 0, sizeof(*utc));
    utc->tm_sec = bcd2bin(r[0] & 0x7F);
    utc->tm_min = bcd2bin(r[1] & 0x7F);
    utc->tm_hour = bcd2bin(r[2] & 0x3F);
    utc->tm_mday = bcd2bin(r[3] & 0x3F);
    utc->tm_wday = r[4] & 0x07;
    utc->tm_mon = bcd2bin(r[5] & 0x1F) - 1;
    utc->tm_year = bcd2bin(r[6]) + 100; /* 2000-based */
    return ESP_OK;
}

esp_err_t board_rtc_write(const struct tm *utc)
{
    if (!s_rtc) {
        return ESP_ERR_INVALID_STATE;
    }
    if (utc->tm_year < 100 || utc->tm_year > 199) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t r[7] = {
        bin2bcd(utc->tm_sec),       /* also clears OS flag */
        bin2bcd(utc->tm_min),
        bin2bcd(utc->tm_hour),
        bin2bcd(utc->tm_mday),
        (uint8_t)(utc->tm_wday & 0x07),
        bin2bcd(utc->tm_mon + 1),
        bin2bcd(utc->tm_year - 100),
    };
    return reg_write(s_rtc, RTC_SECONDS, r, sizeof(r));
}

bool board_rtc_restore_system_time(void)
{
    struct tm utc;
    if (board_rtc_read(&utc) != ESP_OK || utc.tm_year < 124) {
        ESP_LOGW(TAG, "RTC time invalid - waiting for SNTP/server time");
        return false;
    }
    /* mktime() interprets the struct in the local zone; convert as UTC. */
    int64_t days = 0;
    int y = utc.tm_year + 1900;
    for (int yr = 1970; yr < y; yr++) {
        days += ((yr % 4 == 0 && yr % 100 != 0) || yr % 400 == 0) ? 366 : 365;
    }
    static const int mdays[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    for (int m = 0; m < utc.tm_mon; m++) {
        days += mdays[m];
        if (m == 1 && ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0)) {
            days += 1;
        }
    }
    days += utc.tm_mday - 1;
    struct timeval tv = {
        .tv_sec = (time_t)(days * 86400 + utc.tm_hour * 3600 + utc.tm_min * 60 + utc.tm_sec),
        .tv_usec = 0,
    };
    settimeofday(&tv, NULL);
    ESP_LOGI(TAG, "system time restored from RTC: %04d-%02d-%02d %02d:%02d:%02d UTC",
             y, utc.tm_mon + 1, utc.tm_mday, utc.tm_hour, utc.tm_min, utc.tm_sec);
    return true;
}

/* ------------------------------------------------------------------------- */

/* ------------------------------------------------------------------------- */
/* IMU (QMI8658): accelerometer only, polled (its interrupt pins' routing is   */
/* not confirmed on this board)                                               */
/* ------------------------------------------------------------------------- */

#define QMI_WHO_AM_I    0x00    /* reads 0x05 */
#define QMI_CTRL1       0x02    /* b6: register address auto-increment */
#define QMI_CTRL2       0x03    /* accel: b6..4 full scale, b3..0 output data rate */
#define QMI_CTRL7       0x08    /* b0: accelerometer enable */
#define QMI_AX_L        0x35    /* AX, AY, AZ: int16 little endian */
#define QMI_RESET       0x60
#define QMI_ACC_4G_125HZ 0x16   /* +-4 g (8192 LSB/g), ~125 Hz */
#define QMI_LSB_PER_G   8192

static esp_err_t imu_init(void)
{
    ESP_RETURN_ON_ERROR(add_device(BOARD_I2C_ADDR_QMI8658, &s_imu), TAG, "imu dev");
    uint8_t id = 0;
    ESP_RETURN_ON_ERROR(reg_read(s_imu, QMI_WHO_AM_I, &id, 1), TAG, "QMI8658 not responding");
    if (id != 0x05) {
        ESP_LOGW(TAG, "QMI8658 WHO_AM_I = 0x%02x (expected 0x05)", id);
    }
    reg_write8(s_imu, QMI_RESET, 0xB0);
    vTaskDelay(pdMS_TO_TICKS(20));
    ESP_RETURN_ON_ERROR(reg_write8(s_imu, QMI_CTRL1, 0x40), TAG, "imu ctrl1");
    ESP_RETURN_ON_ERROR(reg_write8(s_imu, QMI_CTRL2, QMI_ACC_4G_125HZ), TAG, "imu ctrl2");
    ESP_RETURN_ON_ERROR(reg_write8(s_imu, QMI_CTRL7, 0x01), TAG, "imu ctrl7");
    return ESP_OK;
}

esp_err_t board_imu_read_accel_mg(int *x, int *y, int *z)
{
    if (!s_imu) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t d[6];
    ESP_RETURN_ON_ERROR(reg_read(s_imu, QMI_AX_L, d, sizeof(d)), TAG, "imu read");
    int16_t raw[3];
    for (int i = 0; i < 3; i++) {
        raw[i] = (int16_t)((uint16_t)d[2 * i] | ((uint16_t)d[2 * i + 1] << 8));
    }
    *x = raw[0] * 1000 / QMI_LSB_PER_G;
    *y = raw[1] * 1000 / QMI_LSB_PER_G;
    *z = raw[2] * 1000 / QMI_LSB_PER_G;
    return ESP_OK;
}

bool board_imu_available(void)
{
    return s_imu != NULL;
}

esp_err_t board_init(void)
{
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = BOARD_I2C_PORT,
        .sda_io_num = BOARD_I2C_SDA,
        .scl_io_num = BOARD_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_cfg, &s_i2c_bus), TAG, "i2c bus");

    if (pmu_init() != ESP_OK) {
        ESP_LOGE(TAG, "PMU init failed - battery info unavailable");
    }
    if (rtc_init() != ESP_OK) {
        ESP_LOGE(TAG, "RTC init failed");
    }
    if (imu_init() != ESP_OK) {
        ESP_LOGE(TAG, "IMU init failed - shake to wake unavailable");
        s_imu = NULL;
    }

    /* BOOT button (GPIO0, active low, external pull-up on the board). Only
     * sampled at run time - holding it during reset still enters the ROM
     * download mode as usual. */
    const gpio_config_t boot = {
        .pin_bit_mask = 1ULL << BOARD_BTN_BOOT,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    if (gpio_config(&boot) != ESP_OK) {
        ESP_LOGE(TAG, "BOOT button config failed");
    }
    return ESP_OK;
}

bool board_boot_button_down(void)
{
    return gpio_get_level(BOARD_BTN_BOOT) == 0;
}

/* A press shorter than BOOT_TAP_MAX_US is a tap; longer holds belong to the factory-reset prompt (8 s). Timed
 * from the edges, so a quick tap is never missed between two polls; contact bounce (< 30 ms) is ignored. */
#define BOOT_TAP_MIN_US     (30 * 1000)
#define BOOT_TAP_MAX_US     (1500 * 1000)
static board_button_cb_t s_boot_tap_cb;
static volatile int64_t  s_boot_down_us;

static void boot_isr(void *arg)
{
    int64_t now = esp_timer_get_time();
    if (gpio_get_level(BOARD_BTN_BOOT) == 0) {
        s_boot_down_us = now;
    } else if (s_boot_down_us) {
        int64_t held = now - s_boot_down_us;
        s_boot_down_us = 0;
        if (held >= BOOT_TAP_MIN_US && held < BOOT_TAP_MAX_US && s_boot_tap_cb) {
            s_boot_tap_cb();
        }
    }
}

esp_err_t board_boot_button_on_tap(board_button_cb_t cb)
{
    s_boot_tap_cb = cb;
    esp_err_t err = gpio_install_isr_service(0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {     /* INVALID_STATE: already installed (touch) */
        return err;
    }
    ESP_RETURN_ON_ERROR(gpio_set_intr_type(BOARD_BTN_BOOT, GPIO_INTR_ANYEDGE), TAG, "boot intr");
    return gpio_isr_handler_add(BOARD_BTN_BOOT, boot_isr, NULL);
}
