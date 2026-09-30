/*
 * ola - device side of protocol/PROTOCOL.md v1, see protocol_client.h
 */
#include "protocol_client.h"

#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <sys/time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_crt_bundle.h"
#include "sdkconfig.h"
#include "audio.h"
#include "net.h"
#include "ota.h"
#include "settings.h"

static const char *TAG = "proto";

/* ---- timing (ms) ---- */
#define PING_INTERVAL_MS        15000
#define STATUS_INTERVAL_MS      60000
#define CONNECT_TIMEOUT_MS      10000
#define HELLO_TIMEOUT_MS        15000
#define THINKING_TIMEOUT_MS     30000
#define SPEAKING_IDLE_MS        20000
#define BACKOFF_MIN_MS          2000
#define BACKOFF_MAX_MS          120000
#define BACKOFF_UNSUPPORTED_MS  (10 * 60 * 1000)
#define BACKOFF_ACCOUNT_INACTIVE_MS (10 * 60 * 1000)
#define MDNS_TIMEOUT_MS         3000

#define AUDIO_HDR_LEN           12
#define AUDIO_KIND_UPLINK       0x01
#define AUDIO_KIND_DOWNLINK     0x02
#define AUDIO_CODEC_OPUS        0

#define WS_BUFFER_SIZE          4096
#define MAX_TEXT_MSG            (32 * 1024)
#define MAX_CANDIDATES          4

/* ------------------------------------------------------------------------- */
/* Messages to the proto task                                                 */
/* ------------------------------------------------------------------------- */

typedef enum {
    MSG_NET_UP,
    MSG_NET_DOWN,
    MSG_RECONNECT,
    MSG_WS_CONNECTED,       /* a = conn id */
    MSG_WS_CLOSED,          /* a = conn id */
    MSG_TEXT,               /* a = conn id, str = JSON (owned) */
    MSG_FIRST_AUDIO,        /* a = turn id, t = receive time ms */
    MSG_PLAYBACK_DONE,      /* a = turn id */
    MSG_TAP,
    MSG_SETTINGS_CHANGED,   /* str = JSON changes (owned) */
    MSG_OTA_STATUS,         /* a = state, b = pct */
    MSG_END_CONVERSATION,   /* user left the conversation screen */
    MSG_ITEM_OPEN,          /* a = number, b = 1 for a reminder */
    MSG_ITEM_DELETE,        /* a = number, b = 1 for a reminder */
    MSG_ITEM_DONE,          /* a = reminder number, b = 1 completed / 0 open again */
} msg_type_t;

typedef struct {
    msg_type_t type;
    uint32_t   a;
    int32_t    b;
    int64_t    t;
    char      *str;
} msg_t;

typedef enum {
    CONN_NO_NET,
    CONN_BACKOFF,
    CONN_CONNECTING,
    CONN_HELLO,             /* socket open, waiting for hello_ack / pairing */
    CONN_SESSION,
} conn_state_t;

/* ------------------------------------------------------------------------- */
/* State (owned by the proto task unless noted)                               */
/* ------------------------------------------------------------------------- */

static proto_config_t   s_cfg;
static QueueHandle_t    s_q;

static esp_websocket_client_handle_t s_ws;
static volatile uint32_t s_conn_id;         /* id of the live client (read in ws task) */
static conn_state_t     s_conn = CONN_NO_NET;
static int64_t          s_deadline_ms;      /* connect / hello / backoff deadline */
static uint32_t         s_backoff_ms = BACKOFF_MIN_MS;
/* Minimum backoff until the next successful hello_ack (protocol_unsupported,
 * account_inactive): the server refuses us, so do not hammer it. */
static uint32_t         s_backoff_floor_ms;
static bool             s_account_inactive; /* last hello refused with account_inactive */

static char             s_candidates[MAX_CANDIDATES][SETTINGS_URL_MAX];
static int              s_cand_count;
static int              s_cand_idx;
static bool             s_mdns_tried;
static char             s_url[SETTINGS_URL_MAX];

static SemaphoreHandle_t s_send_lock;       /* envelope build + send atomically */
static char             s_session_id[64];
static uint32_t         s_seq_out;
static int64_t          s_last_ping_ms;
static int64_t          s_last_status_ms;
static int64_t          s_ping_sent_ms;

static char             s_pairing_code[8];
static bool             s_paired_token;     /* hello was sent with a token */

/* Turn state. s_turn_lock guards the fields the ws task reads for binary frames. */
static SemaphoreHandle_t s_turn_lock;
static uint32_t         s_turn_counter;     /* last turn id issued this session */
static volatile uint32_t s_active_turn;
static volatile bool    s_turn_live;        /* active turn not aborted/finished */
static volatile bool    s_first_audio_seen;
static volatile proto_conv_state_t s_conv = PROTO_CONV_IDLE;
static bool             s_tts_ended;
static bool             s_turn_ended;
static int64_t          s_turn_deadline_ms;

static cJSON           *s_pending_changes;  /* local settings changes not yet sent */

/* ws receive reassembly (ws task only) */
static uint8_t         *s_rx_buf;
static size_t           s_rx_cap;
static size_t           s_rx_len;
static int              s_rx_opcode;

/* ------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* ------------------------------------------------------------------------- */

static int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

