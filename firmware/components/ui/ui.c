/*
 * BuddyAI - UI core: watchface, conversation states, theme, screen power
 * (dim / off after screen_timeout_s, tap wakes) and AMOLED pixel shift.
 */
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "esp_log.h"
#include "board.h"
#include "ui_priv.h"

static const char *TAG = "ui";

ui_theme_t       g_ui_theme;
buddy_settings_t g_ui_settings;
ui_callbacks_t   g_ui_cb;

#define LOCK()      board_display_lock(0)
#define UNLOCK()    board_display_unlock()

#define MIC_BTN_SIZE        150
#define RING_BASE_SIZE      170
#define SPINNER_SIZE        186
#define NUM_BARS            5
#define DIM_BRIGHTNESS      8       /* % while dimmed */
#define OFF_AFTER_DIM_MS    60000   /* panel off this long after dimming */
#define CAPTION_MAX         600
#define CAPTION_SHOW        110     /* ~3 lines at 20 px on 360 px */

typedef enum { POWER_ON, POWER_DIM, POWER_OFF } power_state_t;

static lv_display_t *s_disp;
static lv_obj_t *s_scr;
static lv_obj_t *s_content;
static lv_obj_t *s_lbl_wifi;
static lv_obj_t *s_lbl_batt;
static lv_obj_t *s_lbl_time;
static lv_obj_t *s_lbl_date;
static lv_obj_t *s_lbl_hint;
static lv_obj_t *s_lbl_caption;
static lv_obj_t *s_btn_mic;
static lv_obj_t *s_lbl_mic;
static lv_obj_t *s_ring;
static lv_obj_t *s_spinner;
static lv_obj_t *s_bars_box;
static lv_obj_t *s_bars[NUM_BARS];

static lv_style_t s_st_screen;
static lv_style_t s_st_clock;
static lv_style_t s_st_accent_bg;
static lv_style_t s_st_accent_border;

static ui_conv_t     s_conv = UI_CONV_IDLE;
static power_state_t s_power = POWER_ON;
static bool          s_swallow_click;
static int           s_last_min = -1;
static int           s_shift_idx;
static int           s_level_smooth;
static uint32_t      s_anim_phase;
static char          s_caption[CAPTION_MAX];
static char          s_hint[64];
static int           s_batt = -1;
static bool          s_charging;
static ui_link_t     s_link = UI_LINK_NO_WIFI;

/* Burn-in protection: the whole watchface drifts through these offsets. */
static const int8_t s_shift[][2] = {
    {0, 0}, {3, 0}, {3, 3}, {0, 3}, {-3, 3}, {-3, 0}, {-3, -3}, {0, -3}, {3, -3},
};

static lv_color_t hex(uint32_t rgb)
{
    return lv_color_hex(rgb);
}

/* ------------------------------------------------------------------------- */
/* Screen helpers                                                             */
/* ------------------------------------------------------------------------- */

lv_obj_t *ui_watch_screen(void)
{
    return s_scr;
}

void ui_load_screen(lv_obj_t *scr, bool to_left)
{
    if (lv_screen_active() == scr) {
        return;
    }
    lv_screen_load_anim(scr, to_left ? LV_SCR_LOAD_ANIM_MOVE_LEFT : LV_SCR_LOAD_ANIM_MOVE_RIGHT, 220, 0, false);
}

void ui_note_activity(void)
{
    if (s_disp) {
        lv_display_trigger_activity(s_disp);
    }
}

bool ui_consume_wake_tap(void)
{
    bool s = s_swallow_click;
    s_swallow_click = false;
    return s;
}

static void apply_brightness(void)
{
    switch (s_power) {
    case POWER_ON:
        board_display_power(true);
        board_display_set_brightness(g_ui_settings.brightness);
        break;
    case POWER_DIM:
        board_display_set_brightness(g_ui_settings.brightness < DIM_BRIGHTNESS ? g_ui_settings.brightness
                                                                             : DIM_BRIGHTNESS);
        break;
    case POWER_OFF:
        board_display_set_brightness(0);
        board_display_power(false);
        break;
    }
}

/* ------------------------------------------------------------------------- */
/* Watchface content                                                          */
/* ------------------------------------------------------------------------- */

