/*
 * ola - audio pipeline (capture + playback), see audio.h
 */
#include "audio.h"

#include <math.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/idf_additions.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "encoder/impl/esp_opus_enc.h"
#include "decoder/impl/esp_opus_dec.h"
#include "board.h"

static const char *TAG = "audio";

/* ---- tuning ---- */
/* libopus is stack hungry (24 KB overflowed on the first encode). The encode
 * and decode stacks live in PSRAM: internal RAM is needed by Wi-Fi and DMA. */
#define CAPTURE_TASK_STACK      (48 * 1024)
#define DECODE_TASK_STACK       (32 * 1024)
#define WRITER_TASK_STACK       (4 * 1024)
#define CAPTURE_TASK_PRIO       8
#define WRITER_TASK_PRIO        7
#define DECODE_TASK_PRIO        6
#define AUDIO_CORE              1

#define UPLINK_BITRATE          24000
#define UPLINK_COMPLEXITY       3

#define PLAY_RATE               BOARD_AUDIO_SAMPLE_RATE
#define PKT_QUEUE_DEPTH         300                         /* ~18 s of 60 ms packets */
#define PCM_RING_SECONDS        20
#define PCM_RING_BYTES          (PLAY_RATE * 2 * PCM_RING_SECONDS)
#define WRITE_CHUNK_SAMPLES     (PLAY_RATE / 50)            /* 20 ms */
#define DEC_OUT_MAX_SAMPLES     (PLAY_RATE * 120 / 1000)    /* up to 120 ms packets */
#define PA_IDLE_OFF_MS          400
/* Jitter buffer: a reply starts playing once PREBUFFER_MS is queued, its end
 * has arrived, or PREBUFFER_MAX_WAIT_MS passed. The server streams TTS
 * fragment by fragment and the TTS sometimes delivers slower than real time;
 * a reply that ran dry mid-way therefore waits for a much larger reserve
 * (REBUFFER_MS) before resuming: one short pause instead of a stutter. */
#define PREBUFFER_MS            300
#define PREBUFFER_BYTES         (PLAY_RATE * 2 * PREBUFFER_MS / 1000)
#define PREBUFFER_MAX_WAIT_MS   600
#define REBUFFER_MS             1500
#define REBUFFER_BYTES          (PLAY_RATE * 2 * REBUFFER_MS / 1000)
#define REBUFFER_MAX_WAIT_MS    2500

/* ------------------------------------------------------------------------- */
/* PCM ring buffer (PSRAM)                                                    */
/* ------------------------------------------------------------------------- */

typedef struct {
    uint8_t          *buf;
    size_t            size;
    size_t            head;     /* write position */
    size_t            tail;     /* read position */
    size_t            count;
    SemaphoreHandle_t lock;
    SemaphoreHandle_t data_sem;
    SemaphoreHandle_t space_sem;
} ring_t;

static ring_t s_ring;

static esp_err_t ring_init(ring_t *r, size_t size)
{
    r->buf = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_RETURN_ON_FALSE(r->buf, ESP_ERR_NO_MEM, TAG, "ring alloc");
    r->size = size;
    r->head = r->tail = r->count = 0;
    r->lock = xSemaphoreCreateMutex();
    r->data_sem = xSemaphoreCreateBinary();
    r->space_sem = xSemaphoreCreateBinary();
    return ESP_OK;
}

static void ring_clear(ring_t *r)
{
    xSemaphoreTake(r->lock, portMAX_DELAY);
    r->head = r->tail = r->count = 0;
    xSemaphoreGive(r->lock);
    xSemaphoreGive(r->space_sem);
}

static size_t ring_count(ring_t *r)
{
    xSemaphoreTake(r->lock, portMAX_DELAY);
    size_t c = r->count;
    xSemaphoreGive(r->lock);
    return c;
}

/* Non-blocking partial write, returns bytes written. Writes nothing if the
 * playback generation changed (checked under the ring lock, so a concurrent
 * flush either sees this data and clears it, or this call sees the new gen). */
static volatile uint32_t s_play_gen;        /* bumped on begin/flush */