/* Wall clock for envelope timestamps; 0 while the clock is not set. */
static int64_t wall_ms(void)
{
    if (!net_time_valid()) {
        return 0;
    }
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

static void emit(proto_event_type_t type, int num, const char *str)
{
    if (s_cfg.on_event) {
        proto_event_t ev = { .type = type, .num = num, .str = str };
        s_cfg.on_event(&ev, s_cfg.ctx);
    }
}

static void post(const msg_t *m)
{
    if (xQueueSend(s_q, m, pdMS_TO_TICKS(100)) != pdTRUE) {
        ESP_LOGW(TAG, "queue full, dropping msg %d", m->type);
        free(m->str);
    }
}

static char *dup_psram(const char *s, size_t len)
{
    char *d = heap_caps_malloc(len + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!d) {
        d = malloc(len + 1);
    }
    if (d) {
        memcpy(d, s, len);
        d[len] = '\0';
    }
    return d;
}

static uint32_t rd_be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

static void wr_be32(uint8_t *p, uint32_t v)
{
    p[0] = v >> 24;
    p[1] = v >> 16;
    p[2] = v >> 8;
    p[3] = v;
}

static bool json_uint32(const cJSON *obj, const char *key, uint32_t *out)
{
    const cJSON *it = cJSON_GetObjectItemCaseSensitive(obj, key);
    if (!cJSON_IsNumber(it) || it->valuedouble < 0 || it->valuedouble > 4294967295.0) {
        return false;
    }
    *out = (uint32_t)it->valuedouble;
    return true;
}

static const char *json_str(const cJSON *obj, const char *key)
{
    const cJSON *it = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsString(it) ? it->valuestring : NULL;
}

static void set_conv(proto_conv_state_t st)
{
    if (s_conv != st) {
        s_conv = st;
        emit(PROTO_EVT_CONV_STATE, st, NULL);
        /* Modem sleep adds latency; keep the radio awake during a turn. */
        net_wifi_set_power_save(st == PROTO_CONV_IDLE);
    }
}

/* ------------------------------------------------------------------------- */
/* Sending                                                                    */
/* ------------------------------------------------------------------------- */

/* Adds the envelope (PROTOCOL.md section 2) to `msg`, sends it and deletes it.
 * `ts_ms` < 0 means "now". */
static esp_err_t send_json_at(cJSON *msg, const char *type, bool with_turn, uint32_t turn_id, int64_t ts_ms)
{
    esp_err_t ret = ESP_FAIL;
    xSemaphoreTake(s_send_lock, portMAX_DELAY);
    if (s_ws && esp_websocket_client_is_connected(s_ws)) {
        cJSON_AddStringToObject(msg, "type", type);
        cJSON_AddNumberToObject(msg, "protocol_version", PROTO_VERSION);
        if (s_session_id[0]) {
            cJSON_AddStringToObject(msg, "session_id", s_session_id);
        } else {
            cJSON_AddNullToObject(msg, "session_id");
        }
        if (with_turn) {
            cJSON_AddNumberToObject(msg, "turn_id", turn_id);
        } else {
            cJSON_AddNullToObject(msg, "turn_id");
        }
        cJSON_AddNumberToObject(msg, "sequence_number", ++s_seq_out);
        cJSON_AddNumberToObject(msg, "timestamp", (double)(ts_ms >= 0 ? ts_ms : wall_ms()));
        char *txt = cJSON_PrintUnformatted(msg);
        if (txt) {
            int n = esp_websocket_client_send_text(s_ws, txt, strlen(txt), pdMS_TO_TICKS(2000));
            ret = n >= 0 ? ESP_OK : ESP_FAIL;
            ESP_LOGD(TAG, "-> %s", txt);
            cJSON_free(txt);
        }
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "send %s failed", type);
        }
    }
    xSemaphoreGive(s_send_lock);
    cJSON_Delete(msg);
    return ret;
}

static esp_err_t send_json(cJSON *msg, const char *type, bool with_turn, uint32_t turn_id)
{
    return send_json_at(msg, type, with_turn, turn_id, -1);
}

static void send_simple(const char *type)
{
    send_json(cJSON_CreateObject(), type, false, 0);
}

static void send_turn_msg(const char *type, uint32_t turn_id, const char *reason)
{
    cJSON *m = cJSON_CreateObject();
    if (reason) {
        cJSON_AddStringToObject(m, "reason", reason);
    }
    send_json(m, type, true, turn_id);
}

static void new_pairing_code(void)
{
    snprintf(s_pairing_code, sizeof(s_pairing_code), "%06lu", (unsigned long)(esp_random() % 1000000UL));
}

static void send_hello(void)
{
    char token[SETTINGS_TOKEN_MAX];
    cJSON *m = cJSON_CreateObject();
    cJSON_AddStringToObject(m, "device_id", settings_device_id());
    cJSON_AddStringToObject(m, "fw_version", s_cfg.fw_version ? s_cfg.fw_version : "0.0.0");
    cJSON_AddStringToObject(m, "hw_model", s_cfg.hw_model ? s_cfg.hw_model : CONFIG_BUDDYAI_HW_MODEL);
    if (settings_get_token(token, sizeof(token))) {
        cJSON_AddStringToObject(m, "token", token);
        s_paired_token = true;
    } else {
        if (!s_pairing_code[0]) {
            new_pairing_code();
        }
        cJSON_AddStringToObject(m, "pairing_code", s_pairing_code);
        s_paired_token = false;
    }
    cJSON *audio = cJSON_AddObjectToObject(m, "audio");
    cJSON_AddNumberToObject(audio, "uplink_rate", AUDIO_UPLINK_SAMPLE_RATE);
    cJSON *rates = cJSON_AddArrayToObject(audio, "downlink_rates");
    cJSON_AddItemToArray(rates, cJSON_CreateNumber(16000));
    cJSON_AddItemToArray(rates, cJSON_CreateNumber(24000));
    memset(token, 0, sizeof(token));
    ESP_LOGI(TAG, "hello (%s)", s_paired_token ? "token" : "pairing code");
    send_json(m, "hello", false, 0);
}

static void send_status(void)
{
    int batt = -1;
    bool charging = false;
    if (s_cfg.get_status) {
        s_cfg.get_status(&batt, &charging);
    }
    cJSON *m = cJSON_CreateObject();
    cJSON_AddNumberToObject(m, "battery_pct", batt);
    cJSON_AddBoolToObject(m, "charging", charging);
    cJSON_AddNumberToObject(m, "rssi", net_wifi_rssi());
    cJSON_AddNumberToObject(m, "free_heap", esp_get_free_heap_size());
    send_json(m, "status", false, 0);
}

static void send_settings_changes(void)
{
    if (!s_pending_changes || s_conn != CONN_SESSION) {
        return;
    }
    cJSON *m = cJSON_CreateObject();
    cJSON_AddNumberToObject(m, "base_version", settings_get_version());
    cJSON_AddItemToObject(m, "changes", s_pending_changes);
    s_pending_changes = NULL;
    send_json(m, "settings_changed", false, 0);
}