static void update_clock(bool force)
{
    time_t now = time(NULL);
    struct tm tm;
    localtime_r(&now, &tm);
    bool valid = tm.tm_year >= 124;

    if (!force && valid && tm.tm_min == s_last_min) {
        return;
    }
    s_last_min = valid ? tm.tm_min : -1;

    char buf[48];
    if (!valid) {
        lv_label_set_text(s_lbl_time, "--:--");
        lv_label_set_text(s_lbl_date, "");
        return;
    }
    if (g_ui_settings.time_24h) {
        snprintf(buf, sizeof(buf), "%02d:%02d", tm.tm_hour, tm.tm_min);
    } else {
        int h = tm.tm_hour % 12;
        snprintf(buf, sizeof(buf), "%d:%02d", h == 0 ? 12 : h, tm.tm_min);
    }
    lv_label_set_text(s_lbl_time, buf);
    /* "Luni, 28 Septembrie" / "Monday, 28 September" */
    lv_label_set_text_fmt(s_lbl_date, "%s, %d %s", ui_weekday(tm.tm_wday), tm.tm_mday, ui_month(tm.tm_mon));

    /* Pixel shift once per minute. */
    s_shift_idx = (s_shift_idx + 1) % (int)(sizeof(s_shift) / sizeof(s_shift[0]));
    lv_obj_set_pos(s_content, s_shift[s_shift_idx][0], s_shift[s_shift_idx][1]);
}

static void update_status(void)
{
    if (!s_lbl_batt) {
        return;
    }
    const char *icon = ICON_BATTERY_EMPTY;
    if (s_batt >= 88) {
        icon = ICON_BATTERY_FULL;
    } else if (s_batt >= 63) {
        icon = ICON_BATTERY_3;
    } else if (s_batt >= 38) {
        icon = ICON_BATTERY_2;
    } else if (s_batt >= 13) {
        icon = ICON_BATTERY_1;
    }
    if (s_batt < 0) {
        lv_label_set_text(s_lbl_batt, s_charging ? ICON_CHARGE : "");
    } else {
        lv_label_set_text_fmt(s_lbl_batt, "%s%d%% %s", s_charging ? ICON_CHARGE " " : "", s_batt, icon);
    }
    lv_obj_set_style_text_color(s_lbl_batt, (s_batt >= 0 && s_batt < 15 && !s_charging)
                                ? lv_palette_main(LV_PALETTE_RED) : g_ui_theme.text, 0);

    lv_color_t wc = g_ui_theme.text;
    lv_opa_t opa = LV_OPA_COVER;
    if (s_link == UI_LINK_ONLINE) {
        wc = g_ui_theme.accent;
    } else if (s_link == UI_LINK_NO_WIFI) {
        wc = lv_palette_main(LV_PALETTE_RED);
        opa = LV_OPA_70;
    } else {
        opa = LV_OPA_60;
    }
    lv_obj_set_style_text_color(s_lbl_wifi, wc, 0);
    lv_obj_set_style_text_opa(s_lbl_wifi, opa, 0);
}

static void update_hint(void)
{
    const char *txt = s_hint;
    if (!txt[0] && s_conv == UI_CONV_LISTENING && !s_caption[0]) {
        txt = ui_str(STR_LISTENING);
    } else if (!txt[0] && s_conv == UI_CONV_THINKING && !s_caption[0]) {
        txt = ui_str(STR_THINKING);
    }
    lv_label_set_text(s_lbl_hint, txt);
}

static void show_caption(void)
{
    size_t len = strlen(s_caption);
    if (len <= CAPTION_SHOW) {
        lv_label_set_text(s_lbl_caption, s_caption);
    } else {
        /* Show the tail, starting at a word boundary (UTF-8 safe). */
        const char *p = s_caption + len - CAPTION_SHOW;
        while (*p && (((unsigned char)*p & 0xC0) == 0x80)) {
            p++;
        }
        const char *sp = strchr(p, ' ');
        if (sp && sp - p < 20) {
            p = sp + 1;
        }
        lv_label_set_text_fmt(s_lbl_caption, "…%s", p);
    }
    update_hint();
}

/* ------------------------------------------------------------------------- */
/* Conversation visuals                                                       */
/* ------------------------------------------------------------------------- */

