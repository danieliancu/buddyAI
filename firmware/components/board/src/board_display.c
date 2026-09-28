/*
 * BuddyAI - CO5300 QSPI AMOLED (410x502) + FT3168 touch + esp_lvgl_port.
 *
 * Panel init sequence and the "even coordinates" rounder come from the official
 * Waveshare BSP (waveshare/esp32_s3_touch_amoled_2_06) and match the xiaozhi-esp32
 * board definition for this watch (MIT). We use the espressif/esp_lcd_co5300
 * driver instead of the SH8601 driver the BSP uses - both speak the same QSPI
 * command protocol (opcode 0x02 cmd / 0x32 color).
 */
#include "freertos/FreeRTOS.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_co5300.h"
#include "esp_lcd_touch_ft5x06.h"
#include "esp_lvgl_port.h"
#include "board.h"

static const char *TAG = "board_disp";

static esp_lcd_panel_io_handle_t s_io;
static esp_lcd_panel_handle_t s_panel;
static esp_lcd_touch_handle_t s_touch;
static lv_display_t *s_disp;

/* LVGL draw buffer: 20 lines (~16 KB), double buffered, in internal DMA RAM.
 * PSRAM buffers would be bounced through a temporary internal DMA buffer per
 * queued SPI transaction, which fails once Wi-Fi has taken its share of
 * internal RAM. These are allocated at boot, before Wi-Fi starts. */
#define DRAW_BUF_LINES      20
/* One flush = one SPI transaction. */
#define SPI_MAX_TRANSFER    (BOARD_LCD_H_RES * DRAW_BUF_LINES * sizeof(uint16_t))

/* Vendor init sequence for this panel [WS-BSP lcd_init_cmds]. */
static const co5300_lcd_init_cmd_t s_lcd_init_cmds[] = {
    {0x11, (uint8_t[]){0x00}, 0, 120},              /* sleep out */
    {0xC4, (uint8_t[]){0x80}, 1, 0},                /* QSPI mode */
    {0x44, (uint8_t[]){0x01, 0xD1}, 2, 0},          /* tear scanline */
    {0x35, (uint8_t[]){0x00}, 1, 0},                /* TE on */
    {0x53, (uint8_t[]){0x20}, 1, 10},               /* brightness control on */
    {0x63, (uint8_t[]){0xFF}, 1, 10},               /* HBM brightness */
    {0x51, (uint8_t[]){0x00}, 1, 10},               /* brightness 0 until first frame */
    {0x2A, (uint8_t[]){0x00, 0x16, 0x01, 0xAF}, 4, 0},  /* columns 22..431 */
    {0x2B, (uint8_t[]){0x00, 0x00, 0x01, 0xF5}, 4, 0},  /* rows 0..501 */
    {0x29, (uint8_t[]){0x00}, 0, 10},               /* display on */
};

/* CO5300 requires even start / odd end coordinates for partial updates. */
static void rounder_cb(lv_area_t *area)
{
    area->x1 = (area->x1 >> 1) << 1;
    area->y1 = (area->y1 >> 1) << 1;
    area->x2 = ((area->x2 >> 1) << 1) + 1;
    area->y2 = ((area->y2 >> 1) << 1) + 1;
}

static esp_err_t panel_init(void)
{
    const spi_bus_config_t buscfg = CO5300_PANEL_BUS_QSPI_CONFIG(
        BOARD_LCD_PCLK, BOARD_LCD_D0, BOARD_LCD_D1, BOARD_LCD_D2, BOARD_LCD_D3, SPI_MAX_TRANSFER);
    ESP_RETURN_ON_ERROR(spi_bus_initialize(BOARD_LCD_SPI_HOST, &buscfg, SPI_DMA_CH_AUTO), TAG, "spi bus");

    esp_lcd_panel_io_spi_config_t io_cfg = CO5300_PANEL_IO_QSPI_CONFIG(BOARD_LCD_CS, NULL, NULL);
    io_cfg.pclk_hz = BOARD_LCD_PCLK_HZ;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)BOARD_LCD_SPI_HOST, &io_cfg, &s_io),
                        TAG, "panel io");

    co5300_vendor_config_t vendor_cfg = {
        .init_cmds = s_lcd_init_cmds,
        .init_cmds_size = sizeof(s_lcd_init_cmds) / sizeof(s_lcd_init_cmds[0]),
        .flags = {
            .use_qspi_interface = 1,
        },
    };
    esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = BOARD_LCD_RST,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
        .vendor_config = &vendor_cfg,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_co5300(s_io, &panel_cfg, &s_panel), TAG, "co5300");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_panel), TAG, "reset");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_panel), TAG, "init");
    esp_lcd_panel_set_gap(s_panel, BOARD_LCD_X_GAP, BOARD_LCD_Y_GAP);
    esp_lcd_panel_disp_on_off(s_panel, true);
    return ESP_OK;
}