/* Uplink audio (called from the audio capture task). */
static void capture_cb(uint32_t turn_id, uint32_t frame_seq, const uint8_t *opus, size_t len, void *ctx)
{
    if (turn_id != s_active_turn || !s_turn_live || s_conv != PROTO_CONV_LISTENING) {
        return;
    }
    uint8_t frame[AUDIO_HDR_LEN + 1500];
    if (len > sizeof(frame) - AUDIO_HDR_LEN) {
        return;
    }
    frame[0] = AUDIO_KIND_UPLINK;
    frame[1] = 0;
    frame[2] = (AUDIO_CODEC_OPUS >> 8) & 0xFF;
    frame[3] = AUDIO_CODEC_OPUS & 0xFF;
    wr_be32(frame + 4, turn_id);
    wr_be32(frame + 8, frame_seq);
    memcpy(frame + AUDIO_HDR_LEN, opus, len);
    /* The send lock also keeps the client alive (ws_destroy takes it). */
    xSemaphoreTake(s_send_lock, portMAX_DELAY);
    if (s_ws && esp_websocket_client_is_connected(s_ws)) {
        esp_websocket_client_send_bin(s_ws, (const char *)frame, AUDIO_HDR_LEN + len, pdMS_TO_TICKS(200));
    }
    xSemaphoreGive(s_send_lock);
}

static void playback_done_cb(uint32_t turn_id, void *ctx)
{
    msg_t m = { .type = MSG_PLAYBACK_DONE, .a = turn_id };
    post(&m);
}

/* ------------------------------------------------------------------------- */
/* Turn management (PROTOCOL.md sections 2 and 6)                             */
/* ------------------------------------------------------------------------- */

/* Mark the active turn dead; any later frame for it is dropped. */
static void kill_turn_locked(void)
{
    xSemaphoreTake(s_turn_lock, portMAX_DELAY);
    s_turn_live = false;
    xSemaphoreGive(s_turn_lock);
}

static void local_stop_turn(void)
{
    kill_turn_locked();
    audio_capture_stop();
    audio_playback_flush();
}

static void abort_turn(const char *reason)
{
    uint32_t turn = s_active_turn;
    bool live = s_turn_live;
    local_stop_turn();
    if (live) {
        ESP_LOGI(TAG, "abort turn %lu (%s)", (unsigned long)turn, reason);
        send_turn_msg("abort", turn, reason);
    }
}

static void start_turn(void)
{
    buddy_settings_t st;
    settings_get(&st);

    /* Stop and flush first so nothing of the previous reply can play. */
    audio_playback_flush();
    xSemaphoreTake(s_turn_lock, portMAX_DELAY);
    s_active_turn = ++s_turn_counter;
    s_turn_live = true;
    s_first_audio_seen = false;
    xSemaphoreGive(s_turn_lock);
    s_tts_ended = false;
    s_turn_ended = false;

    uint32_t turn = s_active_turn;
    ESP_LOGI(TAG, "listen_start turn %lu", (unsigned long)turn);
    cJSON *m = cJSON_CreateObject();
    cJSON_AddStringToObject(m, "language", st.language);
    send_json(m, "listen_start", true, turn);

    set_conv(PROTO_CONV_LISTENING);
    s_turn_deadline_ms = now_ms() + (int64_t)(st.max_listen_s + 2) * 1000;
    audio_capture_start(turn, capture_cb, NULL);
}

static void handle_tap(void)
{
    if (s_conn != CONN_SESSION) {
        emit(PROTO_EVT_ERROR, s_account_inactive ? PROTO_ERR_ACCOUNT_INACTIVE : PROTO_ERR_NOT_CONNECTED, NULL);
        return;
    }
    switch (s_conv) {
    case PROTO_CONV_IDLE:
        start_turn();
        break;
    case PROTO_CONV_LISTENING:
        /* Tap while listening cancels the turn. */
        abort_turn("user_tap");
        set_conv(PROTO_CONV_IDLE);
        break;
    case PROTO_CONV_THINKING:
    case PROTO_CONV_SPEAKING:
        /* Tap-to-interrupt: stop + flush, abort, immediately start turn+1. */
        abort_turn("user_tap");
        start_turn();
        break;
    }
}

static void finish_turn_idle(void)
{
    kill_turn_locked();
    audio_capture_stop();
    set_conv(PROTO_CONV_IDLE);
}

/* ------------------------------------------------------------------------- */
/* Incoming messages                                                          */
/* ------------------------------------------------------------------------- */

static void on_hello_ack(const cJSON *j)
{
    const char *sid = json_str(j, "session_id");
    strlcpy(s_session_id, sid ? sid : "", sizeof(s_session_id));

    const cJSON *st = cJSON_GetObjectItemCaseSensitive(j, "server_time");
    if (cJSON_IsNumber(st)) {
        int64_t server_ms = (int64_t)st->valuedouble;
        int64_t offset = server_ms - wall_ms();
        if (!net_time_valid()) {
            struct timeval tv = { .tv_sec = server_ms / 1000, .tv_usec = (server_ms % 1000) * 1000 };
            settimeofday(&tv, NULL);
            ESP_LOGI(TAG, "clock set from server_time");
            emit(PROTO_EVT_TIME_SET, 0, NULL);
        } else {
            ESP_LOGI(TAG, "clock offset vs server: %lld ms", (long long)offset);
        }
    }

    uint32_t ver = 0;
    json_uint32(j, "settings_version", &ver);
    const cJSON *settings = cJSON_GetObjectItemCaseSensitive(j, "settings");
    if (cJSON_IsObject(settings)) {
        settings_apply_server(settings, ver);
    }
    uint32_t dl = 0;
    if (json_uint32(j, "downlink_rate", &dl)) {
        ESP_LOGI(TAG, "downlink rate %lu Hz (decoded to %d Hz)", (unsigned long)dl, 16000);
    }

    s_conn = CONN_SESSION;
    s_turn_counter = 0;
    s_backoff_ms = BACKOFF_MIN_MS;
    s_backoff_floor_ms = 0;
    s_account_inactive = false;
    s_last_ping_ms = s_last_status_ms = now_ms();
    s_pairing_code[0] = '\0';
    settings_set_last_server(s_url);
    ota_mark_app_valid();
    ESP_LOGI(TAG, "session %s established with %s", s_session_id, s_url);
    emit(PROTO_EVT_SESSION_READY, 0, NULL);

    /* Changes made while offline: re-apply on top of the server state, send. */
    if (s_pending_changes) {
        settings_apply_local(s_pending_changes);
        send_settings_changes();
    }
    send_status();
}

