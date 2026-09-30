/*
 * BuddyAI - device side of protocol/PROTOCOL.md v1
 *
 * Owns the WebSocket session: server selection (server_url -> last-known ->
 * mDNS), hello / pairing, envelope + sequence numbers, turn management with
 * stale-frame dropping, tap-to-interrupt, playback_started / playback_done,
 * ping (15 s), status telemetry (60 s) and settings sync.
 *
 * All protocol work runs in one task ("proto", core 0). Public calls only post
 * messages to it and are safe from any task (UI, audio, network events).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "cJSON.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PROTO_VERSION   1

typedef enum {
    PROTO_CONV_IDLE,
    PROTO_CONV_LISTENING,
    PROTO_CONV_THINKING,
    PROTO_CONV_SPEAKING,
} proto_conv_state_t;

typedef enum {
    PROTO_ERR_NOT_CONNECTED,        /* tap while no session */
    PROTO_ERR_UNAUTHORIZED,         /* token revoked -> pairing again */
    PROTO_ERR_PROTOCOL_UNSUPPORTED, /* firmware update required */
    PROTO_ERR_AI,                   /* stt/llm/tts_failed, internal */
    PROTO_ERR_BUSY,
    PROTO_ERR_SUBSCRIPTION_REQUIRED, /* listen_start refused: no active BuddyAI Care subscription */
    PROTO_ERR_LIMIT_REACHED,        /* listen_start refused: monthly allowance used up */
    PROTO_ERR_ACCOUNT_INACTIVE,     /* hello refused: owner account suspended/closed (slow retry) */
} proto_error_t;

typedef enum {
    PROTO_EVT_CONNECTING,           /* str = url being tried */
    PROTO_EVT_SERVER_UNREACHABLE,   /* num = 1 if no server is configured at all */
    PROTO_EVT_DISCONNECTED,
    PROTO_EVT_PAIRING,              /* str = 6-digit code, num = expires_in_s */
    PROTO_EVT_PAIRED,
    PROTO_EVT_SESSION_READY,
    PROTO_EVT_CONV_STATE,           /* num = proto_conv_state_t */
    PROTO_EVT_TRANSCRIPT,           /* str = user text (stt_result) */
    PROTO_EVT_REPLY_DELTA,          /* str = llm_text delta */
    PROTO_EVT_REPLY_DISPLAY,        /* str = short value to show large (llm_display), e.g. "21°C" */
    PROTO_EVT_REPLY_LANGUAGE,       /* str = detected language code (stt_result / tts_start) */
    PROTO_EVT_ERROR,                /* num = proto_error_t, str = message (may be NULL) */
    PROTO_EVT_TIME_SET,             /* system clock set from hello_ack.server_time */
    PROTO_EVT_OTA,                  /* num = progress %, -1 = failed */
    PROTO_EVT_ITEMS,                /* str = `items` message JSON (notes/reminders snapshot) */
    PROTO_EVT_LANGUAGES,            /* str = `languages` message JSON (language picker) */
    PROTO_EVT_ITEMS_OPEN,           /* num = 1 reminders, 0 notes (`items_open`) */
    PROTO_EVT_ITEM_SHOW,            /* str = `item_show` message JSON */
    PROTO_EVT_REMINDER,             /* str = `reminder_fire` message JSON */
} proto_event_type_t;

typedef struct {
    proto_event_type_t type;
    int                num;
    const char        *str;         /* valid only during the callback */
} proto_event_t;

typedef void (*proto_event_cb_t)(const proto_event_t *ev, void *ctx);

/* Telemetry source for the periodic `status` message. */
typedef void (*proto_status_fn_t)(int *battery_pct, bool *charging);

typedef struct {
    proto_event_cb_t  on_event;     /* called from the proto task */
    void             *ctx;
    proto_status_fn_t get_status;
    const char       *fw_version;
    const char       *hw_model;
} proto_config_t;

esp_err_t proto_init(const proto_config_t *cfg);

/* Network availability (from Wi-Fi events). */
void proto_network_up(void);
void proto_network_down(void);

/* The big mic button: start a turn, or interrupt/cancel the current one. */
void proto_mic_tap(void);

/* User changed settings on the watch (subset of PROTOCOL.md section 5). The
 * change is applied locally at once and sent as settings_changed. */
void proto_settings_changed(const cJSON *changes);

/* The user left the conversation screen: abort a turn that is only listening. */
void proto_end_conversation(void);

/* Notes / reminders (PROTOCOL.md section 3.3). reminder = false for a note.
 * Ignored while there is no session. */
void proto_item_open(bool reminder, int number);
void proto_item_delete(bool reminder, int number);

/* Force a new connection cycle (e.g. after the server URL changed). */
void proto_reconnect(void);

bool proto_session_ready(void);
proto_conv_state_t proto_conv_state(void);

#ifdef __cplusplus
}
#endif
