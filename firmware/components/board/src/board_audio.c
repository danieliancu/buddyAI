/*
 * BuddyAI - audio codecs: ES8311 (DAC -> NS4150B PA -> speaker) and
 * ES7210 (ADC, 2 onboard MEMS mics + AEC loopback on MIC3).
 *
 * Both codecs share one full-duplex I2S port (ESP32-S3 master, shared BCLK/WS),
 * therefore playback and capture run at the same sample rate
 * (BOARD_AUDIO_SAMPLE_RATE). Wiring follows the Waveshare BSP
 * (bsp_audio_codec_speaker_init / bsp_audio_codec_microphone_init).
 *
 * Uplink is mono: the I2S RX slot is mono (left) which carries ES7210 MIC1.
 */
#include "freertos/FreeRTOS.h"
#include "esp_log.h"
#include "esp_check.h"
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "board.h"

static const char *TAG = "board_audio";

/* Microphone analog gain (dB). TODO(M0): tune on hardware. */
#define MIC_GAIN_DB     30.0f

static i2s_chan_handle_t s_tx;
static i2s_chan_handle_t s_rx;
static esp_codec_dev_handle_t s_speaker;
static esp_codec_dev_handle_t s_mic;

static esp_err_t i2s_init(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(BOARD_I2S_PORT, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;         /* play silence on underrun */
    chan_cfg.dma_desc_num = 6;
    chan_cfg.dma_frame_num = 240;       /* 15 ms per descriptor @16 kHz */
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_tx, &s_rx), TAG, "i2s channel");

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(BOARD_AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = BOARD_I2S_MCLK,
            .bclk = BOARD_I2S_BCLK,
            .ws = BOARD_I2S_WS,
            .dout = BOARD_I2S_DOUT,
            .din = BOARD_I2S_DIN,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    std_cfg.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_tx, &std_cfg), TAG, "tx init");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_rx, &std_cfg), TAG, "rx init");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_tx), TAG, "tx enable");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_rx), TAG, "rx enable");
    return ESP_OK;
}

void board_audio_pa_enable(bool on)
{
    gpio_set_level(BOARD_AUDIO_PA_EN, on ? 1 : 0);
}

esp_err_t board_audio_init(void)
{
    if (s_speaker) {
        return ESP_OK;
    }

    /* PA is driven directly (not by the codec driver) so the audio component
     * can switch it off between replies. */
    gpio_config_t pa_cfg = {
        .pin_bit_mask = 1ULL << BOARD_AUDIO_PA_EN,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&pa_cfg);
    board_audio_pa_enable(false);

    ESP_RETURN_ON_ERROR(i2s_init(), TAG, "i2s");

    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = BOARD_I2S_PORT,
        .rx_handle = s_rx,
        .tx_handle = s_tx,
    };
    const audio_codec_data_if_t *data_if = audio_codec_new_i2s_data(&i2s_cfg);
    ESP_RETURN_ON_FALSE(data_if, ESP_FAIL, TAG, "i2s data if");

    const audio_codec_gpio_if_t *gpio_if = audio_codec_new_gpio();

    /* ---- ES8311: DAC only ---- */
    audio_codec_i2c_cfg_t es8311_i2c = {
        .port = BOARD_I2C_PORT,
        .addr = BOARD_CODEC_ES8311_ADDR8,
        .bus_handle = board_i2c_bus(),
    };
    const audio_codec_ctrl_if_t *es8311_ctrl = audio_codec_new_i2c_ctrl(&es8311_i2c);
    ESP_RETURN_ON_FALSE(es8311_ctrl, ESP_FAIL, TAG, "es8311 ctrl");
    es8311_codec_cfg_t es8311_cfg = {
        .ctrl_if = es8311_ctrl,
        .gpio_if = gpio_if,
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .pa_pin = -1,               /* PA handled by board_audio_pa_enable() */
        .pa_reverted = false,
        .master_mode = false,
        .use_mclk = true,
        .digital_mic = false,
        .invert_mclk = false,
        .invert_sclk = false,
        .hw_gain = {
            .pa_voltage = 5.0,
            .codec_dac_voltage = 3.3,
        },
    };
    const audio_codec_if_t *es8311 = es8311_codec_new(&es8311_cfg);
    ESP_RETURN_ON_FALSE(es8311, ESP_FAIL, TAG, "es8311");
    esp_codec_dev_cfg_t spk_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
        .codec_if = es8311,
        .data_if = data_if,
    };
    s_speaker = esp_codec_dev_new(&spk_cfg);
    ESP_RETURN_ON_FALSE(s_speaker, ESP_FAIL, TAG, "speaker dev");

    /* ---- ES7210: ADC (MIC1 + MIC2, I2S normal mode) ---- */
    audio_codec_i2c_cfg_t es7210_i2c = {
        .port = BOARD_I2C_PORT,
        .addr = BOARD_CODEC_ES7210_ADDR8,
        .bus_handle = board_i2c_bus(),
    };
    const audio_codec_ctrl_if_t *es7210_ctrl = audio_codec_new_i2c_ctrl(&es7210_i2c);
    ESP_RETURN_ON_FALSE(es7210_ctrl, ESP_FAIL, TAG, "es7210 ctrl");
    es7210_codec_cfg_t es7210_cfg = {
        .ctrl_if = es7210_ctrl,
        .master_mode = false,
        .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2,
    };
    const audio_codec_if_t *es7210 = es7210_codec_new(&es7210_cfg);
    ESP_RETURN_ON_FALSE(es7210, ESP_FAIL, TAG, "es7210");
    esp_codec_dev_cfg_t mic_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
        .codec_if = es7210,
        .data_if = data_if,
    };
    s_mic = esp_codec_dev_new(&mic_cfg);
    ESP_RETURN_ON_FALSE(s_mic, ESP_FAIL, TAG, "mic dev");

    /* Open both directions once at the fixed rate and keep them open. */
    esp_codec_dev_sample_info_t fs = {
        .sample_rate = BOARD_AUDIO_SAMPLE_RATE,
        .channel = 1,
        .bits_per_sample = BOARD_AUDIO_BITS,
    };
    ESP_RETURN_ON_FALSE(esp_codec_dev_open(s_speaker, &fs) == ESP_CODEC_DEV_OK, ESP_FAIL, TAG, "open spk");
    ESP_RETURN_ON_FALSE(esp_codec_dev_open(s_mic, &fs) == ESP_CODEC_DEV_OK, ESP_FAIL, TAG, "open mic");
    esp_codec_dev_set_in_gain(s_mic, MIC_GAIN_DB);
    esp_codec_dev_set_out_vol(s_speaker, 70);

    ESP_LOGI(TAG, "audio ready: %d Hz, ES8311 out + ES7210 in", BOARD_AUDIO_SAMPLE_RATE);
    return ESP_OK;
}

esp_codec_dev_handle_t board_audio_speaker(void)
{
    return s_speaker;
}

esp_codec_dev_handle_t board_audio_mic(void)
{
    return s_mic;
}

esp_err_t board_audio_set_volume(int percent)
{
    if (!s_speaker) {
        return ESP_ERR_INVALID_STATE;
    }
    if (percent < 0) {
        percent = 0;
    } else if (percent > 100) {
        percent = 100;
    }
    return esp_codec_dev_set_out_vol(s_speaker, percent) == ESP_CODEC_DEV_OK ? ESP_OK : ESP_FAIL;
}