static void on_error_msg(const cJSON *j, bool has_turn, uint32_t turn)
{
    const char *code = json_str(j, "code");
    const char *message = json_str(j, "message");
    ESP_LOGW(TAG, "server error %s: %s", code ? code : "?", message ? message : "");
    if (!code) {
        return;
    }
    if (strcmp(code, "protocol_unsupported") == 0) {
        emit(PROTO_EVT_ERROR, PROTO_ERR_PROTOCOL_UNSUPPORTED, message);
        s_backoff_floor_ms = BACKOFF_UNSUPPORTED_MS;    /* stop reconnecting fast */
        if (s_ws) {
            esp_websocket_client_close(s_ws, pdMS_TO_TICKS(1000));
        }
    } else if (strcmp(code, "account_inactive") == 0) {
        /* Reply to hello; the server closes the socket. Keep the token (the
         * account may be reactivated) and retry slowly. */
        s_account_inactive = true;
        s_backoff_floor_ms = BACKOFF_ACCOUNT_INACTIVE_MS;
        local_stop_turn();
        set_conv(PROTO_CONV_IDLE);
        emit(PROTO_EVT_ERROR, PROTO_ERR_ACCOUNT_INACTIVE, message);
        if (s_ws) {
            esp_websocket_client_close(s_ws, pdMS_TO_TICKS(1000));
        }
    } else if (strcmp(code, "subscription_required") == 0 || strcmp(code, "limit_reached") == 0) {
        /* Reply to listen_start (turn_end {status: error} follows). The uplink
         * was already cut in the websocket task (uplink_refused_fast_path);
         * stop capture, go idle, no automatic retry. */
        if (has_turn && turn != s_active_turn) {
            return; /* stale turn */
        }
        local_stop_turn();
        set_conv(PROTO_CONV_IDLE);
        emit(PROTO_EVT_ERROR, code[0] == 's' ? PROTO_ERR_SUBSCRIPTION_REQUIRED : PROTO_ERR_LIMIT_REACHED, message);
    } else if (strcmp(code, "unauthorized") == 0) {
        settings_erase_token();
        local_stop_turn();
        set_conv(PROTO_CONV_IDLE);
        s_session_id[0] = '\0';
        s_conn = CONN_HELLO;
        s_deadline_ms = now_ms() + HELLO_TIMEOUT_MS;
        emit(PROTO_EVT_ERROR, PROTO_ERR_UNAUTHORIZED, message);
        new_pairing_code();
        send_hello();
    } else if (strcmp(code, "pairing_expired") == 0) {
        new_pairing_code();
        send_hello();
    } else if (strcmp(code, "bad_request") == 0) {
        /* log only */
    } else if (strcmp(code, "busy") == 0) {
        if (!has_turn || turn == s_active_turn) {
            local_stop_turn();
            set_conv(PROTO_CONV_IDLE);
        }
        emit(PROTO_EVT_ERROR, PROTO_ERR_BUSY, message);
    } else {
        /* stt_failed / llm_failed / tts_failed / internal / unknown */
        if (has_turn && turn != s_active_turn) {
            return; /* stale turn */
        }
        local_stop_turn();
        set_conv(PROTO_CONV_IDLE);
        emit(PROTO_EVT_ERROR, PROTO_ERR_AI, message);
    }
}

static void on_ota_status(ota_state_t state, int pct, void *ctx)
{
    msg_t m = { .type = MSG_OTA_STATUS, .a = state, .b = pct };
    post(&m);
}

