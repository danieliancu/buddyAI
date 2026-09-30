/*
 * BuddyAI - audio pipeline
 *
 * Capture:  ES7210 mic -> 16 kHz mono PCM -> Opus (VOIP, 60 ms / 960 samples)
 *           -> audio_capture_cb_t (one Opus packet per call)
 * Playback: Opus packets -> decoder task -> PCM ring buffer (PSRAM) -> writer
 *           task -> ES8311. audio_playback_flush() stops within one I2S chunk.
 *
 * Tasks are pinned to core 1 (LVGL runs on core 0).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define AUDIO_UPLINK_SAMPLE_RATE    16000
#define AUDIO_FRAME_MS              60
#define AUDIO_UPLINK_FRAME_SAMPLES  (AUDIO_UPLINK_SAMPLE_RATE * AUDIO_FRAME_MS / 1000)   /* 960 */

/* Called from the capture task for every encoded packet. frame_seq starts at 0
 * for each capture session (PROTOCOL.md section 4). */
typedef void (*audio_capture_cb_t)(uint32_t turn_id, uint32_t frame_seq,
                                   const uint8_t *opus, size_t len, void *ctx);

/* Called from the playback writer task once all audio of `turn_id` that was
 * announced complete with audio_playback_end() has been played. */
typedef void (*audio_playback_done_cb_t)(uint32_t turn_id, void *ctx);

esp_err_t audio_init(void);

/* ---- capture ---- */
esp_err_t audio_capture_start(uint32_t turn_id, audio_capture_cb_t cb, void *ctx);
void      audio_capture_stop(void);
bool      audio_capture_active(void);
/* Last input level 0..100 (for the listening animation). */
int       audio_capture_level(void);

/* ---- playback ---- */
void      audio_playback_set_done_cb(audio_playback_done_cb_t cb, void *ctx);
/* Accept downlink packets for `turn_id` (implicitly flushes any other turn). */
void      audio_playback_begin(uint32_t turn_id);
/* Queue one Opus packet. Dropped if turn_id is not the accepted turn. */
esp_err_t audio_playback_feed(uint32_t turn_id, const uint8_t *opus, size_t len);
/* No more packets for turn_id: done callback fires once the buffer drains. */
void      audio_playback_end(uint32_t turn_id);
/* Immediate stop: mute, drop queued packets and PCM, forget the current turn. */
void      audio_playback_flush(void);
bool      audio_playback_active(void);
/* Last output level 0..100 (for the speaking animation). */
int       audio_playback_level(void);

void      audio_set_volume(int percent);

/* Short two-tone alert (reminders). Skipped while a reply plays or the mic is
 * open. Uses the playback path, so volume and speaker muting behave as usual. */
void      audio_beep(void);

#ifdef __cplusplus
}
#endif
