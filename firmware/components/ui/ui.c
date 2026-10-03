/*
 * ola - UI core: watchface, conversation states, theme, screen power
 * (dim / off after screen_timeout_s, tap wakes) and AMOLED pixel shift.
 */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "board.h"
#include "ui_priv.h"

static const char *TAG = "ui";

ui_theme_t       g_ui_theme;
buddy_settings_t g_ui_settings;
ui_callbacks_t   g_ui_cb;

#define LOCK()      board_display_lock(0)
#define UNLOCK()    board_display_unlock()

#define MIC_BTN_SIZE        140
#define MIC_CX              300     /* mic centre on the screen: right of the greeting, its top above the text's */
#define MIC_CY              256
#define RING_BASE_SIZE      164
#define SPINNER_SIZE        176
#define HERO_Y              158     /* pre-rendered art (halo, wave, mic circle): full width, y 158..419 */
#define HERO_H              262
#define WAVE_BLUE           0x4F8CFF /* the blue theme's accent: the greeting prompt's tint */
#define WAVE_LOW_Y          360     /* screen y of the wave's lowest point (under the mic, just below its halo) */
#define HALO_R              94      /* outer halo ring around the mic */
#define GREET_X             24      /* "Hi there!" / "How can I help you today?" left of the mic */
#define GREET_Y             196
#define GREET_W             184
#define SLIDE_MS            350     /* screen slide, slowing down towards the end */
#define NUM_BARS            5
#define DIM_BRIGHTNESS      30      /* % while dimmed */
#define CAPTION_MAX         600
#define CAPTION_SHOW        110     /* code points, ~3 lines at 20 px on 350 px */
#define CAPTION_W           350
#define CAPTION_H           78
#define CAPTION_LINE_SPACE  (-3)    /* Noto Sans 20 px: 28 px line -> 25 px pitch, 3 lines fit */
#define DATE_MAX_W          380     /* longer dates drop the weekday */
#define STATUS_W            300     /* top status row: Wi-Fi | hint | battery */
#define STATUS_HINT_W       170
#define SHORTCUT_W          80      /* notes / reminders / settings icons */
#define SHORTCUT_H          80
#define SHORTCUT_GAP        22
/* Centered vertically between the date line (ends ~y 185) and the mic button (starts at y 308). */
#define SHORTCUT_Y          394     /* centred between the wave and the bottom edge */

typedef enum { POWER_ON, POWER_DIM, POWER_OFF } power_state_t;

static lv_display_t *s_disp;
static lv_obj_t *s_scr;
static lv_obj_t *s_content;
static lv_obj_t *s_lbl_wifi;
static lv_obj_t *s_lbl_batt;
static lv_obj_t *s_time_box;            /* clock: hours | colon | minutes, colon pinned in place */
static lv_obj_t *s_time_hours;
static lv_obj_t *s_time_minutes;
static lv_obj_t *s_lbl_date;
static lv_obj_t *s_lbl_hint;
static lv_obj_t *s_shortcuts;
enum { SHORTCUT_NOTES, SHORTCUT_REMINDERS, SHORTCUT_SETTINGS };
static lv_obj_t *s_shortcut_badge[2];   /* [SHORTCUT_NOTES], [SHORTCUT_REMINDERS] (active ones) */
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
static lv_obj_t  *s_hero_art;            /* watchface: wave, halo and mic circle, pre-rendered */
static uint8_t   *s_hero_buf;            /* its pixels (PSRAM), redrawn on theme / thinking changes */
static lv_obj_t  *s_greet;               /* "Hi there!" + "How can I help you today?" */
static lv_obj_t  *s_lbl_hello;
static lv_obj_t  *s_lbl_help;
static bool       s_mic_dim;             /* mic circle drawn faded (server thinking) */
static lv_obj_t  *s_shortcut_art[3];     /* watchface: shortcut circles, pre-rendered, one shared buffer */
static uint8_t   *s_shortcut_buf;
static int        s_shortcut_count;
static lv_obj_t  *s_shortcut_lbl[3];     /* their icons (dark on a light circle) */

static void mic_render(void);
static void update_greeting(void);
static void mic_fg_apply(void);

static ui_conv_t     s_conv = UI_CONV_IDLE;
static power_state_t s_power = POWER_ON;
static bool          s_swallow_click;
static bool          s_hold_awake;      /* reminder alert on screen */
static int           s_last_min = -1;
static int           s_shift_idx;
static int           s_level_smooth;
static uint32_t      s_anim_phase;
static char          s_caption[CAPTION_MAX];
static char          s_caption_view[CAPTION_MAX];  /* sanitized copy handed to the label */
static bool          s_caption_visible;
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
    lv_obj_t *old = lv_screen_active();
    lv_screen_load_anim(scr, to_left ? LV_SCR_LOAD_ANIM_MOVE_LEFT : LV_SCR_LOAD_ANIM_MOVE_RIGHT, SLIDE_MS, 0, false);
    /* LVGL moves the screens at a constant speed; slow them down towards the end instead. */
    lv_obj_t *moving[2] = { scr, old };
    for (int i = 0; i < 2; i++) {
        lv_anim_t *a = moving[i] ? lv_anim_get(moving[i], NULL) : NULL;
        if (a) {
            a->path_cb = lv_anim_path_ease_out;
        }
    }
}