static void handle_text(const char *txt)
{
    cJSON *j = cJSON_Parse(txt);
    if (!j) {
        ESP_LOGW(TAG, "bad JSON from server");
        return;
    }
    const char *type = json_str(j, "type");
    if (!type) {
        goto out;
    }
    const cJSON *pv = cJSON_GetObjectItemCaseSensitive(j, "protocol_version");
    if (cJSON_IsNumber(pv) && pv->valueint != PROTO_VERSION && strcmp(type, "error") != 0) {
        ESP_LOGW(TAG, "server protocol_version %d != %d", pv->valueint, PROTO_VERSION);
    }
    uint32_t turn = 0;
    bool has_turn = json_uint32(j, "turn_id", &turn);
    ESP_LOGD(TAG, "<- %s turn=%ld", type, has_turn ? (long)turn : -1L);

    /* ---- session-level messages ---- */
    if (strcmp(type, "hello_ack") == 0) {
        on_hello_ack(j);
        goto out;
    }
    if (strcmp(type, "pairing_pending") == 0) {
        uint32_t exp = 300;
        json_uint32(j, "expires_in_s", &exp);
        ESP_LOGI(TAG, "pairing pending, code %s (%lu s)", s_pairing_code, (unsigned long)exp);
        s_deadline_ms = now_ms() + (int64_t)(exp + 30) * 1000;  /* waiting for admin */
        emit(PROTO_EVT_PAIRING, (int)exp, s_pairing_code);
        goto out;
    }
    if (strcmp(type, "paired") == 0) {
        const char *tok = json_str(j, "device_token");
        if (tok && tok[0] && strlen(tok) < SETTINGS_TOKEN_MAX) {
            settings_set_token(tok);
            ESP_LOGI(TAG, "paired - token stored");
            emit(PROTO_EVT_PAIRED, 0, NULL);
            s_deadline_ms = now_ms() + HELLO_TIMEOUT_MS;
            send_hello();
        }
        goto out;
    }
    if (strcmp(type, "settings_update") == 0) {
        uint32_t ver = 0;
        json_uint32(j, "settings_version", &ver);
        settings_apply_server(cJSON_GetObjectItemCaseSensitive(j, "settings"), ver);
        goto out;
    }
    if (strcmp(type, "ota_available") == 0) {
        uint32_t size = 0;
        json_uint32(j, "size", &size);
        const char *ver = json_str(j, "version");
        if (ver && s_cfg.fw_version && strcmp(ver, s_cfg.fw_version) == 0) {
            ESP_LOGI(TAG, "ota_available %s = running version, ignored", ver);
        } else if (ota_start(json_str(j, "url"), ver, json_str(j, "sha256"), size, on_ota_status, NULL) == ESP_OK) {
            emit(PROTO_EVT_OTA, 0, ver);
        }
        goto out;
    }
    if (strcmp(type, "pong") == 0) {
        if (s_ping_sent_ms) {
            ESP_LOGD(TAG, "rtt %lld ms", (long long)(now_ms() - s_ping_sent_ms));
        }
        goto out;
    }
    if (strcmp(type, "error") == 0) {
        on_error_msg(j, has_turn, turn);
        goto out;
    }
    /* ---- notes / reminders (turn_id null) ---- */
    if (strcmp(type, "items") == 0) {
        emit(PROTO_EVT_ITEMS, 0, txt);
        goto out;
    }
    if (strcmp(type, "languages") == 0) {
        emit(PROTO_EVT_LANGUAGES, 0, txt);
        goto out;
    }
    if (strcmp(type, "items_open") == 0) {
        const char *kind = json_str(j, "kind");
        emit(PROTO_EVT_ITEMS_OPEN, kind && strcmp(kind, "reminder") == 0, NULL);
        goto out;
    }
    if (strcmp(type, "item_show") == 0) {
        emit(PROTO_EVT_ITEM_SHOW, 0, txt);
        goto out;
    }
    if (strcmp(type, "reminder_fire") == 0) {
        emit(PROTO_EVT_REMINDER, 0, txt);
        goto out;
    }
    if (strcmp(type, "notice") == 0) {
        emit(PROTO_EVT_NOTICE, 0, txt);
        goto out;
    }

    /* ---- turn messages ---- */
    if (strcmp(type, "turn_end") == 0) {
        /* Bookkeeping is allowed for any turn (section 6.1). */
        const char *status = json_str(j, "status");
        ESP_LOGI(TAG, "turn_end %lu: %s", (unsigned long)turn, status ? status : "?");
        if (has_turn && turn == s_active_turn && s_turn_live) {
            s_turn_ended = true;
            bool completed = status && strcmp(status, "completed") == 0;
            if (!completed || (!s_first_audio_seen && !audio_playback_active())) {
                finish_turn_idle();
            }
        }
        goto out;
    }
    if (!has_turn || turn != s_active_turn || !s_turn_live) {
        ESP_LOGD(TAG, "drop stale %s (turn %lu, active %lu)", type, (unsigned long)turn,
                 (unsigned long)s_active_turn);
        goto out;
    }

    if (strcmp(type, "listen_stop") == 0) {
        audio_capture_stop();
        if (s_conv == PROTO_CONV_LISTENING) {
            set_conv(PROTO_CONV_THINKING);
            s_turn_deadline_ms = now_ms() + THINKING_TIMEOUT_MS;
        }
    } else if (strcmp(type, "state") == 0) {
        const char *st = json_str(j, "state");
        if (!st) {
            goto out;
        }
        if (strcmp(st, "thinking") == 0 && s_conv == PROTO_CONV_LISTENING) {
            audio_capture_stop();
            set_conv(PROTO_CONV_THINKING);
            s_turn_deadline_ms = now_ms() + THINKING_TIMEOUT_MS;
        } else if (strcmp(st, "speaking") == 0 && s_conv != PROTO_CONV_LISTENING) {
            set_conv(PROTO_CONV_SPEAKING);
            s_turn_deadline_ms = now_ms() + SPEAKING_IDLE_MS;
        } else if (strcmp(st, "idle") == 0 && !audio_playback_active() && s_conv != PROTO_CONV_LISTENING) {
            finish_turn_idle();
        }
    } else if (strcmp(type, "stt_result") == 0) {
        const char *text = json_str(j, "text");
        if (text) {
            emit(PROTO_EVT_TRANSCRIPT, cJSON_IsTrue(cJSON_GetObjectItem(j, "final")), text);
        }
        const char *lang = json_str(j, "language");     /* optional, detected language */
        if (lang && lang[0]) {
            emit(PROTO_EVT_REPLY_LANGUAGE, 0, lang);
        }
    } else if (strcmp(type, "llm_display") == 0) {
        const char *text = json_str(j, "text");
        if (text && text[0]) {
            emit(PROTO_EVT_REPLY_DISPLAY, 0, text);
        }
    } else if (strcmp(type, "llm_text") == 0) {
        const char *delta = json_str(j, "delta");
        if (delta) {
            emit(PROTO_EVT_REPLY_DELTA, 0, delta);
        }
        s_turn_deadline_ms = now_ms() + SPEAKING_IDLE_MS;
    } else if (strcmp(type, "tts_start") == 0) {
        uint32_t rate = 0;
        json_uint32(j, "sample_rate", &rate);
        const char *lang = json_str(j, "language");     /* optional, reply language */
        ESP_LOGI(TAG, "tts_start turn %lu @ %lu Hz lang=%s", (unsigned long)turn, (unsigned long)rate,
                 lang ? lang : "-");
        if (lang && lang[0]) {
            emit(PROTO_EVT_REPLY_LANGUAGE, 0, lang);
        }
        audio_capture_stop();
        audio_playback_begin(turn);
        s_turn_deadline_ms = now_ms() + SPEAKING_IDLE_MS;
    } else if (strcmp(type, "tts_end") == 0) {
        s_tts_ended = true;
        audio_playback_end(turn);
    } else {
        ESP_LOGD(TAG, "unhandled message %s", type);
    }
out:
    cJSON_Delete(j);
}

/* Uplink-refused fast path - runs in the websocket task. subscription_required
 * / limit_reached answer listen_start while the mic is already streaming: kill
 * the turn here (capture_cb checks s_turn_live) so not one more uplink frame is
 * sent while the message waits in the proto queue. The proto task then does
 * the full handling (on_error_msg). */