static esp_err_t touch_init(void)
{
    esp_lcd_panel_io_handle_t tp_io = NULL;
    esp_lcd_panel_io_i2c_config_t tp_io_cfg = ESP_LCD_TOUCH_IO_I2C_FT5x06_CONFIG();
    tp_io_cfg.scl_speed_hz = BOARD_I2C_FREQ_HZ;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i2c(board_i2c_bus(), &tp_io_cfg, &tp_io), TAG, "touch io");

    const esp_lcd_touch_config_t tp_cfg = {
        .x_max = BOARD_LCD_H_RES,
        .y_max = BOARD_LCD_V_RES,
        .rst_gpio_num = BOARD_TOUCH_RST,
        .int_gpio_num = BOARD_TOUCH_INT,
        .levels = {
            .reset = 0,
            .interrupt = 0,
        },
        .flags = {
            .swap_xy = 0,
            .mirror_x = 0,
            .mirror_y = 0,
        },
    };
    return esp_lcd_touch_new_i2c_ft5x06(tp_io, &tp_cfg, &s_touch);
}

lv_display_t *board_display_init(void)
{
    if (s_disp) {
        return s_disp;
    }
    if (panel_init() != ESP_OK) {
        ESP_LOGE(TAG, "panel init failed");
        return NULL;
    }

    /* LVGL task on core 0 (audio owns core 1). */
    lvgl_port_cfg_t port_cfg = ESP_LVGL_PORT_INIT_CONFIG();
    port_cfg.task_priority = 4;
    port_cfg.task_stack = 8192;
    port_cfg.task_affinity = 0;
    port_cfg.task_max_sleep_ms = 100;
    port_cfg.timer_period_ms = 5;
    if (lvgl_port_init(&port_cfg) != ESP_OK) {
        ESP_LOGE(TAG, "lvgl port init failed");
        return NULL;
    }

    const lvgl_port_display_cfg_t disp_cfg = {
        .io_handle = s_io,
        .panel_handle = s_panel,
        .buffer_size = BOARD_LCD_H_RES * DRAW_BUF_LINES,
        .double_buffer = true,
        .hres = BOARD_LCD_H_RES,
        .vres = BOARD_LCD_V_RES,
        .monochrome = false,
        .rotation = {
            .swap_xy = false,
            .mirror_x = false,
            .mirror_y = false,
        },
        .rounder_cb = rounder_cb,
        .color_format = LV_COLOR_FORMAT_RGB565,
        .flags = {
            .buff_dma = true,
            .buff_spiram = false,
            .sw_rotate = false,
            .swap_bytes = true,
        },
    };
    s_disp = lvgl_port_add_disp(&disp_cfg);
    if (!s_disp) {
        ESP_LOGE(TAG, "lvgl add disp failed");
        return NULL;
    }

    if (touch_init() == ESP_OK) {
        const lvgl_port_touch_cfg_t touch_cfg = {
            .disp = s_disp,
            .handle = s_touch,
        };
        if (!lvgl_port_add_touch(&touch_cfg)) {
            ESP_LOGE(TAG, "lvgl add touch failed");
        }
    } else {
        ESP_LOGE(TAG, "touch init failed");
    }

    ESP_LOGI(TAG, "display %dx%d ready", BOARD_LCD_H_RES, BOARD_LCD_V_RES);
    return s_disp;
}

esp_err_t board_display_set_brightness(int percent)
{
    if (!s_panel) {
        return ESP_ERR_INVALID_STATE;
    }
    if (percent < 0) {
        percent = 0;
    } else if (percent > 100) {
        percent = 100;
    }
    return esp_lcd_panel_co5300_set_brightness(s_panel, (uint8_t)percent);
}

esp_err_t board_display_power(bool on)
{
    if (!s_panel) {
        return ESP_ERR_INVALID_STATE;
    }
    return esp_lcd_panel_disp_on_off(s_panel, on);
}

bool board_display_lock(uint32_t timeout_ms)
{
    return lvgl_port_lock(timeout_ms);
}

void board_display_unlock(void)
{
    lvgl_port_unlock();
}