void ui_note_activity(void)
{
    if (s_disp) {
        lv_display_trigger_activity(s_disp);
    }
}

bool ui_is_awake(void)
{
    return ui_screen_awake();
}

bool ui_screen_awake(void)
{
    return s_power == POWER_ON;
}

void ui_hold_awake(bool hold)
{
    s_hold_awake = hold;
    if (hold) {
        ui_note_activity();
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

/* "12:34" / "9:05" / "--:--": hours right of their box, minutes left of theirs. */
static void set_time_text(const char *t)
{
    const char *colon = strchr(t, ':');
    char hours[4] = "";
    if (colon && colon - t < (ptrdiff_t)sizeof(hours)) {
        memcpy(hours, t, (size_t)(colon - t));
        hours[colon - t] = '\0';
    }
    lv_label_set_text(s_time_hours, hours);
    lv_label_set_text(s_time_minutes, colon ? colon + 1 : "");
}

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
        set_time_text("--:--");
        lv_label_set_text(s_lbl_date, "");
        return;
    }
    snprintf(buf, sizeof(buf), "%02d:%02d", tm.tm_hour, tm.tm_min);     /* always 24-hour */
    set_time_text(buf);
    /* "Monday, 28 September" / "Montag, 28. September"; drop the weekday if too wide. */
    char date[64];
    ui_format_date(date, sizeof(date), tm.tm_wday, tm.tm_mday, tm.tm_mon, true);
    lv_point_t sz;
    lv_text_get_size(&sz, date, &buddy_font_20, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    if (sz.x > DATE_MAX_W) {
        ui_format_date(date, sizeof(date), tm.tm_wday, tm.tm_mday, tm.tm_mon, false);
    }
    lv_label_set_text(s_lbl_date, date);

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
    char batt[32];
    if (s_batt < 0) {
        strlcpy(batt, s_charging ? ICON_CHARGE : "", sizeof(batt));
    } else {
        snprintf(batt, sizeof(batt), "%s%d%% %s", s_charging ? ICON_CHARGE " " : "", s_batt, icon);
    }
    lv_label_set_text(s_lbl_batt, batt);
    const lv_color_t batt_color = (s_batt >= 0 && s_batt < 15 && !s_charging)
                                  ? lv_palette_main(LV_PALETTE_RED) : g_ui_theme.text;
    lv_obj_set_style_text_color(s_lbl_batt, batt_color, 0);

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
    ui_settings_status(batt, batt_color, wc, opa);
    ui_items_status(batt, batt_color);
}

static void update_hint(void)
{
    const char *txt = s_hint;
    if (!txt[0] && s_conv == UI_CONV_LISTENING && !s_caption_visible) {
        txt = ui_str(STR_LISTENING);
    } else if (!txt[0] && s_conv == UI_CONV_THINKING && !s_caption_visible) {
        txt = ui_str(STR_THINKING);
    }
    lv_label_set_text(s_lbl_hint, txt);
}

/* Decode one UTF-8 code point and advance *p. Malformed input -> U+FFFD. */
static uint32_t utf8_next(const char **p)
{
    const unsigned char *s = (const unsigned char *)*p;
    uint32_t c = s[0];
    int n;
    if (c < 0x80) {
        n = 0;
    } else if ((c & 0xE0) == 0xC0) {
        c &= 0x1F;
        n = 1;
    } else if ((c & 0xF0) == 0xE0) {
        c &= 0x0F;
        n = 2;
    } else if ((c & 0xF8) == 0xF0) {
        c &= 0x07;
        n = 3;
    } else {
        *p += 1;
        return 0xFFFD;
    }
    for (int i = 1; i <= n; i++) {
        if ((s[i] & 0xC0) != 0x80) {
            *p += i;
            return 0xFFFD;
        }
        c = (c << 6) | (s[i] & 0x3F);
    }
    *p += n + 1;
    return c;
}

/* Spaces other than ' ' / '\n' / NBSP: shown as a plain space. */
static bool is_other_space(uint32_t c)
{
    return c == '\t' || (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 ||
           c == 0x202F || c == 0x205F || c == 0x3000;
}

/* Control / zero-width / format characters: dropped from the caption. */
static bool is_invisible(uint32_t c)
{
    return (c < 0x20 && c != '\n') || (c >= 0x7F && c < 0xA0) || (c >= 0x200B && c <= 0x200F) ||
           (c >= 0x2060 && c <= 0x2064) || (c >= 0xFE00 && c <= 0xFE0F) || c == 0xFEFF;
}

/* Copy s_caption into s_caption_view (spaces normalized, invisible characters
 * dropped). Returns false if the caption font lacks a glyph for any visible
 * character (e.g. CJK, Arabic, emoji) - the caption is then hidden instead of
 * showing boxes. */
static bool caption_sanitize(const lv_font_t *font)
{
    const char *in = s_caption;
    char *out = s_caption_view;
    while (*in) {
        const char *start = in;
        uint32_t c = utf8_next(&in);
        if (is_other_space(c)) {
            *out++ = ' ';
            continue;
        }
        if (is_invisible(c)) {
            continue;
        }
        if (c != ' ' && c != '\n') {
            lv_font_glyph_dsc_t g;
            if (!lv_font_get_glyph_dsc(font, &g, c, 0) || g.is_placeholder) {
                s_caption_view[0] = '\0';
                return false;
            }
        }
        memcpy(out, start, (size_t)(in - start));
        out += in - start;
    }
    *out = '\0';
    return true;
}

static void show_caption(void)
{
    static char tail[CAPTION_SHOW * 4 + 8];
    const lv_font_t *font = &buddy_font_20;

    s_caption_visible = caption_sanitize(font) && s_caption_view[0];
    if (!s_caption_visible) {
        lv_label_set_text(s_lbl_caption, "");
        lv_obj_add_flag(s_lbl_caption, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_greet, LV_OBJ_FLAG_HIDDEN);
        update_hint();
        return;
    }
    lv_obj_remove_flag(s_lbl_caption, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_greet, LV_OBJ_FLAG_HIDDEN);      /* the caption takes the greeting's place */

    /* Show the tail: the last CAPTION_SHOW code points (UTF-8 safe), starting
     * at a word boundary, trimmed further until it fits the caption box. */
    const char *view = s_caption_view;
    const char *p = view + strlen(view);
    int cps = 0;
    while (p > view && cps < CAPTION_SHOW) {
        p--;
        if (((unsigned char)*p & 0xC0) != 0x80) {
            cps++;
        }
    }
    bool cut = p > view;
    if (cut) {
        const char *sp = strchr(p, ' ');
        if (sp && sp - p < 20) {
            p = sp + 1;
        }
    }
    for (;;) {
        snprintf(tail, sizeof(tail), "%s%s", cut ? "…" : "", p);
        lv_point_t sz;
        lv_text_get_size(&sz, tail, font, 0, CAPTION_LINE_SPACE, CAPTION_W, LV_TEXT_FLAG_NONE);
        const char *sp = strchr(p, ' ');
        if (sz.y <= CAPTION_H || !sp || !sp[1]) {
            break;
        }
        p = sp + 1;         /* drop the first word and measure again */
        cut = true;
    }
    lv_label_set_text(s_lbl_caption, tail);
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
    /* Mic button fades while the server works; stays tappable (interrupt). Its circle is
     * pre-rendered, so redraw that (once per change, not per frame). */
    if (s_mic_dim != thinking) {
        s_mic_dim = thinking;
        mic_render();
    }
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
    ui_chat_anim(s_level_smooth, s_anim_phase);

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
    bool busy = s_conv != UI_CONV_IDLE || s_hold_awake;
    power_state_t want;
    if (busy || inactive < timeout) {
        want = POWER_ON;
    } else {
        want = POWER_DIM;           /* stays dimmed: the panel never switches off on its own */
    }
    if (want != s_power) {
        ESP_LOGD(TAG, "screen power %d -> %d", s_power, want);
        if (want == POWER_DIM && s_conv == UI_CONV_IDLE && s_caption[0]) {
            s_caption[0] = '\0';        /* the last reply clears when the screen dims */
            show_caption();
        }
        /* The dimmed screen stays on: only the watchface has burn-in pixel shift, so lists and
         * settings give way to it. The chat and message screens (pairing code...) stay until the
         * user leaves them. */
        if (want == POWER_DIM && !ui_is_watchface() && !ui_msg_is_active() && !ui_chat_is_active()) {
            ui_go_watchface();
        }
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

static void shortcut_event_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_PRESSED) {
        s_swallow_click = (s_power != POWER_ON);     /* a tap on a dim screen only wakes it */
    } else if (code == LV_EVENT_CLICKED && !ui_consume_wake_tap() && s_conv == UI_CONV_IDLE) {
        int which = (int)(intptr_t)lv_event_get_user_data(e);
        if (which == SHORTCUT_SETTINGS) {
            ui_settings_open();
        } else {
            ui_items_open(which == SHORTCUT_REMINDERS);
        }
    }
}

static lv_obj_t *add_shortcut(lv_obj_t *parent, const char *icon, int which)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_set_size(b, SHORTCUT_W, SHORTCUT_H);
    lv_obj_set_ext_click_area(b, 6);
    lv_obj_add_flag(b, LV_OBJ_FLAG_GESTURE_BUBBLE);
    /* gradient circle with a light rim, pre-rendered (see shortcuts_render); white icon; shrinks a
     * little while pressed */
    if (s_shortcut_buf && s_shortcut_count < 3) {
        lv_obj_t *art = lv_canvas_create(b);
        lv_canvas_set_buffer(art, s_shortcut_buf, SHORTCUT_W, SHORTCUT_H, LV_COLOR_FORMAT_ARGB8888);
        lv_obj_remove_flag(art, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(art, LV_OBJ_FLAG_GESTURE_BUBBLE);
        s_shortcut_art[s_shortcut_count++] = art;
    }
    lv_obj_set_style_transform_scale(b, 230, LV_STATE_PRESSED);
    lv_obj_set_style_transform_pivot_x(b, SHORTCUT_W / 2, 0);
    lv_obj_set_style_transform_pivot_y(b, SHORTCUT_H / 2, 0);
    lv_obj_add_event_cb(b, shortcut_event_cb, LV_EVENT_PRESSED, (void *)(intptr_t)which);
    lv_obj_add_event_cb(b, shortcut_event_cb, LV_EVENT_CLICKED, (void *)(intptr_t)which);
    lv_obj_t *l = lv_label_create(b);
    lv_obj_set_style_text_font(l, &buddy_font_shortcut, 0);
    lv_obj_set_style_text_color(l, lv_color_white(), 0);
    lv_label_set_text(l, icon);
    lv_obj_center(l);
    if (s_shortcut_count > 0 && s_shortcut_count <= 3) {
        s_shortcut_lbl[s_shortcut_count - 1] = l;
    }

    /* Count in the top-right corner, positioned on its own so the icon never moves. */
    lv_obj_t *badge = lv_label_create(b);
    lv_obj_set_style_text_font(badge, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(badge, lv_color_white(), 0);
    lv_obj_set_style_text_align(badge, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_bg_color(badge, lv_palette_main(LV_PALETTE_RED), 0);
    lv_obj_set_style_bg_opa(badge, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(badge, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_min_width(badge, 20, 0);                   /* round for 1 digit, pill for more */
    lv_obj_set_style_pad_hor(badge, 5, 0);
    lv_obj_set_style_pad_ver(badge, 2, 0);
    lv_obj_add_flag(badge, LV_OBJ_FLAG_FLOATING | LV_OBJ_FLAG_HIDDEN);
    lv_label_set_text(badge, "");
    lv_obj_align(badge, LV_ALIGN_TOP_RIGHT, 0, 0);
    if (which == SHORTCUT_SETTINGS) {
        lv_obj_delete(badge);   /* no counter on the gear */
    } else {
        s_shortcut_badge[which] = badge;
    }
    return b;
}

void ui_shortcut_counts(int notes, int reminders)
{
    const int counts[2] = { notes, reminders };
    for (int i = 0; i < 2; i++) {
        if (!s_shortcut_badge[i]) {
            continue;
        }
        if (counts[i] > 0) {
            lv_label_set_text_fmt(s_shortcut_badge[i], "%d", counts[i]);
            lv_obj_remove_flag(s_shortcut_badge[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(s_shortcut_badge[i], LV_OBJ_FLAG_HIDDEN);
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

/* A circle of diameter d centred on (cx, cy): vertical gradient top -> bottom, then a 2 px rim. */
static void draw_circle(lv_layer_t *layer, int32_t cx, int32_t cy, int32_t d, lv_color_t top,
                        lv_color_t bottom, lv_opa_t bg_opa, lv_color_t rim, lv_opa_t rim_opa)
{
    lv_draw_rect_dsc_t r;
    lv_draw_rect_dsc_init(&r);
    r.radius = LV_RADIUS_CIRCLE;
    r.bg_opa = bg_opa;
    r.bg_color = top;
    r.bg_grad.dir = LV_GRAD_DIR_VER;
    r.bg_grad.stops_count = 2;
    r.bg_grad.stops[0] = (lv_grad_stop_t){ .color = top, .opa = LV_OPA_COVER, .frac = 0 };
    r.bg_grad.stops[1] = (lv_grad_stop_t){ .color = bottom, .opa = LV_OPA_COVER, .frac = 255 };
    r.border_width = 2;
    r.border_color = rim;
    r.border_opa = rim_opa;
    const lv_area_t area = { cx - d / 2, cy - d / 2, cx - d / 2 + d - 1, cy - d / 2 + d - 1 };
    lv_draw_rect(layer, &r, &area);
}

/* A filled circle of radius r centred on (cx, cy), with a rim. Colours are opaque: the art is drawn on
 * the plain background, so "faint" means mixed towards the background, not transparent. */
static void fill_circle(lv_layer_t *layer, int32_t cx, int32_t cy, int32_t r, lv_color_t fill, int32_t rim_w,
                        lv_color_t rim)
{
    lv_draw_rect_dsc_t d;
    lv_draw_rect_dsc_init(&d);
    d.radius = LV_RADIUS_CIRCLE;
    d.bg_color = fill;
    d.bg_opa = LV_OPA_COVER;
    d.border_width = rim_w;
    d.border_color = rim;
    d.border_opa = LV_OPA_COVER;
    const lv_area_t area = { cx - r, cy - r, cx + r - 1, cy + r - 1 };
    lv_draw_rect(layer, &d, &area);
}

/* The white theme ("mono"): its own wave artwork and white greeting text. */
static bool theme_is_white(void)
{
    return strcmp(g_ui_settings.theme.preset, "mono") == 0;
}

/* Blend the wave artwork (ui_wave_img.c: wave.png, or wave-white.png in the white theme) into the art's
 * pixels, its lowest point at WAVE_LOW_Y. Drawn over the background and the halo, under the mic circle. */
static void wave_paint(void)
{
    const uint16_t *img_rgb = theme_is_white() ? g_wave_white_rgb565 : g_wave_img_rgb565;
    const uint8_t *img_alpha = theme_is_white() ? g_wave_white_alpha : g_wave_img_alpha;
    lv_draw_buf_t *buf = lv_canvas_get_draw_buf(s_hero_art);
    const uint32_t stride = buf->header.stride / 2;
    uint16_t *px = (uint16_t *)buf->data;
    const int32_t top = WAVE_LOW_Y - g_wave_img_low_y - HERO_Y;     /* first band row, in art rows */

    for (int32_t r = 0; r < g_wave_img_h; r++) {
        const int32_t y = top + r;
        if (y < 0 || y >= HERO_H) {
            continue;
        }
        for (int32_t x = 0; x < g_wave_img_w && x < BOARD_LCD_H_RES; x++) {
            const uint32_t i = (uint32_t)r * g_wave_img_w + x;
            const uint8_t al = img_alpha[i];
            if (!al) {
                continue;
            }
            uint16_t *p = &px[y * stride + x];
            const uint16_t w = img_rgb[i];
            if (al == 255) {
                *p = w;
                continue;
            }
            const lv_color_t under = lv_color_make((uint8_t)(((*p >> 11) & 0x1F) << 3),
                                                   (uint8_t)(((*p >> 5) & 0x3F) << 2), (uint8_t)((*p & 0x1F) << 3));
            const lv_color_t over = lv_color_make((uint8_t)(((w >> 11) & 0x1F) << 3),
                                                  (uint8_t)(((w >> 5) & 0x3F) << 2), (uint8_t)((w & 0x1F) << 3));
            *p = lv_color_to_u16(lv_color_mix(over, under, al));
        }
    }
}

/* The watchface art behind the greeting and the mic: halo rings, the wave (wave_paint) and the
 * mic circle. Too costly to draw every frame (listening ring, screen slides), so it is drawn once into a
 * canvas and then only copied; redrawn on theme changes and when the mic dims (server thinking). The
 * mic button on top of it is transparent. Colours are opaque, mixed towards the plain background.
 * Caller holds the lock. */
static void hero_render(void)
{
    if (!s_hero_art) {
        return;
    }
    const lv_color_t a = g_ui_theme.accent, bg = g_ui_theme.background, white = lv_color_white();
    const int32_t cx = MIC_CX, cy = MIC_CY - HERO_Y;
    lv_canvas_fill_bg(s_hero_art, bg, LV_OPA_COVER);

    /* halo around the mic: a soft glow disc and a ring */
    lv_layer_t layer;
    lv_canvas_init_layer(s_hero_art, &layer);
    fill_circle(&layer, cx, cy, HALO_R, lv_color_mix(a, bg, 22), 2, lv_color_mix(a, bg, 60));
    fill_circle(&layer, cx, cy, HALO_R - 14, lv_color_mix(a, bg, 38), 2, lv_color_mix(a, bg, 105));
    lv_canvas_finish_layer(s_hero_art, &layer);

    wave_paint();

    /* the mic circle: light accent at the top to the accent at the bottom, light rim */
    lv_canvas_init_layer(s_hero_art, &layer);
    draw_circle(&layer, cx, cy, MIC_BTN_SIZE, lv_color_mix(white, a, 80), lv_color_mix(a, lv_color_black(), 230),
                s_mic_dim ? LV_OPA_60 : LV_OPA_COVER, lv_color_mix(white, a, 110), LV_OPA_80);
    lv_canvas_finish_layer(s_hero_art, &layer);
    lv_obj_invalidate(s_hero_art);
    mic_fg_apply();
}

static void mic_render(void)
{
    hero_render();
}

/* The mic icon / speaking bars: dark when the mic circle is light (e.g. the white "mono" accent). */
static void mic_fg_apply(void)
{
    const lv_color_t a = g_ui_theme.accent;
    const lv_color_t mid = lv_color_mix(lv_color_mix(lv_color_white(), a, 80), lv_color_mix(a, lv_color_black(), 230), 128);
    const lv_color_t fg = ui_on_color(mid);
    if (s_lbl_mic) {
        lv_obj_set_style_text_color(s_lbl_mic, fg, 0);
    }
    for (int i = 0; i < NUM_BARS; i++) {
        if (s_bars[i]) {
            lv_obj_set_style_bg_color(s_bars[i], fg, 0);
        }
    }
}

/* The three shortcut circles look the same: drawn once into one buffer that all three canvases
 * show. They sit on the plain background, so the corners are filled with it. Caller holds the lock. */
static void shortcuts_render(void)
{
    if (!s_shortcut_count) {
        return;
    }
    const lv_color_t a = g_ui_theme.accent, bg = g_ui_theme.background;
    lv_canvas_fill_bg(s_shortcut_art[0], bg, LV_OPA_TRANSP);
    lv_layer_t layer;
    lv_canvas_init_layer(s_shortcut_art[0], &layer);
    /* solid dark-blue circle (the accent towards the background), rim in a slightly paler accent */
    draw_circle(&layer, SHORTCUT_W / 2, SHORTCUT_H / 2, SHORTCUT_W, lv_color_mix(a, bg, 95),
                lv_color_mix(a, bg, 60), LV_OPA_COVER, lv_color_mix(lv_color_white(), a, 60), LV_OPA_COVER);
    lv_canvas_finish_layer(s_shortcut_art[0], &layer);
    for (int i = 1; i < s_shortcut_count; i++) {
        lv_obj_invalidate(s_shortcut_art[i]);
    }
    const lv_color_t fg = ui_on_color(lv_color_mix(lv_color_mix(a, bg, 95), lv_color_mix(a, bg, 60), 128));
    for (int i = 0; i < 3; i++) {
        if (s_shortcut_lbl[i]) {
            lv_obj_set_style_text_color(s_shortcut_lbl[i], fg, 0);
        }
    }
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
    lv_style_set_text_color(&s_st_accent_bg, ui_on_color(g_ui_theme.accent));
    lv_style_set_border_color(&s_st_accent_border, g_ui_theme.accent);
    lv_style_set_arc_color(&s_st_accent_border, g_ui_theme.accent);

    mic_render();
    shortcuts_render();
    lv_obj_report_style_change(NULL);
}

lv_color_t ui_on_color(lv_color_t bg)
{
    return lv_color_luminance(bg) > 150 ? lv_color_hex(0x111318) : lv_color_white();
}

lv_obj_t *ui_add_close_x(lv_obj_t *scr, lv_event_cb_t cb)
{
    lv_obj_t *x = lv_button_create(scr);
    lv_obj_remove_style_all(x);
    lv_obj_add_flag(x, LV_OBJ_FLAG_FLOATING);
    lv_obj_remove_flag(x, LV_OBJ_FLAG_SCROLL_ON_FOCUS);
    lv_obj_set_size(x, 72, 72);
    /* Alignment is relative to the parent's padded content area: undo its padding, so the X sits 22 px
     * from the right edge and 2 px from the top on every screen. */
    lv_obj_align(x, LV_ALIGN_TOP_RIGHT, -22 + lv_obj_get_style_pad_right(scr, 0), 2 - lv_obj_get_style_pad_top(scr, 0));
    lv_obj_set_ext_click_area(x, 28);
    lv_obj_add_event_cb(x, cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *l = lv_label_create(x);
    lv_obj_set_style_text_font(l, &buddy_font_28, 0);
    lv_obj_set_style_text_color(l, lv_color_white(), 0);
    lv_label_set_text(l, ICON_CLOSE);
    lv_obj_center(l);
    return x;
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

lv_style_t *ui_style_accent_border(void)
{
    return &s_st_accent_border;
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

    /* Status row at the top, fixed slots so nothing moves when one is empty:
     * Wi-Fi (left) | hint, e.g. "Connecting…" (middle) | battery (right). */
    lv_obj_t *status = lv_obj_create(s_content);
    lv_obj_remove_style_all(status);
    lv_obj_set_size(status, STATUS_W, 30);
    lv_obj_remove_flag(status, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(status, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_align(status, LV_ALIGN_TOP_MID, 0, 18);

    s_lbl_wifi = lv_label_create(status);
    lv_label_set_text(s_lbl_wifi, ICON_WIFI);
    lv_obj_align(s_lbl_wifi, LV_ALIGN_LEFT_MID, 0, 0);

    s_lbl_hint = lv_label_create(status);
    lv_obj_set_width(s_lbl_hint, STATUS_HINT_W);
    lv_label_set_long_mode(s_lbl_hint, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(s_lbl_hint, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_opa(s_lbl_hint, LV_OPA_70, 0);
    lv_label_set_text(s_lbl_hint, "");
    lv_obj_align(s_lbl_hint, LV_ALIGN_CENTER, 0, 0);

    s_lbl_batt = lv_label_create(status);
    lv_label_set_text(s_lbl_batt, "");
    lv_obj_align(s_lbl_batt, LV_ALIGN_RIGHT_MID, 0, 0);

    /* Time */
    s_time_box = lv_obj_create(s_content);
    lv_obj_remove_style_all(s_time_box);
    lv_obj_set_size(s_time_box, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_remove_flag(s_time_box, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(s_time_box, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_flex_flow(s_time_box, LV_FLEX_FLOW_ROW);
    lv_obj_align(s_time_box, LV_ALIGN_TOP_MID, 0, 58);
    /* hours ":" minutes, each as wide as its own digits: the whole time is centred horizontally on
     * what is actually shown ("11:11" is narrower than "08:48"). The box is aligned TOP_MID with
     * content size, so LVGL re-centres it whenever the digits change; the height never moves. */
    lv_obj_t *parts[3];
    for (int i = 0; i < 3; i++) {
        lv_obj_t *l = lv_label_create(s_time_box);
        lv_obj_add_style(l, &s_st_clock, 0);
        lv_obj_set_style_text_font(l, &buddy_font_clock_md, 0);
        parts[i] = l;
    }
    lv_label_set_text(parts[1], ":");
    s_time_hours = parts[0];
    s_time_minutes = parts[2];
    set_time_text("--:--");

    /* Date */
    s_lbl_date = lv_label_create(s_content);
    lv_obj_set_style_text_font(s_lbl_date, &buddy_font_20, 0);
    lv_label_set_text(s_lbl_date, "");
    lv_obj_align(s_lbl_date, LV_ALIGN_TOP_MID, 0, 120);

    /* Notes / reminders / settings shortcuts: a centred row at the bottom */
    s_shortcut_buf = heap_caps_malloc(SHORTCUT_W * SHORTCUT_H * 4, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_shortcuts = lv_obj_create(s_content);
    lv_obj_remove_style_all(s_shortcuts);
    lv_obj_set_size(s_shortcuts, SHORTCUT_W * 3 + SHORTCUT_GAP * 2, SHORTCUT_H);
    lv_obj_remove_flag(s_shortcuts, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(s_shortcuts, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_flex_flow(s_shortcuts, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(s_shortcuts, SHORTCUT_GAP, 0);
    lv_obj_align(s_shortcuts, LV_ALIGN_TOP_MID, 0, SHORTCUT_Y);
    add_shortcut(s_shortcuts, ICON_PEN, SHORTCUT_NOTES);
    add_shortcut(s_shortcuts, ICON_CALENDAR, SHORTCUT_REMINDERS);
    add_shortcut(s_shortcuts, ICON_SETTINGS, SHORTCUT_SETTINGS);
    shortcuts_render();

    /* Art behind the greeting and the mic: wave, halo, mic circle (see hero_render) */
    s_hero_buf = heap_caps_malloc(BOARD_LCD_H_RES * HERO_H * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_hero_buf) {
        s_hero_art = lv_canvas_create(s_content);
        lv_canvas_set_buffer(s_hero_art, s_hero_buf, BOARD_LCD_H_RES, HERO_H, LV_COLOR_FORMAT_RGB565);
        lv_obj_remove_flag(s_hero_art, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(s_hero_art, LV_OBJ_FLAG_GESTURE_BUBBLE);
        lv_obj_set_pos(s_hero_art, 0, HERO_Y);
    }

    /* Greeting, left of the mic */
    s_greet = lv_obj_create(s_content);
    lv_obj_remove_style_all(s_greet);
    lv_obj_set_size(s_greet, GREET_W, LV_SIZE_CONTENT);
    lv_obj_remove_flag(s_greet, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(s_greet, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_flex_flow(s_greet, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_greet, 4, 0);
    lv_obj_set_pos(s_greet, GREET_X, GREET_Y);
    s_lbl_hello = lv_label_create(s_greet);
    lv_obj_set_width(s_lbl_hello, GREET_W);
    lv_obj_set_style_text_font(s_lbl_hello, &buddy_font_28b, 0);
    lv_label_set_long_mode(s_lbl_hello, LV_LABEL_LONG_DOT);
    s_lbl_help = lv_label_create(s_greet);
    lv_obj_set_width(s_lbl_help, GREET_W);
    lv_obj_set_style_text_font(s_lbl_help, &buddy_font_20, 0);
    lv_obj_set_style_text_line_space(s_lbl_help, -2, 0);
    lv_label_set_long_mode(s_lbl_help, LV_LABEL_LONG_WRAP);
    update_greeting();

    /* Caption (transcript / reply), in the greeting's place */
    s_lbl_caption = lv_label_create(s_content);
    lv_obj_set_width(s_lbl_caption, CAPTION_W);
    lv_obj_set_height(s_lbl_caption, CAPTION_H);
    lv_obj_set_style_text_line_space(s_lbl_caption, CAPTION_LINE_SPACE, 0);
    lv_label_set_long_mode(s_lbl_caption, LV_LABEL_LONG_CLIP);
    lv_obj_set_style_text_align(s_lbl_caption, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_text(s_lbl_caption, "");
    lv_obj_set_pos(s_lbl_caption, GREET_X, GREET_Y);
    lv_obj_set_width(s_lbl_caption, GREET_W);
    lv_obj_set_style_text_align(s_lbl_caption, LV_TEXT_ALIGN_LEFT, 0);
    lv_obj_add_flag(s_lbl_caption, LV_OBJ_FLAG_HIDDEN);

    /* Mic button: transparent over its circle in the art (see hero_render); lightens while pressed */
    s_btn_mic = lv_button_create(s_content);
    lv_obj_remove_style_all(s_btn_mic);
    lv_obj_set_size(s_btn_mic, MIC_BTN_SIZE, MIC_BTN_SIZE);
    lv_obj_set_style_radius(s_btn_mic, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_btn_mic, lv_color_white(), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(s_btn_mic, LV_OPA_20, LV_STATE_PRESSED);
    lv_obj_set_pos(s_btn_mic, MIC_CX - MIC_BTN_SIZE / 2, MIC_CY - MIC_BTN_SIZE / 2);
    lv_obj_set_ext_click_area(s_btn_mic, 10);
    lv_obj_add_flag(s_btn_mic, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(s_btn_mic, mic_event_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(s_btn_mic, mic_event_cb, LV_EVENT_CLICKED, NULL);
    hero_render();

    s_lbl_mic = lv_label_create(s_btn_mic);
    lv_obj_set_style_text_font(s_lbl_mic, &buddy_font_mic, 0);
    lv_obj_set_style_text_color(s_lbl_mic, lv_color_white(), 0);
    lv_label_set_text(s_lbl_mic, ICON_MIC);
    lv_obj_center(s_lbl_mic);
    mic_fg_apply();

    /* Speaking bars (inside the button) */
    s_bars_box = lv_obj_create(s_btn_mic);
    lv_obj_remove_style_all(s_bars_box);
    lv_obj_set_size(s_bars_box, 84, 70);
    lv_obj_center(s_bars_box);
    lv_obj_remove_flag(s_bars_box, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_flex_flow(s_bars_box, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(s_bars_box, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    for (int i = 0; i < NUM_BARS; i++) {
        s_bars[i] = lv_obj_create(s_bars_box);
        lv_obj_remove_style_all(s_bars[i]);
        lv_obj_set_size(s_bars[i], 10, 18);
        lv_obj_set_style_radius(s_bars[i], 5, 0);
        lv_obj_set_style_bg_color(s_bars[i], lv_color_white(), 0);
        lv_obj_set_style_bg_opa(s_bars[i], LV_OPA_COVER, 0);
        lv_obj_remove_flag(s_bars[i], LV_OBJ_FLAG_CLICKABLE);
    }
    mic_fg_apply();
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

    lv_obj_move_foreground(s_shortcuts);     /* above the art, which reaches down to them */
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
    ui_items_init();
    ui_chat_init();
    ui_lang_init();
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
    /* Only redo what changed: a new theme restyles every screen and redraws the watchface art, a new
     * language re-texts them; a volume or brightness change (or the server echoing a change the watch
     * already applied) only updates the settings screen. */
    const bool theme_changed = memcmp(&s->theme, &g_ui_settings.theme, sizeof(s->theme)) != 0;
    const bool lang_changed = strcmp(s->language, g_ui_settings.language) != 0;
    g_ui_settings = *s;
    if (lang_changed) {
        ui_i18n_set_language(s->language);
    }
    if (theme_changed) {
        g_ui_theme.accent = hex(s->theme.accent);
        g_ui_theme.background = hex(s->theme.background);
        g_ui_theme.clock = hex(s->theme.clock);
        g_ui_theme.text = hex(s->theme.text);
        theme_styles_apply();
        update_status();
    }
    if (theme_changed || lang_changed) {
        update_clock(true);
        update_hint();
        update_greeting();
        ui_msg_refresh_theme();
        ui_items_refresh_theme();
        ui_chat_refresh_theme();
    }
    ui_settings_refresh();
    if (s_power == POWER_ON) {
        board_display_set_brightness(s->brightness);
    }
    UNLOCK();
}

static void update_greeting(void)
{
    if (!s_greet) {
        return;
    }
    lv_label_set_text(s_lbl_hello, ui_str(STR_HELLO));
    lv_label_set_text(s_lbl_help, ui_str(STR_HELP_PROMPT));
    /* white title; the prompt is light blue in the blue theme, white in the white one (like its wave) */
    lv_obj_set_style_text_color(s_lbl_hello, lv_color_white(), 0);
    lv_obj_set_style_text_color(s_lbl_help, theme_is_white() ? lv_color_white()
                                : lv_color_mix(lv_color_white(), lv_color_hex(WAVE_BLUE), 110), 0);
    lv_obj_set_style_text_color(s_lbl_date, lv_color_mix(lv_color_white(), g_ui_theme.accent, 190), 0);
}

void ui_set_reply_language(const char *lang)
{
    LOCK();
    if (ui_i18n_set_reply_language(lang)) {    /* "auto": menus and date follow the reply */
        update_clock(true);
        update_hint();
        update_greeting();
        ui_settings_refresh();
        ui_items_refresh_theme();
    }
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

/* ---- server notices (usage thresholds): shown only while no conversation runs ---- */
#define NOTICE_MAX          160
#define NOTICE_DELAY_MS     2500    /* after the conversation ends: let the last answer be read */
#define NOTICE_SHOW_MS      5000
static char        s_notice_text[NOTICE_MAX];
static char        s_notice_level[12];
static bool        s_notice_pending;
static lv_timer_t *s_notice_timer;

static void notice_timer_cb(lv_timer_t *t)
{
    s_notice_timer = NULL;
    if (!s_notice_pending || s_conv != UI_CONV_IDLE) {
        return;                         /* a new conversation started: wait for the next idle */
    }
    s_notice_pending = false;
    bool limit = strcmp(s_notice_level, "limit") == 0;
    bool warn = limit || strcmp(s_notice_level, "warning") == 0;
    lv_color_t color = limit ? lv_palette_main(LV_PALETTE_RED)
                             : (warn ? lv_palette_main(LV_PALETTE_AMBER) : g_ui_theme.accent);
    ui_msg_show(ICON_WARNING, color, ui_str(STR_CARE), s_notice_text, NULL, NULL, NULL, true, NOTICE_SHOW_MS);
}

/* Caller holds the lock. */
static void notice_schedule(void)
{
    if (!s_notice_pending || s_conv != UI_CONV_IDLE) {
        return;
    }
    if (s_notice_timer) {
        lv_timer_reset(s_notice_timer);
        return;
    }
    s_notice_timer = lv_timer_create(notice_timer_cb, NOTICE_DELAY_MS, NULL);
    lv_timer_set_repeat_count(s_notice_timer, 1);
}

void ui_show_notice(const char *json)
{
    cJSON *j = cJSON_Parse(json);
    const cJSON *text = j ? cJSON_GetObjectItemCaseSensitive(j, "text") : NULL;
    const cJSON *level = j ? cJSON_GetObjectItemCaseSensitive(j, "level") : NULL;
    if (cJSON_IsString(text) && text->valuestring[0]) {
        LOCK();
        strlcpy(s_notice_text, text->valuestring, sizeof(s_notice_text));
        strlcpy(s_notice_level, cJSON_IsString(level) ? level->valuestring : "info", sizeof(s_notice_level));
        s_notice_pending = true;
        notice_schedule();
        UNLOCK();
    }
    cJSON_Delete(j);
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
        ui_chat_set_state(st);      /* a turn opens the conversation screen */
        notice_schedule();          /* a waiting notice shows once the conversation is over */
        if (st != UI_CONV_IDLE) {
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