static void uplink_refused_fast_path(const char *txt)
{
    if (!strstr(txt, "subscription_required") && !strstr(txt, "limit_reached")) {
        return;
    }
    cJSON *j = cJSON_Parse(txt);
    if (!j) {
        return;
    }
    const char *type = json_str(j, "type");
    const char *code = json_str(j, "code");
    uint32_t turn = 0;
    bool has_turn = json_uint32(j, "turn_id", &turn);
    if (type && code && strcmp(type, "error") == 0 &&
        (strcmp(code, "subscription_required") == 0 || strcmp(code, "limit_reached") == 0)) {
        xSemaphoreTake(s_turn_lock, portMAX_DELAY);
        if (!has_turn || turn == s_active_turn) {
            s_turn_live = false;
        }
        xSemaphoreGive(s_turn_lock);
    }
    cJSON_Delete(j);
}

/* Binary downlink fast path - runs in the websocket task. */
static void handle_binary(const uint8_t *data, size_t len)
{
    if (len <= AUDIO_HDR_LEN || data[0] != AUDIO_KIND_DOWNLINK) {
        return;
    }
    uint16_t codec = ((uint16_t)data[2] << 8) | data[3];
    if (codec != AUDIO_CODEC_OPUS) {
        return;
    }
    uint32_t turn = rd_be32(data + 4);
    bool first = false;

    xSemaphoreTake(s_turn_lock, portMAX_DELAY);
    if (turn == s_active_turn && s_turn_live) {
        if (!s_first_audio_seen) {
            s_first_audio_seen = true;
            first = true;
            audio_playback_begin(turn);     /* frames may overtake tts_start processing */
        }
        audio_playback_feed(turn, data + AUDIO_HDR_LEN, len - AUDIO_HDR_LEN);
    }
    xSemaphoreGive(s_turn_lock);

    if (first) {
        msg_t m = { .type = MSG_FIRST_AUDIO, .a = turn, .t = wall_ms() };
        post(&m);
    }
}

/* ------------------------------------------------------------------------- */
/* WebSocket                                                                  */
/* ------------------------------------------------------------------------- */

static void ws_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    uint32_t conn = (uint32_t)(uintptr_t)arg;
    esp_websocket_event_data_t *ev = data;
    msg_t m = { .a = conn };

    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
        m.type = MSG_WS_CONNECTED;
        post(&m);
        break;
    case WEBSOCKET_EVENT_DISCONNECTED:
    case WEBSOCKET_EVENT_CLOSED:
    case WEBSOCKET_EVENT_ERROR:
        m.type = MSG_WS_CLOSED;
        post(&m);
        break;
    case WEBSOCKET_EVENT_DATA: {
        if (conn != s_conn_id) {
            break;
        }
        int op = ev->op_code;
        if (op == 0x08 || op == 0x09 || op == 0x0A) {
            break; /* close / ping / pong handled by the client */
        }
        if (op != 0x00) {
            s_rx_opcode = op;       /* new message (continuation frames keep it) */
            if (ev->payload_offset == 0) {
                s_rx_len = 0;
            }
        }
        size_t need = s_rx_len + ev->data_len;
        if (need > MAX_TEXT_MSG) {
            ESP_LOGW(TAG, "incoming message too large, dropped");
            s_rx_len = 0;
            break;
        }
        if (need + 1 > s_rx_cap) {
            size_t cap = need + 1 > 2048 ? need + 1 : 2048;
            uint8_t *nb = heap_caps_realloc(s_rx_buf, cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (!nb) {
                s_rx_len = 0;
                break;
            }
            s_rx_buf = nb;
            s_rx_cap = cap;
        }
        memcpy(s_rx_buf + s_rx_len, ev->data_ptr, ev->data_len);
        s_rx_len += ev->data_len;

        bool chunk_done = ev->payload_offset + ev->data_len >= ev->payload_len;
        if (!chunk_done || !ev->fin) {
            break; /* wait for the rest */
        }
        if (s_rx_opcode == 0x02) {
            handle_binary(s_rx_buf, s_rx_len);
        } else if (s_rx_opcode == 0x01) {
            msg_t t = { .type = MSG_TEXT, .a = conn, .str = dup_psram((const char *)s_rx_buf, s_rx_len) };
            if (t.str) {
                uplink_refused_fast_path(t.str);
                post(&t);
            }
        }
        s_rx_len = 0;
        break;
    }
    default:
        break;
    }
}

static void ws_destroy(void)
{
    if (s_ws) {
        esp_websocket_client_handle_t ws = s_ws;
        xSemaphoreTake(s_send_lock, portMAX_DELAY);
        s_ws = NULL;
        xSemaphoreGive(s_send_lock);
        esp_websocket_client_stop(ws);
        esp_websocket_client_destroy(ws);
    }
}

static bool ws_open(const char *url)
{
    ws_destroy();
    s_conn_id++;
    strlcpy(s_url, url, sizeof(s_url));

    esp_websocket_client_config_t cfg = {
        .uri = url,
        .disable_auto_reconnect = true,
        .buffer_size = WS_BUFFER_SIZE,
        .task_stack = 6144,
        .task_prio = 6,
        .task_core_id_set = true,
        .task_core_id = 0,
        .network_timeout_ms = CONNECT_TIMEOUT_MS,
        .ping_interval_sec = 10,
        .pingpong_timeout_sec = 30,
        .keep_alive_enable = true,
        .crt_bundle_attach = esp_crt_bundle_attach,     /* used for wss:// only */
    };
    esp_websocket_client_handle_t ws = esp_websocket_client_init(&cfg);
    if (!ws) {
        return false;
    }
    esp_websocket_register_events(ws, WEBSOCKET_EVENT_ANY, ws_event_handler, (void *)(uintptr_t)s_conn_id);
    if (esp_websocket_client_start(ws) != ESP_OK) {
        esp_websocket_client_destroy(ws);
        return false;
    }
    xSemaphoreTake(s_send_lock, portMAX_DELAY);
    s_ws = ws;
    xSemaphoreGive(s_send_lock);
    return true;
}

/* ------------------------------------------------------------------------- */
/* Connection manager                                                         */
/* ------------------------------------------------------------------------- */

static void add_candidate(const char *url)
{
    if (!url || !url[0] || s_cand_count >= MAX_CANDIDATES) {
        return;
    }
#if CONFIG_BUDDYAI_RELEASE_BUILD
    /* Release builds only talk to the server over TLS. */
    if (strncmp(url, "wss://", 6) != 0) {
        ESP_LOGW(TAG, "release build: ignoring non-wss server URL %s", url);
        return;
    }
#endif
    for (int i = 0; i < s_cand_count; i++) {
        if (strcmp(s_candidates[i], url) == 0) {
            return;
        }
    }
    strlcpy(s_candidates[s_cand_count++], url, SETTINGS_URL_MAX);
}