static void apply_conv_visuals(void)
{
    bool listening = s_conv == UI_CONV_LISTENING;
    bool thinking = s_conv == UI_CONV_THINKING;
    bool speaking = s_conv == UI_CONV_SPEAKING;

    if (listening) {
        lv_obj_remove_flag(s_ring, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_ring, LV_OBJ_FLAG_HIDDEN);
    }
    if (thinking) {
        lv_obj_remove_flag(s_spinner, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_spinner, LV_OBJ_FLAG_HIDDEN);
    }
    if (speaking) {
        lv_obj_remove_flag(s_bars_box, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_lbl_mic, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_bars_box, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_lbl_mic, LV_OBJ_FLAG_HIDDEN);
    }
    /* Mic button fades while the server works; stays tappable (interrupt). */
    lv_obj_set_style_bg_opa(s_btn_mic, thinking ? LV_OPA_60 : LV_OPA_COVER, 0);
    update_hint();
}

/* 40 ms animation tick: ring follows mic level, bars follow speaker level. */
static void anim_timer_cb(lv_timer_t *t)
{
    if (s_conv != UI_CONV_LISTENING && s_conv != UI_CONV_SPEAKING) {
        return;
    }
    int level = g_ui_cb.get_audio_level ? g_ui_cb.get_audio_level() : 0;
    s_level_smooth = (s_level_smooth * 3 + level) / 4;
    s_anim_phase += 1;

    if (s_conv == UI_CONV_LISTENING) {
        /* Gentle idle pulse + level-driven growth. */
        int pulse = (lv_trigo_sin((int32_t)(s_anim_phase * 9) % 360) + 32767) * 8 / 65534;
        int size = RING_BASE_SIZE + pulse + s_level_smooth * 45 / 100;
        lv_obj_set_size(s_ring, size, size);
        lv_obj_align_to(s_ring, s_btn_mic, LV_ALIGN_CENTER, 0, 0);
        lv_obj_set_style_border_opa(s_ring, (lv_opa_t)(120 + s_level_smooth * 135 / 100), 0);
    } else {
        for (int i = 0; i < NUM_BARS; i++) {
            int s = lv_trigo_sin((int32_t)(s_anim_phase * 17 + i * 70) % 360);   /* -32767..32767 */
            int amp = 10 + s_level_smooth * 55 / 100;
            int h = 14 + (amp * (s + 32767)) / 65534;
            lv_obj_set_height(s_bars[i], h);
        }
    }
}

/* 200 ms: screen power management. */
static void power_timer_cb(lv_timer_t *t)
{
    uint32_t inactive = lv_display_get_inactive_time(s_disp);
    uint32_t timeout = (uint32_t)g_ui_settings.screen_timeout_s * 1000;
    bool busy = s_conv != UI_CONV_IDLE;
    power_state_t want;
    if (busy || inactive < timeout) {
        want = POWER_ON;
    } else if (inactive < timeout + OFF_AFTER_DIM_MS) {
        want = POWER_DIM;
    } else {
        want = POWER_OFF;
    }
    if (want != s_power) {
        ESP_LOGD(TAG, "screen power %d -> %d", s_power, want);
        s_power = want;
        apply_brightness();
    }
}

static void clock_timer_cb(lv_timer_t *t)
{
    update_clock(false);
}

/* ------------------------------------------------------------------------- */
/* Events                                                                     */
/* ------------------------------------------------------------------------- */

static void mic_event_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_PRESSED) {
        /* A tap on a dimmed / dark screen only wakes it. */
        s_swallow_click = (s_power != POWER_ON);
    } else if (code == LV_EVENT_CLICKED) {
        if (ui_consume_wake_tap()) {
            return;
        }
        if (g_ui_cb.on_mic_tap) {
            g_ui_cb.on_mic_tap();
        }
    }
}

static void screen_gesture_cb(lv_event_t *e)
{
    lv_dir_t dir = lv_indev_get_gesture_dir(lv_indev_active());
    if ((dir == LV_DIR_LEFT || dir == LV_DIR_TOP) && s_conv == UI_CONV_IDLE) {
        lv_indev_wait_release(lv_indev_active());
        ui_settings_open();
    }
}

