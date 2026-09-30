/*
 * ola - Waveshare ESP32-S3-Touch-AMOLED-2.06 pin map
 *
 * Single source of truth for every GPIO / bus address used by the firmware.
 *
 * Sources (cross-checked 2026-09-28):
 *  [WS-BSP]  Waveshare BSP component waveshare/esp32_s3_touch_amoled_2_06 v2.0.0
 *            (include/bsp/esp32_s3_touch_amoled_2_06.h), used by the official
 *            repo github.com/waveshareteam/ESP32-S3-Touch-AMOLED-2.06 ESP-IDF examples.
 *  [WS-ARD]  Official repo examples/arduino/libraries/Mylibrary/pin_config.h
 *  [XZ]      github.com/78/xiaozhi-esp32 main/boards/waveshare/esp32-s3-touch-amoled-2.06/config.h
 *            (MIT licensed)
 *  [SCH]     Official schematic ESP32-S3-Touch-AMOLED-2.06-Schematic-V1.0.pdf
 *
 * Notes:
 *  - The Waveshare wiki table shows SD pins overlapping I2S and PWR overlapping
 *    touch INT. The BSP/xiaozhi/schematic agree on the values below: the SD card
 *    uses GPIO1/2/3 (+CS 17) and does NOT overlap I2S (16/40/41/42/45); the PWR
 *    key is wired to the AXP2101 PWRON pin (not a GPIO), so it does not collide
 *    with touch INT (GPIO38).
 *  - Audio: ES8311 is used as DAC only (speaker via NS4150B PA); the two onboard
 *    microphones go to an ES7210 4-ch ADC (MIC1/MIC2, MIC3 = AEC loopback of the
 *    speaker output) whose SDOUT is GPIO42 [SCH][WS-BSP][XZ].
 *  - All I2C peripherals share one bus (GPIO14/15) [WS-BSP][XZ][SCH].
 */
#pragma once

#include "driver/gpio.h"

/* ---- I2C (shared: touch, codecs, PMU, RTC, IMU) ---- */
#define BOARD_I2C_PORT              I2C_NUM_0
#define BOARD_I2C_SCL               GPIO_NUM_14     /* [WS-BSP][WS-ARD][XZ] */
#define BOARD_I2C_SDA               GPIO_NUM_15     /* [WS-BSP][WS-ARD][XZ] */
#define BOARD_I2C_FREQ_HZ           400000

/* 7-bit I2C addresses */
#define BOARD_I2C_ADDR_AXP2101      0x34            /* [XZ] Pmic(i2c_bus_, 0x34) */
#define BOARD_I2C_ADDR_PCF85063     0x51            /* PCF85063A datasheet fixed address */
#define BOARD_I2C_ADDR_QMI8658      0x6B            /* [SCH] "0X6B" next to U5 */
#define BOARD_I2C_ADDR_FT3168       0x38            /* FT5x06-family default (ESP_LCD_TOUCH_IO_I2C_FT5x06_ADDRESS) */
/* esp_codec_dev expects 8-bit (shifted) addresses */
#define BOARD_CODEC_ES8311_ADDR8    0x30            /* ES8311_CODEC_DEFAULT_ADDR (7-bit 0x18) */
#define BOARD_CODEC_ES7210_ADDR8    0x80            /* ES7210_CODEC_DEFAULT_ADDR (7-bit 0x40, [SCH] A0=A1=0) */

/* ---- AMOLED CO5300, QSPI ---- */
#define BOARD_LCD_SPI_HOST          SPI2_HOST
#define BOARD_LCD_CS                GPIO_NUM_12     /* [WS-BSP][WS-ARD][XZ] */
#define BOARD_LCD_PCLK              GPIO_NUM_11     /* [WS-BSP][WS-ARD][XZ] */
#define BOARD_LCD_D0                GPIO_NUM_4      /* [WS-BSP][WS-ARD][XZ] */
#define BOARD_LCD_D1                GPIO_NUM_5
#define BOARD_LCD_D2                GPIO_NUM_6
#define BOARD_LCD_D3                GPIO_NUM_7
#define BOARD_LCD_RST               GPIO_NUM_8      /* [WS-BSP][WS-ARD][XZ] */
#define BOARD_LCD_TE                GPIO_NUM_NC     /* LCD_TE exists on the FPC [SCH]; routing to the S3 unverified - unused */
#define BOARD_LCD_H_RES             410
#define BOARD_LCD_V_RES             502
#define BOARD_LCD_X_GAP             0x16            /* column offset 22, [WS-BSP] esp_lcd_panel_set_gap(panel, 0x16, 0) */
#define BOARD_LCD_Y_GAP             0
#define BOARD_LCD_PCLK_HZ           (40 * 1000 * 1000)

/* ---- Touch FT3168 (FT5x06 compatible), on the shared I2C bus ---- */
#define BOARD_TOUCH_RST             GPIO_NUM_9      /* [WS-BSP][WS-ARD][XZ] */
#define BOARD_TOUCH_INT             GPIO_NUM_38     /* [WS-BSP][WS-ARD][XZ] */

/* ---- Audio I2S (ESP32-S3 is I2S master, codecs are slaves) ---- */
#define BOARD_I2S_PORT              I2S_NUM_0
#define BOARD_I2S_MCLK              GPIO_NUM_16     /* [WS-BSP][XZ] */
#define BOARD_I2S_BCLK              GPIO_NUM_41     /* [WS-BSP] BSP_I2S_SCLK, [XZ] */
#define BOARD_I2S_WS                GPIO_NUM_45     /* [WS-BSP] BSP_I2S_LCLK, [XZ] */
#define BOARD_I2S_DOUT              GPIO_NUM_40     /* to ES8311 DSDIN [WS-BSP][XZ] */
#define BOARD_I2S_DIN               GPIO_NUM_42     /* from ES7210 SDOUT [WS-BSP][XZ][SCH] */
#define BOARD_AUDIO_PA_EN           GPIO_NUM_46     /* NS4150B CTRL, active high [WS-BSP][XZ][SCH PA_CTRL] */

/* ---- Buttons ---- */
#define BOARD_BTN_BOOT              GPIO_NUM_0      /* [XZ] BOOT_BUTTON_GPIO */
/* PWR key -> AXP2101 PWRON (short press IRQ / long press power-off), no GPIO */

/* ---- microSD (SPI mode, unused by ola for now) ---- */
#define BOARD_SD_CLK                GPIO_NUM_2      /* [WS-BSP][WS-ARD] */
#define BOARD_SD_CMD                GPIO_NUM_1
#define BOARD_SD_D0                 GPIO_NUM_3
#define BOARD_SD_CS                 GPIO_NUM_17     /* [WS-ARD] */

/* ---- Unverified / unused lines (schematic net names only; confirm in M0) ---- */
/* RTC_INT, QMI_INT1/QMI_INT2, MOTOR: routing to specific GPIOs not confirmed by
 * any example - intentionally not used. */