/* Order (plan section 11): configured server_url -> last-known -> mDNS. */
static void build_candidates(void)
{
    char url[SETTINGS_URL_MAX];
    s_cand_count = 0;
    s_cand_idx = 0;
    s_mdns_tried = false;
    if (settings_get_server_url(url, sizeof(url))) {
        add_candidate(url);
    } else if (CONFIG_BUDDYAI_DEFAULT_SERVER_URL[0]) {
        add_candidate(CONFIG_BUDDYAI_DEFAULT_SERVER_URL);
    }
    if (settings_get_last_server(url, sizeof(url))) {
        add_candidate(url);
    }
}

static void enter_backoff(void)
{
    uint32_t wait = s_backoff_ms > s_backoff_floor_ms ? s_backoff_ms : s_backoff_floor_ms;
    s_conn = CONN_BACKOFF;
    s_deadline_ms = now_ms() + wait;
    ESP_LOGI(TAG, "next connection attempt in %lu s", (unsigned long)(wait / 1000));
    s_backoff_ms = s_backoff_ms * 2 > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : s_backoff_ms * 2;
}

static void try_next_candidate(void)
{
    ws_destroy();
    while (true) {
        if (s_cand_idx >= s_cand_count) {
#if CONFIG_BUDDYAI_MDNS_DISCOVERY
            if (!s_mdns_tried) {
                s_mdns_tried = true;
                char url[SETTINGS_URL_MAX];
                if (net_mdns_find_server(url, sizeof(url), MDNS_TIMEOUT_MS) == ESP_OK) {
                    int before = s_cand_count;
                    add_candidate(url);
                    if (s_cand_count > before) {
                        continue;
                    }
                }
            }
#endif
            bool none = (s_cand_count == 0);
            ESP_LOGW(TAG, "%s", none ? "no server configured or discovered" : "all servers unreachable");
            emit(PROTO_EVT_SERVER_UNREACHABLE, none ? 1 : 0, NULL);
            enter_backoff();
            return;
        }
        const char *url = s_candidates[s_cand_idx++];
        ESP_LOGI(TAG, "connecting to %s", url);
        emit(PROTO_EVT_CONNECTING, 0, url);
        if (ws_open(url)) {
            s_conn = CONN_CONNECTING;
            s_deadline_ms = now_ms() + CONNECT_TIMEOUT_MS + 2000;
            return;
        }
    }
}

static void start_cycle(void)
{
    build_candidates();
    try_next_candidate();
}

static void session_lost(void)
{
    bool had_session = (s_conn == CONN_SESSION);
    local_stop_turn();
    set_conv(PROTO_CONV_IDLE);
    s_session_id[0] = '\0';
    s_seq_out = 0;
    if (had_session) {
        emit(PROTO_EVT_DISCONNECTED, 0, NULL);
        s_backoff_ms = BACKOFF_MIN_MS;
    }
}

static void on_ws_connected(void)
{
    if (s_conn != CONN_CONNECTING) {
        return;
    }
    ESP_LOGI(TAG, "websocket open: %s", s_url);
    s_conn = CONN_HELLO;
    s_seq_out = 0;
    s_session_id[0] = '\0';
    s_deadline_ms = now_ms() + HELLO_TIMEOUT_MS;
    send_hello();
}

static void on_ws_closed(void)
{
    ESP_LOGW(TAG, "websocket closed (%s)", s_url);
    conn_state_t prev = s_conn;
    session_lost();
    if (prev == CONN_CONNECTING) {
        try_next_candidate();
    } else if (prev == CONN_HELLO || prev == CONN_SESSION) {
        ws_destroy();
        enter_backoff();
    }
}

static void handle_msg(msg_t *m)
{
    switch (m->type) {
    case MSG_NET_UP:
        if (s_conn == CONN_NO_NET) {
            s_backoff_ms = BACKOFF_MIN_MS;
            start_cycle();
        }
        break;
    case MSG_NET_DOWN:
        if (s_conn != CONN_NO_NET) {
            session_lost();
            ws_destroy();
            s_conn = CONN_NO_NET;
        }
        break;
    case MSG_RECONNECT:
        session_lost();
        ws_destroy();
        if (net_wifi_connected()) {
            s_backoff_ms = BACKOFF_MIN_MS;
            start_cycle();
        } else {
            s_conn = CONN_NO_NET;
        }
        break;
    case MSG_WS_CONNECTED:
        if (m->a == s_conn_id) {
            on_ws_connected();
        }
        break;
    case MSG_WS_CLOSED:
        if (m->a == s_conn_id && s_conn >= CONN_CONNECTING) {
            on_ws_closed();
        }
        break;
    case MSG_TEXT:
        if (m->a == s_conn_id && s_conn >= CONN_HELLO) {
            handle_text(m->str);
        }
        break;
    case MSG_FIRST_AUDIO:
        if (m->a == s_active_turn && s_turn_live) {
            /* TTFA end point (section 9): timestamp = first frame receive time. */
            send_json_at(cJSON_CreateObject(), "playback_started", true, m->a, m->t);
            audio_capture_stop();
            set_conv(PROTO_CONV_SPEAKING);
            s_turn_deadline_ms = now_ms() + SPEAKING_IDLE_MS;
        }
        break;
    case MSG_PLAYBACK_DONE:
        if (m->a == s_active_turn && s_turn_live) {
            send_turn_msg("playback_done", m->a, NULL);
            finish_turn_idle();
        }
        break;
    case MSG_END_CONVERSATION:
        if (s_conv == PROTO_CONV_LISTENING) {
            abort_turn("user_tap");
            set_conv(PROTO_CONV_IDLE);
        }
        break;
    case MSG_TAP:
        handle_tap();
        break;
    case MSG_SETTINGS_CHANGED: {
        cJSON *changes = cJSON_Parse(m->str);
        if (changes) {
            settings_apply_local(changes);
            if (!s_pending_changes) {
                s_pending_changes = changes;
            } else {
                /* merge: newer values replace older ones */
                cJSON *it = changes->child;
                while (it) {
                    cJSON *next = it->next;
                    char key[32];
                    strlcpy(key, it->string ? it->string : "", sizeof(key));
                    cJSON_DetachItemViaPointer(changes, it);
                    cJSON_DeleteItemFromObjectCaseSensitive(s_pending_changes, key);
                    cJSON_AddItemToObject(s_pending_changes, key, it);
                    it = next;
                }
                cJSON_Delete(changes);
            }
            send_settings_changes();
        }
        break;
    }
    case MSG_OTA_STATUS:
        emit(PROTO_EVT_OTA, m->a == OTA_STATE_FAILED ? -1 : m->b, NULL);
        break;
    case MSG_ITEM_OPEN:
    case MSG_ITEM_DELETE:
        if (s_conn == CONN_SESSION) {
            cJSON *msg = cJSON_CreateObject();
            cJSON_AddStringToObject(msg, "kind", m->b ? "reminder" : "note");
            cJSON_AddNumberToObject(msg, "number", m->a);
            send_json(msg, m->type == MSG_ITEM_OPEN ? "item_open" : "item_delete", false, 0);
        }
        break;
    case MSG_ITEM_DONE:
        if (s_conn == CONN_SESSION) {
            cJSON *msg = cJSON_CreateObject();
            cJSON_AddStringToObject(msg, "kind", "reminder");
            cJSON_AddNumberToObject(msg, "number", m->a);
            cJSON_AddBoolToObject(msg, "done", m->b != 0);
            send_json(msg, "item_done", false, 0);
        }
        break;
    }
    free(m->str);
}