/* ------------------------------------------------------------------------- */
/* Build                                                                      */
/* ------------------------------------------------------------------------- */

static void theme_styles_init(void)
{
    lv_style_init(&s_st_screen);
    lv_style_init(&s_st_clock);
    lv_style_init(&s_st_accent_bg);
    lv_style_init(&s_st_accent_border);
}

static void theme_styles_apply(void)
{
    lv_style_set_bg_color(&s_st_screen, g_ui_theme.background);
    lv_style_set_bg_opa(&s_st_screen, LV_OPA_COVER);
    lv_style_set_text_color(&s_st_screen, g_ui_theme.text);
    lv_style_set_text_font(&s_st_screen, &buddy_font_20);
    lv_style_set_text_color(&s_st_clock, g_ui_theme.clock);
    lv_style_set_bg_color(&s_st_accent_bg, g_ui_theme.accent);
    lv_style_set_bg_opa(&s_st_accent_bg, LV_OPA_COVER);
    lv_style_set_border_color(&s_st_accent_border, g_ui_theme.accent);
    lv_style_set_arc_color(&s_st_accent_border, g_ui_theme.accent);
    lv_obj_report_style_change(NULL);
}

/* Shared by the other screens (ui_screens.c) through lv_obj_add_style. */
lv_style_t *ui_style_screen(void)
{
    return &s_st_screen;
}

lv_style_t *ui_style_accent_bg(void)
{
    return &s_st_accent_bg;
}

