/*
 * ola - development only: the current screen as an image over Wi-Fi.
 *
 *   GET http://<watch-ip>:8080/snap  ->  image/bmp, 410 x 502, 24 bit
 *
 * Used to compare the watch with the web app's preview and to check layouts
 * without a camera. Never built into release firmware (CONFIG_BUDDYAI_RELEASE_BUILD).
 */
#include "sdkconfig.h"

#if !CONFIG_BUDDYAI_RELEASE_BUILD

#include <string.h>
#include "esp_http_server.h"
#include "esp_log.h"
#include "lvgl.h"
#include "board.h"
#include "debug_snap.h"

static const char *TAG = "snap";
static httpd_handle_t s_httpd;

static void put_le32(uint8_t *p, uint32_t v)
{
    p[0] = v & 0xff;
    p[1] = (v >> 8) & 0xff;
    p[2] = (v >> 16) & 0xff;
    p[3] = (v >> 24) & 0xff;
}

static esp_err_t snap_get(httpd_req_t *req)
{
    if (!board_display_lock(2000)) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "display busy");
    }
    lv_draw_buf_t *buf = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_RGB565);
    board_display_unlock();
    if (!buf) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "no memory for the snapshot");
    }
    const uint32_t w = buf->header.w, h = buf->header.h, stride = buf->header.stride;
    const uint32_t row_bytes = (w * 3 + 3) & ~3u;

    uint8_t hdr[54] = { 'B', 'M' };
    put_le32(hdr + 2, 54 + row_bytes * h);
    put_le32(hdr + 10, 54);
    put_le32(hdr + 14, 40);
    put_le32(hdr + 18, w);
    put_le32(hdr + 22, h);          /* positive: rows bottom-up */
    hdr[26] = 1;
    hdr[28] = 24;
    put_le32(hdr + 34, row_bytes * h);
    httpd_resp_set_type(req, "image/bmp");
    httpd_resp_send_chunk(req, (const char *)hdr, sizeof(hdr));

    static uint8_t row[1240];
    esp_err_t err = ESP_OK;
    for (int32_t y = (int32_t)h - 1; y >= 0 && err == ESP_OK; y--) {
        const uint16_t *src = (const uint16_t *)(buf->data + (uint32_t)y * stride);
        memset(row, 0, row_bytes);
        for (uint32_t x = 0; x < w && x * 3 + 2 < sizeof(row); x++) {
            uint16_t c = src[x];
            uint8_t r = (c >> 11) & 0x1f, g = (c >> 5) & 0x3f, b = c & 0x1f;
            row[x * 3 + 0] = (uint8_t)((b << 3) | (b >> 2));
            row[x * 3 + 1] = (uint8_t)((g << 2) | (g >> 4));
            row[x * 3 + 2] = (uint8_t)((r << 3) | (r >> 2));
        }
        err = httpd_resp_send_chunk(req, (const char *)row, row_bytes);
    }
    httpd_resp_send_chunk(req, NULL, 0);
    lv_draw_buf_destroy(buf);
    return err;
}

void debug_snap_start(void)
{
    if (s_httpd) {
        return;
    }
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.server_port = 8080;
    cfg.ctrl_port = 32769;      /* the setup portal uses the default */
    cfg.stack_size = 6144;
    if (httpd_start(&s_httpd, &cfg) != ESP_OK) {
        ESP_LOGW(TAG, "debug server not started");
        s_httpd = NULL;
        return;
    }
    static const httpd_uri_t uri = { .uri = "/snap", .method = HTTP_GET, .handler = snap_get };
    httpd_register_uri_handler(s_httpd, &uri);
    ESP_LOGI(TAG, "screen snapshots: http://<watch-ip>:8080/snap (development build)");
}

#else

void debug_snap_start(void)
{
}

#endif