static size_t ring_write_some(ring_t *r, const uint8_t *data, size_t len, uint32_t gen)
{
    xSemaphoreTake(r->lock, portMAX_DELAY);
    if (gen != s_play_gen) {
        xSemaphoreGive(r->lock);
        return 0;
    }
    size_t n = r->size - r->count;
    if (n > len) {
        n = len;
    }
    size_t first = r->size - r->head;
    if (first > n) {
        first = n;
    }
    memcpy(r->buf + r->head, data, first);
    memcpy(r->buf, data + first, n - first);
    r->head = (r->head + n) % r->size;
    r->count += n;
    xSemaphoreGive(r->lock);
    if (n) {
        xSemaphoreGive(r->data_sem);
    }
    return n;
}

static size_t ring_read(ring_t *r, uint8_t *out, size_t len, TickType_t wait)
{
    for (int attempt = 0; attempt < 2; attempt++) {
        xSemaphoreTake(r->lock, portMAX_DELAY);
        size_t n = r->count < len ? r->count : len;
        size_t first = r->size - r->tail;
        if (first > n) {
            first = n;
        }
        memcpy(out, r->buf + r->tail, first);
        memcpy(out + first, r->buf, n - first);
        r->tail = (r->tail + n) % r->size;
        r->count -= n;
        xSemaphoreGive(r->lock);
        if (n) {
            xSemaphoreGive(r->space_sem);
            return n;
        }
        if (attempt == 0 && xSemaphoreTake(r->data_sem, wait) != pdTRUE) {
            break;
        }
    }
    return 0;
}

/* ------------------------------------------------------------------------- */
/* State                                                                      */
/* ------------------------------------------------------------------------- */

typedef struct {
    uint32_t gen;
    uint16_t len;           /* 0 = end-of-turn marker */
    uint8_t  data[];
} pkt_t;

static SemaphoreHandle_t s_play_lock;
static QueueHandle_t     s_pkt_q;
static void             *s_decoder;
static volatile uint32_t s_play_turn;
static volatile bool     s_play_turn_valid;
static volatile uint32_t s_end_gen;         /* gen whose end marker was decoded */
static volatile bool     s_end_seen;
static volatile int      s_play_level;
static bool              s_muted = true;    /* speaker mute state, guarded by s_play_lock */
static audio_playback_done_cb_t s_done_cb;
static void             *s_done_ctx;

static void             *s_encoder;
static int               s_enc_in_size;
static int               s_enc_out_size;
static TaskHandle_t      s_capture_task;
static SemaphoreHandle_t s_capture_idle;    /* given when the capture loop is idle */
static volatile bool     s_capturing;
static volatile uint32_t s_cap_turn;
static audio_capture_cb_t s_cap_cb;
static void             *s_cap_ctx;
static volatile int      s_cap_level;

static int pcm_level(const int16_t *pcm, size_t samples)
{
    if (!samples) {
        return 0;
    }
    int64_t acc = 0;
    for (size_t i = 0; i < samples; i++) {
        acc += (int32_t)pcm[i] * pcm[i];
    }
    float rms = sqrtf((float)acc / samples);
    if (rms < 1.0f) {
        return 0;
    }
    float db = 20.0f * log10f(rms / 32768.0f);     /* -90 .. 0 dBFS */
    int lvl = (int)((db + 60.0f) * 100.0f / 60.0f);
    return lvl < 0 ? 0 : (lvl > 100 ? 100 : lvl);
}

/* ------------------------------------------------------------------------- */
/* Capture                                                                    */
/* ------------------------------------------------------------------------- */