static void build_watchface(void)
{
    s_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_scr, &s_st_screen, 0);
    lv_obj_remove_flag(s_scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_scr, screen_gesture_cb, LV_EVENT_GESTURE, NULL);

    s_content = lv_obj_create(s_scr);
    lv_obj_remove_style_all(s_content);
    lv_obj_set_size(s_content, BOARD_LCD_H_RES, BOARD_LCD_V_RES);
    lv_obj_remove_flag(s_content, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(s_content, LV_OBJ_FLAG_GESTURE_BUBBLE | LV_OBJ_FLAG_EVENT_BUBBLE);

    /* Status row: Wi-Fi + battery */
    s_lbl_wifi = lv_label_create(s_content);
    lv_label_set_text(s_lbl_wifi, ICON_WIFI);
    lv_obj_align(s_lbl_wifi, LV_ALIGN_TOP_MID, -70, 20);

    s_lbl_batt = lv_label_create(s_content);
    lv_label_set_text(s_lbl_batt, "");
    lv_obj_align(s_lbl_batt, LV_ALIGN_TOP_MID, 40, 20);

    /* Time */
    s_lbl_time = lv_label_create(s_content);
    lv_obj_add_style(s_lbl_time, &s_st_clock, 0);
    lv_obj_set_style_text_font(s_lbl_time, &buddy_font_clock, 0);
    lv_label_set_text(s_lbl_time, "--:--");
    lv_obj_align(s_lbl_time, LV_ALIGN_TOP_MID, 0, 58);

    /* Date */
    s_lbl_date = lv_label_create(s_content);
    lv_obj_set_style_text_font(s_lbl_date, &buddy_font_28, 0);
    lv_label_set_text(s_lbl_date, "");
    lv_obj_align(s_lbl_date, LV_ALIGN_TOP_MID, 0, 150);

    /* Hint line (connection state, listening / thinking) */
    s_lbl_hint = lv_label_create(s_content);
    lv_obj_set_style_text_opa(s_lbl_hint, LV_OPA_70, 0);
    lv_label_set_text(s_lbl_hint, "");
    lv_obj_align(s_lbl_hint, LV_ALIGN_TOP_MID, 0, 192);

    /* Caption (transcript / reply) */
    s_lbl_caption = lv_label_create(s_content);
    lv_obj_set_width(s_lbl_caption, 350);
    lv_obj_set_height(s_lbl_caption, 78);
    lv_label_set_long_mode(s_lbl_caption, LV_LABEL_LONG_CLIP);
    lv_obj_set_style_text_align(s_lbl_caption, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_text(s_lbl_caption, "");
    lv_obj_align(s_lbl_caption, LV_ALIGN_TOP_MID, 0, 222);

    /* Mic button */
    s_btn_mic = lv_button_create(s_content);
    lv_obj_remove_style_all(s_btn_mic);
    lv_obj_add_style(s_btn_mic, &s_st_accent_bg, 0);
    lv_obj_set_size(s_btn_mic, MIC_BTN_SIZE, MIC_BTN_SIZE);
    lv_obj_set_style_radius(s_btn_mic, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_transform_scale(s_btn_mic, 240, LV_STATE_PRESSED);
    lv_obj_set_style_transform_pivot_x(s_btn_mic, MIC_BTN_SIZE / 2, 0);
    lv_obj_set_style_transform_pivot_y(s_btn_mic, MIC_BTN_SIZE / 2, 0);
    lv_obj_align(s_btn_mic, LV_ALIGN_BOTTOM_MID, 0, -44);
    lv_obj_add_flag(s_btn_mic, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(s_btn_mic, mic_event_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(s_btn_mic, mic_event_cb, LV_EVENT_CLICKED, NULL);

    s_lbl_mic = lv_label_create(s_btn_mic);
    lv_obj_set_style_text_font(s_lbl_mic, &buddy_font_icon, 0);
    lv_obj_set_style_text_color(s_lbl_mic, lv_color_white(), 0);
    lv_label_set_text(s_lbl_mic, ICON_MIC);
    lv_obj_center(s_lbl_mic);

    /* Speaking bars (inside the button) */
    s_bars_box = lv_obj_create(s_btn_mic);
    lv_obj_remove_style_all(s_bars_box);
    lv_obj_set_size(s_bars_box, 110, 90);
    lv_obj_center(s_bars_box);
    lv_obj_remove_flag(s_bars_box, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_flex_flow(s_bars_box, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(s_bars_box, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    for (int i = 0; i < NUM_BARS; i++) {
        s_bars[i] = lv_obj_create(s_bars_box);
        lv_obj_remove_style_all(s_bars[i]);
        lv_obj_set_size(s_bars[i], 12, 20);
        lv_obj_set_style_radius(s_bars[i], 6, 0);
        lv_obj_set_style_bg_color(s_bars[i], lv_color_white(), 0);
        lv_obj_set_style_bg_opa(s_bars[i], LV_OPA_COVER, 0);
        lv_obj_remove_flag(s_bars[i], LV_OBJ_FLAG_CLICKABLE);
    }
    lv_obj_add_flag(s_bars_box, LV_OBJ_FLAG_HIDDEN);

    /* Listening ring (behind the button visually: drawn as a border only) */
    s_ring = lv_obj_create(s_content);
    lv_obj_remove_style_all(s_ring);
    lv_obj_add_style(s_ring, &s_st_accent_border, 0);
    lv_obj_set_size(s_ring, RING_BASE_SIZE, RING_BASE_SIZE);
    lv_obj_set_style_radius(s_ring, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(s_ring, 6, 0);
    lv_obj_set_style_border_opa(s_ring, LV_OPA_80, 0);
    lv_obj_remove_flag(s_ring, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_align_to(s_ring, s_btn_mic, LV_ALIGN_CENTER, 0, 0);
    lv_obj_add_flag(s_ring, LV_OBJ_FLAG_HIDDEN);

    /* Thinking spinner around the button */
    s_spinner = lv_spinner_create(s_content);
    lv_spinner_set_anim_params(s_spinner, 1000, 60);
    lv_obj_set_size(s_spinner, SPINNER_SIZE, SPINNER_SIZE);
    lv_obj_set_style_arc_width(s_spinner, 6, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(s_spinner, LV_OPA_20, LV_PART_MAIN);
    lv_obj_set_style_arc_width(s_spinner, 6, LV_PART_INDICATOR);
    lv_obj_add_style(s_spinner, &s_st_accent_border, LV_PART_INDICATOR);
    lv_obj_remove_flag(s_spinner, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_align_to(s_spinner, s_btn_mic, LV_ALIGN_CENTER, 0, 0);
    lv_obj_add_flag(s_spinner, LV_OBJ_FLAG_HIDDEN);

    lv_obj_move_foreground(s_btn_mic);
}

esp_err_t ui_init(lv_display_t *disp, const ui_callbacks_t *cb)
{
    s_disp = disp;
    if (cb) {
        g_ui_cb = *cb;
    }
    settings_get(&g_ui_settings);
    ui_i18n_set_language(g_ui_settings.language);
    g_ui_theme.accent = hex(g_ui_settings.theme.accent);
    g_ui_theme.background = hex(g_ui_settings.theme.background);
    g_ui_theme.clock = hex(g_ui_settings.theme.clock);
    g_ui_theme.text = hex(g_ui_settings.theme.text);

    LOCK();
    theme_styles_init();
    theme_styles_apply();
    build_watchface();
    ui_screens_init();
    update_clock(true);
    update_status();
    lv_screen_load(s_scr);
    lv_timer_create(clock_timer_cb, 1000, NULL);
    lv_timer_create(anim_timer_cb, 40, NULL);
    lv_timer_create(power_timer_cb, 200, NULL);
    UNLOCK();

    s_power = POWER_ON;
    apply_brightness();
    ESP_LOGI(TAG, "ui ready");
    return ESP_OK;
}

/* ------------------------------------------------------------------------- */
/* Public API                                                                 */
/* ------------------------------------------------------------------------- */

void ui_apply_settings(const buddy_settings_t *s)
{
    LOCK();
    bool lang_changed = strcmp(g_ui_settings.language, s->language) != 0;
    g_ui_settings = *s;
    ui_i18n_set_language(s->language);
    g_ui_theme.accent = hex(s->theme.accent);
    g_ui_theme.background = hex(s->theme.background);
    g_ui_theme.clock = hex(s->theme.clock);
    g_ui_theme.text = hex(s->theme.text);
    theme_styles_apply();
    update_clock(true);
    update_status();
    update_hint();
    ui_settings_refresh();
    ui_msg_refresh_theme();
    if (s_power == POWER_ON) {
        board_display_set_brightness(s->brightness);
    }
    (void)lang_changed;
    UNLOCK();
}

void ui_show_watchface(void)
{
    LOCK();
    ui_go_watchface();
    UNLOCK();
}

void ui_go_watchface(void)
{
    ui_load_screen(s_scr, false);
}

bool ui_is_watchface(void)
{
    return lv_screen_active() == s_scr;
}

void ui_set_conv_state(ui_conv_t st)
{
    LOCK();
    if (st != s_conv) {
        if (st == UI_CONV_LISTENING) {
            s_caption[0] = '\0';
            show_caption();
        }
        s_conv = st;
        if (st != UI_CONV_IDLE) {
            ui_go_watchface();
            ui_note_activity();
            if (s_power != POWER_ON) {
                s_power = POWER_ON;
                apply_brightness();
            }
        }
        apply_conv_visuals();
    }
    UNLOCK();
}

void ui_caption_clear(void)
{
    LOCK();
    s_caption[0] = '\0';
    show_caption();
    UNLOCK();
}

void ui_caption_set(const char *text)
{
    LOCK();
    strlcpy(s_caption, text ? text : "", sizeof(s_caption));
    show_caption();
    UNLOCK();
}

void ui_caption_append(const char *delta)
{
    if (!delta) {
        return;
    }
    LOCK();
    size_t len = strlen(s_caption);
    size_t add = strlen(delta);
    if (len + add >= sizeof(s_caption)) {
        /* keep the tail */
        size_t drop = len + add - sizeof(s_caption) + 1 + 64;
        if (drop > len) {
            drop = len;
        }
        memmove(s_caption, s_caption + drop, len - drop + 1);
    }
    strlcat(s_caption, delta, sizeof(s_caption));
    show_caption();
    UNLOCK();
}

void ui_set_hint(const char *text)
{
    LOCK();
    strlcpy(s_hint, text ? text : "", sizeof(s_hint));
    update_hint();
    UNLOCK();
}

void ui_set_status(int battery_pct, bool charging, ui_link_t link)
{
    LOCK();
    s_batt = battery_pct;
    s_charging = charging;
    s_link = link;
    update_status();
    UNLOCK();
}

void ui_wake(void)
{
    LOCK();
    ui_note_activity();
    if (s_power != POWER_ON) {
        s_power = POWER_ON;
        apply_brightness();
    }
    UNLOCK();
}

const char *ui_text_connecting(void)
{
    return ui_str(STR_CONNECTING);
}