static void handle_timers(void)
{
    int64_t now = now_ms();

    switch (s_conn) {
    case CONN_BACKOFF:
        if (now >= s_deadline_ms) {
            if (net_wifi_connected()) {
                start_cycle();
            } else {
                s_conn = CONN_NO_NET;
            }
        }
        break;
    case CONN_CONNECTING:
        if (now >= s_deadline_ms) {
            ESP_LOGW(TAG, "connect timeout (%s)", s_url);
            try_next_candidate();
        }
        break;
    case CONN_HELLO:
        if (now >= s_deadline_ms) {
            ESP_LOGW(TAG, "no hello_ack/pairing progress - reconnecting");
            ws_destroy();
            enter_backoff();
        } else if (now - s_last_ping_ms >= PING_INTERVAL_MS) {
            s_last_ping_ms = now;
            send_simple("ping");
        }
        break;
    case CONN_SESSION:
        if (now - s_last_ping_ms >= PING_INTERVAL_MS) {
            s_last_ping_ms = now;
            s_ping_sent_ms = now;
            send_simple("ping");
        }
        if (now - s_last_status_ms >= STATUS_INTERVAL_MS) {
            s_last_status_ms = now;
            send_status();
        }
        /* Turn safety limits (the server VAD is the authority, section 10). */
        if (s_conv != PROTO_CONV_IDLE && now >= s_turn_deadline_ms) {
            if (s_conv == PROTO_CONV_SPEAKING && audio_playback_active()) {
                s_turn_deadline_ms = now + 1000;    /* still playing */
            } else {
                ESP_LOGW(TAG, "turn %lu timed out in state %d", (unsigned long)s_active_turn, s_conv);
                abort_turn("timeout");
                set_conv(PROTO_CONV_IDLE);
            }
        }
        break;
    default:
        break;
    }
}

static void proto_task(void *arg)
{
    for (;;) {
        msg_t m;
        if (xQueueReceive(s_q, &m, pdMS_TO_TICKS(250)) == pdTRUE) {
            handle_msg(&m);
        }
        handle_timers();
    }
}

/* ------------------------------------------------------------------------- */
/* Public API                                                                 */
/* ------------------------------------------------------------------------- */

esp_err_t proto_init(const proto_config_t *cfg)
{
    s_cfg = *cfg;
    s_q = xQueueCreate(32, sizeof(msg_t));
    s_send_lock = xSemaphoreCreateMutex();
    s_turn_lock = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(s_q && s_send_lock && s_turn_lock, ESP_ERR_NO_MEM, TAG, "rtos objs");
    audio_playback_set_done_cb(playback_done_cb, NULL);
    ESP_RETURN_ON_FALSE(xTaskCreatePinnedToCore(proto_task, "proto", 8192, NULL, 5, NULL, 0) == pdPASS,
                        ESP_ERR_NO_MEM, TAG, "task");
    return ESP_OK;
}

void proto_network_up(void)
{
    msg_t m = { .type = MSG_NET_UP };
    post(&m);
}

void proto_network_down(void)
{
    msg_t m = { .type = MSG_NET_DOWN };
    post(&m);
}

void proto_reconnect(void)
{
    msg_t m = { .type = MSG_RECONNECT };
    post(&m);
}

void proto_mic_tap(void)
{
    msg_t m = { .type = MSG_TAP };
    post(&m);
}

void proto_settings_changed(const cJSON *changes)
{
    char *s = cJSON_PrintUnformatted(changes);
    if (!s) {
        return;
    }
    msg_t m = { .type = MSG_SETTINGS_CHANGED, .str = dup_psram(s, strlen(s)) };
    cJSON_free(s);
    if (m.str) {
        post(&m);
    }
}

void proto_end_conversation(void)
{
    msg_t m = { .type = MSG_END_CONVERSATION };
    post(&m);
}

void proto_item_open(bool reminder, int number)
{
    msg_t m = { .type = MSG_ITEM_OPEN, .a = (uint32_t)number, .b = reminder ? 1 : 0 };
    post(&m);
}

void proto_item_delete(bool reminder, int number)
{
    msg_t m = { .type = MSG_ITEM_DELETE, .a = (uint32_t)number, .b = reminder ? 1 : 0 };
    post(&m);
}

void proto_item_done(int number, bool done)
{
    msg_t m = { .type = MSG_ITEM_DONE, .a = (uint32_t)number, .b = done ? 1 : 0 };
    post(&m);
}

bool proto_session_ready(void)
{
    return s_conn == CONN_SESSION;
}

proto_conv_state_t proto_conv_state(void)
{
    return s_conv;
}