static void capture_task(void *arg)
{
    int16_t *pcm = heap_caps_malloc(s_enc_in_size, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    uint8_t *out = heap_caps_malloc(s_enc_out_size, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    assert(pcm && out);
    esp_codec_dev_handle_t mic = board_audio_mic();

    for (;;) {
        xSemaphoreGive(s_capture_idle);
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        xSemaphoreTake(s_capture_idle, 0);

        uint32_t seq = 0;
        uint32_t turn = s_cap_turn;
        /* Discard whatever sat in the RX DMA buffers before the tap. */
        esp_codec_dev_read(mic, pcm, s_enc_in_size / 4);

        while (s_capturing && turn == s_cap_turn) {
            if (esp_codec_dev_read(mic, pcm, s_enc_in_size) != ESP_CODEC_DEV_OK) {
                ESP_LOGW(TAG, "mic read failed");
                vTaskDelay(pdMS_TO_TICKS(10));
                continue;
            }
            s_cap_level = pcm_level(pcm, s_enc_in_size / 2);

            esp_audio_enc_in_frame_t in = {
                .buffer = (uint8_t *)pcm,
                .len = (uint32_t)s_enc_in_size,
            };
            esp_audio_enc_out_frame_t o = {
                .buffer = out,
                .len = (uint32_t)s_enc_out_size,
            };
            esp_audio_err_t ret = esp_opus_enc_process(s_encoder, &in, &o);
            if (ret != ESP_AUDIO_ERR_OK) {
                ESP_LOGW(TAG, "opus encode error %d", ret);
                continue;
            }
            if (s_capturing && turn == s_cap_turn && s_cap_cb && o.encoded_bytes > 0) {
                s_cap_cb(turn, seq++, out, o.encoded_bytes, s_cap_ctx);
            }
        }
        s_cap_level = 0;
        ESP_LOGI(TAG, "capture stack: %u B unused of %u", (unsigned)uxTaskGetStackHighWaterMark(NULL),
                 (unsigned)CAPTURE_TASK_STACK);
    }
}

esp_err_t audio_capture_start(uint32_t turn_id, audio_capture_cb_t cb, void *ctx)
{
    /* Half duplex (MVP): nothing plays while the user talks. */
    audio_playback_flush();
    s_cap_cb = cb;
    s_cap_ctx = ctx;
    s_cap_turn = turn_id;
    s_capturing = true;
    xTaskNotifyGive(s_capture_task);
    ESP_LOGI(TAG, "capture start (turn %lu)", (unsigned long)turn_id);
    return ESP_OK;
}

void audio_capture_stop(void)
{
    if (!s_capturing) {
        return;
    }
    s_capturing = false;
    /* Wait (bounded) for the loop to finish its current 60 ms frame. */
    if (xSemaphoreTake(s_capture_idle, pdMS_TO_TICKS(200)) == pdTRUE) {
        xSemaphoreGive(s_capture_idle);
    }
    ESP_LOGI(TAG, "capture stop");
}

bool audio_capture_active(void)
{
    return s_capturing;
}

int audio_capture_level(void)
{
    return s_cap_level;
}

/* ------------------------------------------------------------------------- */
/* Playback                                                                   */
/* ------------------------------------------------------------------------- */

static void decode_task(void *arg)
{
    int16_t *pcm = heap_caps_malloc(DEC_OUT_MAX_SAMPLES * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    assert(pcm);

    for (;;) {
        pkt_t *p = NULL;
        if (xQueueReceive(s_pkt_q, &p, portMAX_DELAY) != pdTRUE || !p) {
            continue;
        }
        if (p->gen != s_play_gen) {
            free(p);                    /* stale: flushed while queued */
            continue;
        }
        if (p->len == 0) {
            s_end_gen = p->gen;         /* end-of-turn marker reached */
            s_end_seen = true;
            free(p);
            continue;
        }

        esp_audio_dec_in_raw_t raw = {
            .buffer = p->data,
            .len = p->len,
        };
        esp_audio_dec_out_frame_t frame = {
            .buffer = (uint8_t *)pcm,
            .len = DEC_OUT_MAX_SAMPLES * 2,
        };
        esp_audio_dec_info_t info = {0};
        esp_audio_err_t ret = esp_opus_dec_decode(s_decoder, &raw, &frame, &info);
        uint32_t gen = p->gen;
        free(p);
        if (ret != ESP_AUDIO_ERR_OK) {
            ESP_LOGW(TAG, "opus decode error %d", ret);
            continue;
        }

        /* Push PCM; wait for space while this generation is still current. */
        const uint8_t *src = (const uint8_t *)pcm;
        size_t left = frame.decoded_size;
        while (left && gen == s_play_gen) {
            size_t n = ring_write_some(&s_ring, src, left, gen);
            src += n;
            left -= n;
            if (left) {
                xSemaphoreTake(s_ring.space_sem, pdMS_TO_TICKS(50));
            }
        }
    }
}

static bool prebuffer_ready(uint32_t gen, int64_t *since_us, bool rebuffer)
{
    size_t have = ring_count(&s_ring);
    if (have == 0) {
        *since_us = 0;
        return s_end_seen && s_end_gen == gen;      /* empty reply: let "done" through */
    }
    if (*since_us == 0) {
        *since_us = esp_timer_get_time();
    }
    size_t want = rebuffer ? REBUFFER_BYTES : PREBUFFER_BYTES;
    int64_t max_wait_us = (rebuffer ? REBUFFER_MAX_WAIT_MS : PREBUFFER_MAX_WAIT_MS) * 1000LL;
    return have >= want || (s_end_seen && s_end_gen == gen) ||
           esp_timer_get_time() - *since_us >= max_wait_us;
}

static void writer_task(void *arg)
{
    const size_t chunk_bytes = WRITE_CHUNK_SAMPLES * 2;
    int16_t *buf = heap_caps_malloc(chunk_bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    int16_t *silence = heap_caps_calloc(1, chunk_bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    assert(buf && silence);
    esp_codec_dev_handle_t spk = board_audio_speaker();

    bool pa_on = false;
    uint32_t played_gen = UINT32_MAX;       /* generation currently audible */
    uint32_t reported_gen = UINT32_MAX;     /* generation whose done was reported */
    int64_t last_audio_us = 0;
    bool buffering = true;                  /* waiting to (re)start: new reply or ran dry */
    bool rebuffer = false;                  /* the wait is after running dry mid-reply */
    int64_t buffer_since_us = 0;

    for (;;) {
        uint32_t gen = s_play_gen;
        if (played_gen != gen) {
            buffering = true;
            rebuffer = false;
        }
        size_t n = 0;
        if (!buffering || prebuffer_ready(gen, &buffer_since_us, rebuffer)) {
            buffering = false;
            n = ring_read(&s_ring, (uint8_t *)buf, chunk_bytes, pdMS_TO_TICKS(20));
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }

        if (n > 0) {
            if (played_gen != gen) {
                /* New reply: push out anything left in the I2S DMA ring while
                 * still muted (flush/begin leave the speaker muted). */
                for (int i = 0; i < 5; i++) {
                    esp_codec_dev_write(spk, silence, chunk_bytes);
                }
                played_gen = gen;
            }
            /* Unmute only if no flush happened since the read; a flush that
             * races with the write below mutes the chunk in flight. */
            xSemaphoreTake(s_play_lock, portMAX_DELAY);
            bool current = (gen == s_play_gen);
            if (current) {
                board_audio_pa_enable(true);
                if (s_muted) {
                    esp_codec_dev_set_out_mute(spk, false);
                    s_muted = false;
                }
            }
            xSemaphoreGive(s_play_lock);
            if (!current) {
                continue;               /* stale chunk - drop */
            }
            if (n & 1) {
                n--;
            }
            s_play_level = pcm_level(buf, n / 2);
            esp_codec_dev_write(spk, buf, n);
            last_audio_us = esp_timer_get_time();
            pa_on = true;
            continue;
        }

        /* Buffer empty. */
        s_play_level = 0;
        if (!buffering && played_gen == gen && !(s_end_seen && s_end_gen == gen)) {
            buffering = true;               /* ran dry mid-reply: rebuffer before resuming */
            rebuffer = true;
            buffer_since_us = 0;
        }
        if (s_end_seen && s_end_gen == gen && reported_gen != gen && s_play_turn_valid) {
            reported_gen = gen;
            uint32_t turn = s_play_turn;
            ESP_LOGI(TAG, "playback done (turn %lu)", (unsigned long)turn);
            if (s_done_cb) {
                s_done_cb(turn, s_done_ctx);
            }
        }
        /* Keep the amplifier on while waiting mid-reply (no click on resume). */
        if (pa_on && !(buffering && rebuffer) && (esp_timer_get_time() - last_audio_us) > PA_IDLE_OFF_MS * 1000) {
            xSemaphoreTake(s_play_lock, portMAX_DELAY);
            board_audio_pa_enable(false);
            xSemaphoreGive(s_play_lock);
            pa_on = false;
        }
    }
}

void audio_playback_set_done_cb(audio_playback_done_cb_t cb, void *ctx)
{
    s_done_cb = cb;
    s_done_ctx = ctx;
}

/* Caller holds s_play_lock. Silences the speaker immediately and invalidates
 * everything queued so far. */
static void flush_locked(void)
{
    if (!s_muted) {
        esp_codec_dev_set_out_mute(board_audio_speaker(), true);
        s_muted = true;
    }
    board_audio_pa_enable(false);
    s_play_gen++;
    s_end_seen = false;
    ring_clear(&s_ring);
    pkt_t *p;
    while (xQueueReceive(s_pkt_q, &p, 0) == pdTRUE) {
        free(p);
    }
    if (s_decoder) {
        esp_opus_dec_reset(s_decoder);
    }
}

void audio_playback_flush(void)
{
    xSemaphoreTake(s_play_lock, portMAX_DELAY);
    flush_locked();
    s_play_turn_valid = false;
    s_play_level = 0;
    xSemaphoreGive(s_play_lock);
}

void audio_playback_begin(uint32_t turn_id)
{
    xSemaphoreTake(s_play_lock, portMAX_DELAY);
    if (!s_play_turn_valid || s_play_turn != turn_id) {
        flush_locked();
        s_play_turn = turn_id;
        s_play_turn_valid = true;
        ESP_LOGI(TAG, "playback begin (turn %lu)", (unsigned long)turn_id);
    }
    xSemaphoreGive(s_play_lock);
}

static esp_err_t enqueue(uint32_t turn_id, const uint8_t *data, size_t len)
{
    esp_err_t ret = ESP_OK;
    xSemaphoreTake(s_play_lock, portMAX_DELAY);
    if (!s_play_turn_valid || s_play_turn != turn_id) {
        ret = ESP_ERR_INVALID_STATE;    /* stale turn */
        goto out;
    }
    pkt_t *p = heap_caps_malloc(sizeof(pkt_t) + len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!p) {
        ret = ESP_ERR_NO_MEM;
        goto out;
    }
    p->gen = s_play_gen;
    p->len = (uint16_t)len;
    if (len) {
        memcpy(p->data, data, len);
    }
    if (xQueueSend(s_pkt_q, &p, 0) != pdTRUE) {
        free(p);
        ret = ESP_ERR_TIMEOUT;
        ESP_LOGW(TAG, "packet queue full - dropping downlink frame");
    }
out:
    xSemaphoreGive(s_play_lock);
    return ret;
}

esp_err_t audio_playback_feed(uint32_t turn_id, const uint8_t *opus, size_t len)
{
    if (!opus || len == 0 || len > 1500) {
        return ESP_ERR_INVALID_ARG;
    }
    return enqueue(turn_id, opus, len);
}

void audio_playback_end(uint32_t turn_id)
{
    enqueue(turn_id, NULL, 0);
}

void audio_beep(void)
{
    if (!s_ring.buf || audio_playback_active() || audio_capture_active()) {
        return;
    }
    /* 880 Hz then 1320 Hz, 150 ms each with a 60 ms gap; 5 ms fades avoid clicks. */
    const int tone = PLAY_RATE * 150 / 1000, gap = PLAY_RATE * 60 / 1000, fade = PLAY_RATE * 5 / 1000;
    const int total = tone * 2 + gap;
    int16_t *pcm = heap_caps_calloc(total, sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!pcm) {
        return;
    }
    const float freqs[2] = { 880.0f, 1320.0f };
    for (int t = 0; t < 2; t++) {
        int16_t *out = pcm + t * (tone + gap);
        for (int i = 0; i < tone; i++) {
            float env = 1.0f;
            if (i < fade) {
                env = (float)i / fade;
            } else if (i > tone - fade) {
                env = (float)(tone - i) / fade;
            }
            out[i] = (int16_t)(0.4f * 32767.0f * env * sinf(2.0f * (float)M_PI * freqs[t] * i / PLAY_RATE));
        }
    }

    xSemaphoreTake(s_play_lock, portMAX_DELAY);
    flush_locked();
    s_play_turn_valid = false;      /* no turn: no playback_done callback */
    uint32_t gen = s_play_gen;
    xSemaphoreGive(s_play_lock);

    ring_write_some(&s_ring, (const uint8_t *)pcm, total * sizeof(int16_t), gen);
    free(pcm);
    xSemaphoreTake(s_play_lock, portMAX_DELAY);
    if (gen == s_play_gen) {
        s_end_gen = gen;            /* complete: the writer starts at once */
        s_end_seen = true;
    }
    xSemaphoreGive(s_play_lock);
}

bool audio_playback_active(void)
{
    return s_play_turn_valid && (ring_count(&s_ring) > 0 || uxQueueMessagesWaiting(s_pkt_q) > 0);
}

int audio_playback_level(void)
{
    return s_play_level;
}

void audio_set_volume(int percent)
{
    board_audio_set_volume(percent);
}

/* ------------------------------------------------------------------------- */

esp_err_t audio_init(void)
{
    ESP_RETURN_ON_FALSE(board_audio_speaker() && board_audio_mic(), ESP_ERR_INVALID_STATE, TAG,
                        "board_audio_init() first");

    /* Opus encoder: 16 kHz mono, 60 ms, VOIP (PROTOCOL.md section 4). */
    esp_opus_enc_config_t enc_cfg = {
        .sample_rate = AUDIO_UPLINK_SAMPLE_RATE,
        .channel = 1,
        .bits_per_sample = 16,
        .bitrate = UPLINK_BITRATE,
        .frame_duration = ESP_OPUS_ENC_FRAME_DURATION_60_MS,
        .application_mode = ESP_OPUS_ENC_APPLICATION_VOIP,
        .complexity = UPLINK_COMPLEXITY,
        .enable_fec = false,
        .enable_dtx = false,
        .enable_vbr = true,
    };
    ESP_RETURN_ON_FALSE(esp_opus_enc_open(&enc_cfg, sizeof(enc_cfg), &s_encoder) == ESP_AUDIO_ERR_OK,
                        ESP_FAIL, TAG, "opus enc open");
    esp_opus_enc_get_frame_size(s_encoder, &s_enc_in_size, &s_enc_out_size);
    ESP_LOGI(TAG, "opus encoder: in %d B, out max %d B", s_enc_in_size, s_enc_out_size);

    /* Opus decoder outputs at the codec rate whatever the encoded rate
     * (16 or 24 kHz) - libopus resamples internally. */
    esp_opus_dec_cfg_t dec_cfg = {
        .sample_rate = PLAY_RATE,
        .channel = 1,
        .frame_duration = ESP_OPUS_DEC_FRAME_DURATION_INVALID,
        .self_delimited = false,
    };
    ESP_RETURN_ON_FALSE(esp_opus_dec_open(&dec_cfg, sizeof(dec_cfg), &s_decoder) == ESP_AUDIO_ERR_OK,
                        ESP_FAIL, TAG, "opus dec open");

    ESP_RETURN_ON_ERROR(ring_init(&s_ring, PCM_RING_BYTES), TAG, "ring");
    esp_codec_dev_set_out_mute(board_audio_speaker(), true);
    s_muted = true;
    s_play_lock = xSemaphoreCreateMutex();
    s_capture_idle = xSemaphoreCreateBinary();
    s_pkt_q = xQueueCreate(PKT_QUEUE_DEPTH, sizeof(pkt_t *));
    ESP_RETURN_ON_FALSE(s_play_lock && s_capture_idle && s_pkt_q, ESP_ERR_NO_MEM, TAG, "rtos objs");

    BaseType_t ok = pdPASS;
    ok &= xTaskCreatePinnedToCoreWithCaps(capture_task, "aud_cap", CAPTURE_TASK_STACK, NULL,
                                          CAPTURE_TASK_PRIO, &s_capture_task, AUDIO_CORE, MALLOC_CAP_SPIRAM);
    ok &= xTaskCreatePinnedToCoreWithCaps(decode_task, "aud_dec", DECODE_TASK_STACK, NULL,
                                          DECODE_TASK_PRIO, NULL, AUDIO_CORE, MALLOC_CAP_SPIRAM);
    ok &= xTaskCreatePinnedToCore(writer_task, "aud_out", WRITER_TASK_STACK, NULL,
                                  WRITER_TASK_PRIO, NULL, AUDIO_CORE);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "tasks");

    ESP_LOGI(TAG, "audio pipeline ready (PCM ring %d KB in PSRAM)", PCM_RING_BYTES / 1024);
    return ESP_OK;
}
