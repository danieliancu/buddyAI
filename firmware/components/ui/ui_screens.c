/*
 * ola - quick settings screen and message screens (pairing, Wi-Fi setup,
 * errors, OTA).
 */
#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "board.h"
#include "ui_priv.h"

#define LOCK()      board_display_lock(0)
#define UNLOCK()    board_display_unlock()

/* Settings layout: cards on the theme background. Flat fills and 1-2 px borders only - no
 * shadows, gradients, glows or translucent layers, so scrolling and the screen slide stay cheap. */
#define CARD_W          386
#define CARD_PAD        12
#define CARD_INNER_W    (CARD_W - 2 * CARD_PAD)
#define ICON_COL_W      32
#define ROW_H           62
#define CHIP_H          44
#define THEME_DOT       48
#define SLIDER_W        90
#define SLIDER_TRACK_H  12
#define VALUE_W         54
#define LANG_PILL_W     100

/* ---- settings screen ---- */
static lv_obj_t *s_set_scr;
static lv_obj_t *s_set_title;
static lv_obj_t *s_set_wifi;
static lv_obj_t *s_set_batt;
static lv_obj_t *s_lbl_theme;
static lv_obj_t *s_theme_btns[8];
static int       s_theme_count;
static lv_obj_t *s_lbl_vol;
static lv_obj_t *s_lbl_bri;
static lv_obj_t *s_val_vol;
static lv_obj_t *s_val_bri;
static lv_obj_t *s_sld_vol;
static lv_obj_t *s_sld_bri;
static lv_obj_t *s_lbl_timeout;
static lv_obj_t *s_val_timeout;
static lv_obj_t *s_lbl_lang;
static lv_obj_t *s_btn_lang_en;     /* English */
static lv_obj_t *s_btn_lang_other;  /* "Other" or the chosen foreign language: opens the picker */
static lv_obj_t *s_lbl_lang_other;
static lv_obj_t *s_lbl_wifi_btn;

/* Colours derived from the theme, refreshed in ui_settings_refresh(). */
static lv_style_t s_st_card;
static lv_style_t s_st_card_pressed;
static lv_style_t s_st_icon;
static lv_style_t s_st_chip;
static lv_style_t s_st_chip_on;
static lv_style_t s_st_track;
static lv_style_t s_st_fill;
static lv_style_t s_st_knob;

/* Screen timeout choices, cycled by tapping the row (the server accepts 5..300 s). */
static const uint16_t s_timeouts[] = { 10, 15, 30, 60, 120, 300 };

/* ---- message screen ---- */
static lv_obj_t   *s_msg_scr;
static lv_obj_t   *s_msg_icon;
static lv_obj_t   *s_msg_title;
static lv_obj_t   *s_msg_body;
static lv_obj_t   *s_msg_code;
static lv_obj_t   *s_msg_btn;
static lv_obj_t   *s_msg_btn_lbl;
static void      (*s_msg_btn_cb)(void);
static bool        s_msg_dismissable;
static lv_timer_t *s_msg_timer;

/* ------------------------------------------------------------------------- */
/* Settings                                                                   */
/* ------------------------------------------------------------------------- */

static void send_change_int(const char *key, int value)
{
    if (!g_ui_cb.on_settings_change) {
        return;
    }
    cJSON *c = cJSON_CreateObject();
    cJSON_AddNumberToObject(c, key, value);
    g_ui_cb.on_settings_change(c);
    cJSON_Delete(c);
}

/* Only a real drag of a slider changes the setting. LVGL also moves the knob
 * to the touch point on a plain tap (and the extended click area catches taps
 * aimed at nearby buttons or at scrolling the screen); those are undone. */
static bool s_slider_dragged;

static int slider_setting(lv_obj_t *sld)
{
    return sld == s_sld_vol ? g_ui_settings.volume : g_ui_settings.brightness;
}

static void show_slider_value(lv_obj_t *sld, int v)
{
    lv_label_set_text_fmt(sld == s_sld_vol ? s_val_vol : s_val_bri, "%d%%", v);
}

static void slider_cb(lv_event_t *e)
{
    lv_obj_t *sld = lv_event_get_target(e);
    lv_event_code_t code = lv_event_get_code(e);
    int v = lv_slider_get_value(sld);
    switch (code) {
    case LV_EVENT_PRESSED:
        s_slider_dragged = false;
        break;
    case LV_EVENT_PRESSING:
        if (lv_slider_is_dragged(sld)) {
            s_slider_dragged = true;
        }
        break;
    case LV_EVENT_VALUE_CHANGED:
        if (s_slider_dragged) {
            show_slider_value(sld, v);
            if (sld == s_sld_bri) {
                board_display_set_brightness(v);    /* live preview */
            }
        }
        break;
    case LV_EVENT_RELEASED:
    case LV_EVENT_PRESS_LOST:
        if (code == LV_EVENT_RELEASED && s_slider_dragged && v != slider_setting(sld)) {
            send_change_int(sld == s_sld_vol ? "volume" : "brightness", v);
        } else {
            lv_slider_set_value(sld, slider_setting(sld), LV_ANIM_OFF);
            show_slider_value(sld, slider_setting(sld));
            if (sld == s_sld_bri) {
                board_display_set_brightness(g_ui_settings.brightness);
            }
        }
        s_slider_dragged = false;
        break;
    default:
        break;
    }
}

static void lang_en_cb(lv_event_t *e)
{
    if (strcmp(g_ui_settings.language, "en") == 0 || !g_ui_cb.on_settings_change) {
        return;
    }
    cJSON *c = cJSON_CreateObject();
    cJSON_AddStringToObject(c, "language", "en");
    g_ui_cb.on_settings_change(c);
    cJSON_Delete(c);
}

static void lang_other_cb(lv_event_t *e)
{
    ui_lang_open();
}

static void theme_cb(lv_event_t *e)
{
    const char *name = lv_event_get_user_data(e);
    settings_theme_t t;
    if (!name || !settings_theme_preset(name, &t) || !g_ui_cb.on_settings_change) {
        return;
    }
    char buf[8];
    cJSON *c = cJSON_CreateObject();
    cJSON *th = cJSON_AddObjectToObject(c, "theme");
    cJSON_AddStringToObject(th, "preset", t.preset);
    snprintf(buf, sizeof(buf), "#%06lX", (unsigned long)t.accent);
    cJSON_AddStringToObject(th, "accent", buf);
    snprintf(buf, sizeof(buf), "#%06lX", (unsigned long)t.background);
    cJSON_AddStringToObject(th, "background", buf);
    snprintf(buf, sizeof(buf), "#%06lX", (unsigned long)t.clock);
    cJSON_AddStringToObject(th, "clock", buf);
    snprintf(buf, sizeof(buf), "#%06lX", (unsigned long)t.text);
    cJSON_AddStringToObject(th, "text", buf);
    g_ui_cb.on_settings_change(c);
    cJSON_Delete(c);
}

static void timeout_cb(lv_event_t *e)
{
    const int n = (int)(sizeof(s_timeouts) / sizeof(s_timeouts[0]));
    int next = s_timeouts[0];
    for (int i = 0; i < n; i++) {
        if (s_timeouts[i] > g_ui_settings.screen_timeout_s) {
            next = s_timeouts[i];
            break;
        }
    }
    send_change_int("screen_timeout_s", next);
}

static void wifi_btn_cb(lv_event_t *e)
{
    if (g_ui_cb.on_wifi_setup) {
        g_ui_cb.on_wifi_setup();
    }
}

static void settings_gesture_cb(lv_event_t *e)
{
    lv_dir_t dir = lv_indev_get_gesture_dir(lv_indev_active());
    if (dir == LV_DIR_RIGHT || dir == LV_DIR_BOTTOM) {
        lv_indev_wait_release(lv_indev_active());
        ui_go_watchface();
    }
}

static void back_cb(lv_event_t *e)
{
    ui_go_watchface();
}

static lv_obj_t *make_pill_button(lv_obj_t *parent, const char *text, lv_obj_t **label_out)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_height(b, 48);
    lv_obj_set_style_radius(b, 24, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(0x252b36), 0);
    lv_obj_add_style(b, ui_style_accent_bg(), LV_STATE_CHECKED);
    lv_obj_set_style_shadow_width(b, 0, 0);
    lv_obj_t *l = lv_label_create(b);
    lv_label_set_text(l, text);
    lv_obj_set_style_text_color(b, lv_color_white(), 0);    /* the label inherits; accent: see below */
    lv_obj_center(l);
    if (label_out) {
        *label_out = l;
    }
    return b;
}

/* A plain container: no style, no scrolling, gestures go to the screen. */
static lv_obj_t *make_box(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(o, LV_OBJ_FLAG_GESTURE_BUBBLE | LV_OBJ_FLAG_EVENT_BUBBLE);
    return o;
}

static lv_obj_t *make_card(lv_obj_t *parent)
{
    lv_obj_t *c = make_box(parent);
    lv_obj_add_style(c, &s_st_card, 0);
    lv_obj_set_width(c, CARD_W);
    lv_obj_set_height(c, LV_SIZE_CONTENT);
    return c;
}

/* A one-line card: [icon] [name ...] and whatever the caller adds on the right. */
static lv_obj_t *make_row_card(lv_obj_t *parent, const char *icon, lv_obj_t **name_out)
{
    lv_obj_t *c = make_card(parent);
    lv_obj_set_height(c, ROW_H);
    lv_obj_set_style_pad_ver(c, 0, 0);
    lv_obj_set_flex_flow(c, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(c, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(c, 8, 0);
    lv_obj_t *i = lv_label_create(c);
    lv_obj_add_style(i, &s_st_icon, 0);
    lv_obj_set_width(i, ICON_COL_W);
    lv_obj_set_style_text_align(i, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_text(i, icon);
    lv_obj_t *n = lv_label_create(c);
    lv_obj_set_flex_grow(n, 1);
    lv_label_set_long_mode(n, LV_LABEL_LONG_DOT);
    *name_out = n;
    return c;
}

/* A row card that reacts to taps (darker while pressed) and ends in a value and a chevron. */
static lv_obj_t *make_tap_row(lv_obj_t *parent, const char *icon, lv_obj_t **name_out, lv_obj_t **value_out,
                              lv_event_cb_t cb)
{
    lv_obj_t *c = make_row_card(parent, icon, name_out);
    lv_obj_add_flag(c, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_style(c, &s_st_card_pressed, LV_STATE_PRESSED);
    lv_obj_add_event_cb(c, cb, LV_EVENT_CLICKED, NULL);
    if (value_out) {
        *value_out = lv_label_create(c);
    }
    lv_obj_t *chev = lv_label_create(c);
    lv_obj_add_style(chev, &s_st_icon, 0);
    lv_label_set_text(chev, SET_ICON_CHEVRON);
    return c;
}

static lv_obj_t *make_slider_row(lv_obj_t *parent, const char *icon, lv_obj_t **name_out, lv_obj_t **value_out,
                                 int min, int max)
{
    lv_obj_t *c = make_row_card(parent, icon, name_out);
    lv_obj_t *s = lv_slider_create(c);
    lv_obj_remove_style_all(s);
    lv_obj_set_size(s, SLIDER_W, SLIDER_TRACK_H);
    lv_slider_set_range(s, min, max);
    lv_obj_add_style(s, &s_st_track, LV_PART_MAIN);
    lv_obj_add_style(s, &s_st_fill, LV_PART_INDICATOR);
    lv_obj_add_style(s, &s_st_knob, LV_PART_KNOB);
    lv_obj_set_style_margin_hor(s, 10, 0);      /* room for the knob */
    lv_obj_remove_flag(s, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_ext_click_area(s, 18);
    lv_obj_add_event_cb(s, slider_cb, LV_EVENT_ALL, NULL);
    lv_obj_t *v = lv_label_create(c);
    lv_obj_set_width(v, VALUE_W);
    lv_obj_set_style_text_align(v, LV_TEXT_ALIGN_RIGHT, 0);
    *value_out = v;
    return s;
}

static void build_settings(void)
{
    lv_style_init(&s_st_card);
    lv_style_set_radius(&s_st_card, 18);
    lv_style_set_bg_opa(&s_st_card, LV_OPA_COVER);
    lv_style_set_border_width(&s_st_card, 1);
    lv_style_set_pad_all(&s_st_card, CARD_PAD);
    lv_style_init(&s_st_card_pressed);
    lv_style_init(&s_st_icon);
    lv_style_set_text_font(&s_st_icon, &buddy_font_set);
    lv_style_init(&s_st_chip);
    lv_style_set_radius(&s_st_chip, LV_RADIUS_CIRCLE);
    lv_style_set_bg_opa(&s_st_chip, LV_OPA_COVER);
    lv_style_set_border_width(&s_st_chip, 1);
    lv_style_set_pad_left(&s_st_chip, 10);
    lv_style_set_pad_right(&s_st_chip, 14);
    lv_style_set_pad_column(&s_st_chip, 8);
    lv_style_init(&s_st_chip_on);
    lv_style_set_border_width(&s_st_chip_on, 2);
    lv_style_init(&s_st_track);
    lv_style_set_radius(&s_st_track, LV_RADIUS_CIRCLE);
    lv_style_set_bg_opa(&s_st_track, LV_OPA_COVER);
    lv_style_init(&s_st_fill);
    lv_style_set_radius(&s_st_fill, LV_RADIUS_CIRCLE);
    lv_style_set_bg_opa(&s_st_fill, LV_OPA_COVER);
    lv_style_init(&s_st_knob);
    lv_style_set_radius(&s_st_knob, LV_RADIUS_CIRCLE);
    lv_style_set_bg_opa(&s_st_knob, LV_OPA_COVER);
    lv_style_set_bg_color(&s_st_knob, lv_color_white());
    lv_style_set_pad_all(&s_st_knob, 6);            /* knob = track height + 12 px */
    lv_style_set_border_width(&s_st_knob, 2);

    s_set_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_set_scr, ui_style_screen(), 0);
    lv_obj_add_event_cb(s_set_scr, settings_gesture_cb, LV_EVENT_GESTURE, NULL);
    lv_obj_set_scroll_dir(s_set_scr, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(s_set_scr, LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_flex_flow(s_set_scr, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(s_set_scr, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_top(s_set_scr, 18, 0);
    lv_obj_set_style_pad_bottom(s_set_scr, 40, 0);
    lv_obj_set_style_pad_row(s_set_scr, 10, 0);

    /* Header: Wi-Fi status + battery | title | close (back to the watchface; swiping right or
     * down does the same). */
    lv_obj_t *hdr = make_box(s_set_scr);
    lv_obj_set_size(hdr, 350, 48);
    lv_obj_add_flag(hdr, LV_OBJ_FLAG_OVERFLOW_VISIBLE);    /* the X's touch area reaches past the row */
    lv_obj_t *status = make_box(hdr);
    lv_obj_set_size(status, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(status, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(status, 6, 0);
    lv_obj_align(status, LV_ALIGN_LEFT_MID, 0, 0);
    s_set_wifi = lv_label_create(status);
    lv_label_set_text(s_set_wifi, ICON_WIFI);
    s_set_batt = lv_label_create(status);
    lv_label_set_text(s_set_batt, "");
    s_set_title = lv_label_create(hdr);
    lv_obj_set_style_text_font(s_set_title, &buddy_font_28, 0);
    lv_obj_center(s_set_title);
    lv_obj_t *close = ui_add_close_x(s_set_scr, back_cb);   /* the same X as on every screen */

    /* Theme: preset colour dots */
    lv_obj_t *card = make_card(s_set_scr);
    lv_obj_set_flex_flow(card, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_flex_align(card, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_START);
    lv_obj_set_style_pad_row(card, 10, 0);
    lv_obj_t *head = make_box(card);
    lv_obj_set_size(head, CARD_INNER_W, 30);
    lv_obj_t *hi = lv_label_create(head);
    lv_obj_add_style(hi, &s_st_icon, 0);
    lv_obj_set_width(hi, ICON_COL_W);
    lv_obj_set_style_text_align(hi, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_text(hi, SET_ICON_PALETTE);
    lv_obj_align(hi, LV_ALIGN_LEFT_MID, 0, 0);
    s_lbl_theme = lv_label_create(head);
    lv_obj_align(s_lbl_theme, LV_ALIGN_LEFT_MID, ICON_COL_W + 8, 0);
    const char *const *names = settings_theme_preset_names();
    s_theme_count = 0;
    for (int i = 0; names[i] && s_theme_count < (int)(sizeof(s_theme_btns) / sizeof(s_theme_btns[0])); i++) {
        settings_theme_t t;
        settings_theme_preset(names[i], &t);
        lv_obj_t *b = lv_button_create(card);
        lv_obj_remove_style_all(b);
        lv_obj_set_size(b, THEME_DOT, THEME_DOT);
        lv_obj_set_style_radius(b, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_color(b, lv_color_hex(t.accent), 0);
        lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
        lv_obj_set_style_border_color(b, lv_color_hex(t.background == 0 ? 0x333333 : t.background), 0);
        lv_obj_set_style_border_width(b, 4, 0);
        lv_obj_set_style_border_color(b, lv_color_white(), LV_STATE_CHECKED);
        lv_obj_add_flag(b, LV_OBJ_FLAG_GESTURE_BUBBLE);
        lv_obj_add_event_cb(b, theme_cb, LV_EVENT_CLICKED, (void *)names[i]);
        s_theme_btns[s_theme_count++] = b;
    }

    s_sld_bri = make_slider_row(s_set_scr, SET_ICON_SUN, &s_lbl_bri, &s_val_bri, 5, 100);
    s_sld_vol = make_slider_row(s_set_scr, SET_ICON_VOLUME, &s_lbl_vol, &s_val_vol, 0, 100);
    make_tap_row(s_set_scr, SET_ICON_POWER, &s_lbl_timeout, &s_val_timeout, timeout_cb);

    /* Language: English | Other (opens the picker) */
    lv_obj_t *lang = make_row_card(s_set_scr, SET_ICON_GLOBE, &s_lbl_lang);
    s_btn_lang_en = lv_button_create(lang);
    s_btn_lang_other = lv_button_create(lang);
    lv_obj_t *pills[2] = { s_btn_lang_en, s_btn_lang_other };
    for (int i = 0; i < 2; i++) {
        lv_obj_t *b = pills[i];
        lv_obj_remove_style_all(b);
        lv_obj_add_style(b, &s_st_chip, 0);
        lv_obj_add_style(b, &s_st_chip_on, LV_STATE_CHECKED);
        lv_obj_add_style(b, &s_st_card_pressed, LV_STATE_PRESSED);
        lv_obj_set_size(b, LANG_PILL_W, CHIP_H);
        lv_obj_set_style_pad_hor(b, 8, 0);
        lv_obj_add_flag(b, LV_OBJ_FLAG_GESTURE_BUBBLE);
        lv_obj_t *l = lv_label_create(b);
        lv_obj_set_width(l, LANG_PILL_W - 16);
        lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
        lv_label_set_long_mode(l, LV_LABEL_LONG_DOT);
        lv_obj_center(l);
        if (i == 0) {
            lv_label_set_text(l, "English");
        } else {
            s_lbl_lang_other = l;
        }
    }
    lv_obj_add_event_cb(s_btn_lang_en, lang_en_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_add_event_cb(s_btn_lang_other, lang_other_cb, LV_EVENT_CLICKED, NULL);

    make_tap_row(s_set_scr, SET_ICON_WIFI, &s_lbl_wifi_btn, NULL, wifi_btn_cb);
    lv_obj_move_foreground(close);
}

/* Name of the active foreign language: the server's list, else quick_languages. */
static const char *foreign_label(void)
{
    const char *lang = g_ui_settings.language;
    if (strcmp(lang, "en") == 0 || strcmp(lang, "auto") == 0) {
        return NULL;
    }
    const char *label = ui_lang_label(lang);
    for (int i = 0; !label && i < g_ui_settings.quick_language_count && i < SETTINGS_QUICK_LANG_MAX; i++) {
        if (strcmp(g_ui_settings.quick_languages[i].code, lang) == 0) {
            label = g_ui_settings.quick_languages[i].label;
        }
    }
    return label ? label : lang;
}

/* Card colours from the theme: tints of the accent over the background. */
static void settings_styles_apply(void)
{
    const lv_color_t a = g_ui_theme.accent, bg = g_ui_theme.background;
    const lv_color_t card = lv_color_mix(a, bg, 22), line = lv_color_mix(a, bg, 70);
    lv_style_set_bg_color(&s_st_card, card);
    lv_style_set_border_color(&s_st_card, line);
    lv_style_set_bg_color(&s_st_card_pressed, lv_color_mix(a, bg, 60));
    lv_style_set_text_color(&s_st_icon, a);
    lv_style_set_bg_color(&s_st_chip, lv_color_mix(a, bg, 12));
    lv_style_set_border_color(&s_st_chip, line);
    lv_style_set_bg_color(&s_st_chip_on, lv_color_mix(a, bg, 70));
    lv_style_set_border_color(&s_st_chip_on, a);
    lv_style_set_bg_color(&s_st_track, lv_color_mix(a, bg, 50));
    lv_style_set_bg_color(&s_st_fill, a);
    lv_style_set_border_color(&s_st_knob, bg);
    lv_style_t *all[] = { &s_st_card, &s_st_card_pressed, &s_st_icon, &s_st_chip, &s_st_chip_on,
                          &s_st_track, &s_st_fill, &s_st_knob };
    for (size_t i = 0; i < sizeof(all) / sizeof(all[0]); i++) {
        lv_obj_report_style_change(all[i]);
    }
}

void ui_settings_status(const char *batt, lv_color_t batt_color, lv_color_t wifi_color, lv_opa_t wifi_opa)
{
    if (!s_set_scr) {
        return;
    }
    lv_label_set_text(s_set_batt, batt);
    lv_obj_set_style_text_color(s_set_batt, batt_color, 0);
    lv_obj_set_style_text_color(s_set_wifi, wifi_color, 0);
    lv_obj_set_style_text_opa(s_set_wifi, wifi_opa, 0);
}

void ui_settings_refresh(void)
{
    if (!s_set_scr) {
        return;
    }
    settings_styles_apply();
    lv_label_set_text(s_set_title, ui_str(STR_SETTINGS));
    lv_label_set_text(s_lbl_theme, ui_str(STR_THEME));
    lv_label_set_text(s_lbl_bri, ui_str(STR_BRIGHTNESS));
    lv_label_set_text(s_lbl_vol, ui_str(STR_VOLUME));
    lv_label_set_text(s_lbl_timeout, ui_str(STR_SCREEN_TIMEOUT));
    lv_label_set_text(s_lbl_lang, ui_str(STR_LANGUAGE));
    lv_label_set_text(s_lbl_wifi_btn, ui_str(STR_WIFI_SETUP));


    lv_slider_set_value(s_sld_vol, g_ui_settings.volume, LV_ANIM_OFF);
    lv_slider_set_value(s_sld_bri, g_ui_settings.brightness, LV_ANIM_OFF);
    show_slider_value(s_sld_vol, g_ui_settings.volume);
    show_slider_value(s_sld_bri, g_ui_settings.brightness);

    int t = g_ui_settings.screen_timeout_s;
    if (t >= 60 && t % 60 == 0) {
        lv_label_set_text_fmt(s_val_timeout, "%d %s", t / 60, ui_str(STR_UNIT_MIN));
    } else {
        lv_label_set_text_fmt(s_val_timeout, "%d %s", t, ui_str(STR_UNIT_S));
    }

    const char *foreign = foreign_label();
    lv_label_set_text(s_lbl_lang_other, foreign ? foreign : ui_str(STR_OTHER));
    lv_obj_set_state(s_btn_lang_en, LV_STATE_CHECKED, strcmp(g_ui_settings.language, "en") == 0);
    lv_obj_set_state(s_btn_lang_other, LV_STATE_CHECKED, foreign != NULL);

    const char *const *names = settings_theme_preset_names();
    for (int i = 0; i < s_theme_count; i++) {
        lv_obj_set_state(s_theme_btns[i], LV_STATE_CHECKED,
                         strcmp(names[i], g_ui_settings.theme.preset) == 0);
    }
}

void ui_settings_open(void)
{
    ui_settings_refresh();
    ui_load_screen(s_set_scr, true);
}

/* ------------------------------------------------------------------------- */
/* Message screen                                                             */
/* ------------------------------------------------------------------------- */

static void msg_timer_cb(lv_timer_t *t)
{
    s_msg_timer = NULL;
    if (lv_screen_active() == s_msg_scr) {
        ui_go_watchface();
    }
}

static void msg_click_cb(lv_event_t *e)
{
    if (s_msg_dismissable) {
        ui_go_watchface();
    }
}

static void msg_btn_cb(lv_event_t *e)
{
    lv_event_stop_bubbling(e);
    if (s_msg_btn_cb) {
        s_msg_btn_cb();
    }
}

static void build_msg(void)
{
    s_msg_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_msg_scr, ui_style_screen(), 0);
    lv_obj_remove_flag(s_msg_scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_msg_scr, msg_click_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_set_flex_flow(s_msg_scr, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(s_msg_scr, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(s_msg_scr, 14, 0);
    lv_obj_set_style_pad_hor(s_msg_scr, 28, 0);

    s_msg_icon = lv_label_create(s_msg_scr);
    lv_obj_set_style_text_font(s_msg_icon, &buddy_font_icon, 0);

    s_msg_title = lv_label_create(s_msg_scr);
    lv_obj_set_style_text_font(s_msg_title, &buddy_font_28, 0);
    lv_obj_set_style_text_align(s_msg_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(s_msg_title, LV_PCT(100));
    lv_label_set_long_mode(s_msg_title, LV_LABEL_LONG_WRAP);

    s_msg_body = lv_label_create(s_msg_scr);
    lv_obj_set_style_text_align(s_msg_body, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(s_msg_body, LV_PCT(100));
    lv_label_set_long_mode(s_msg_body, LV_LABEL_LONG_WRAP);

    s_msg_code = lv_label_create(s_msg_scr);
    lv_obj_set_style_text_font(s_msg_code, &buddy_font_code, 0);
    lv_obj_set_style_text_letter_space(s_msg_code, 6, 0);

    s_msg_btn = make_pill_button(s_msg_scr, "", &s_msg_btn_lbl);
    lv_obj_set_width(s_msg_btn, 260);
    lv_obj_remove_state(s_msg_btn, LV_STATE_CHECKED);
    lv_obj_add_style(s_msg_btn, ui_style_accent_bg(), 0);
    lv_obj_add_event_cb(s_msg_btn, msg_btn_cb, LV_EVENT_CLICKED, NULL);
}

bool ui_msg_is_active(void)
{
    return s_msg_scr && lv_screen_active() == s_msg_scr;
}

void ui_msg_refresh_theme(void)
{
    if (s_msg_code) {
        lv_obj_set_style_text_color(s_msg_code, g_ui_theme.accent, 0);
    }
}

void ui_msg_show(const char *icon, lv_color_t icon_color, const char *title, const char *body,
                 const char *code, const char *button, void (*button_cb)(void),
                 bool dismissable, uint32_t auto_close_ms)
{
    /* the action button sits on the accent: readable text on it (dark on a white accent) */
    lv_obj_set_style_text_color(s_msg_btn, ui_on_color(g_ui_theme.accent), 0);
    lv_label_set_text(s_msg_icon, icon ? icon : "");
    lv_obj_set_style_text_color(s_msg_icon, icon_color, 0);
    lv_label_set_text(s_msg_title, title ? title : "");
    lv_label_set_text(s_msg_body, body ? body : "");
    if (code) {
        lv_label_set_text(s_msg_code, code);
        lv_obj_set_style_text_color(s_msg_code, g_ui_theme.accent, 0);
        lv_obj_remove_flag(s_msg_code, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_msg_code, LV_OBJ_FLAG_HIDDEN);
    }
    if (button && button_cb) {
        lv_label_set_text(s_msg_btn_lbl, button);
        lv_obj_remove_flag(s_msg_btn, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_msg_btn, LV_OBJ_FLAG_HIDDEN);
    }
    s_msg_btn_cb = button_cb;
    s_msg_dismissable = dismissable;
    if (s_msg_timer) {
        lv_timer_delete(s_msg_timer);
        s_msg_timer = NULL;
    }
    if (auto_close_ms) {
        s_msg_timer = lv_timer_create(msg_timer_cb, auto_close_ms, NULL);
        lv_timer_set_repeat_count(s_msg_timer, 1);
    }
    ui_note_activity();
    if (lv_screen_active() != s_msg_scr) {
        ui_load_screen(s_msg_scr, true);
    }
}

void ui_screens_init(void)
{
    build_settings();
    build_msg();
    ui_settings_refresh();
}

/* ------------------------------------------------------------------------- */
/* Public message screens                                                     */
/* ------------------------------------------------------------------------- */

static void wifi_setup_action(void)
{
    if (g_ui_cb.on_wifi_setup) {
        g_ui_cb.on_wifi_setup();
    }
}

static void retry_action(void)
{
    ui_go_watchface();
    if (g_ui_cb.on_retry) {
        g_ui_cb.on_retry();
    }
}

void ui_show_pairing(const char *code)
{
    LOCK();
    char spaced[16];
    /* "123 456" reads better on a watch */
    if (code && strlen(code) == 6) {
        snprintf(spaced, sizeof(spaced), "%.3s %.3s", code, code + 3);
    } else {
        strlcpy(spaced, code ? code : "------", sizeof(spaced));
    }
    ui_msg_show(ICON_LOCK, g_ui_theme.accent, ui_str(STR_PAIR_TITLE), ui_str(STR_PAIR_BODY),
                spaced, NULL, NULL, false, 0);
    UNLOCK();
}

void ui_show_wifi_setup(const char *ap_ssid)
{
    LOCK();
    char body[160];
    snprintf(body, sizeof(body), "%s\n\n%s\n\n192.168.4.1", ui_str(STR_WIFI_BODY), ap_ssid ? ap_ssid : "ola");
    ui_msg_show(ICON_WIFI, g_ui_theme.accent, ui_str(STR_WIFI_TITLE), body, NULL, NULL, NULL, false, 0);
    UNLOCK();
}

void ui_show_error(ui_error_t err, const char *detail)
{
    const lv_color_t red = lv_palette_main(LV_PALETTE_RED);
    const lv_color_t amber = lv_palette_main(LV_PALETTE_AMBER);
    char body[200];

    LOCK();
    switch (err) {
    case UI_ERR_NO_WIFI:
        ui_msg_show(ICON_WIFI, red, ui_str(STR_ERR_NO_WIFI_T), ui_str(STR_ERR_NO_WIFI_B), NULL,
                    ui_str(STR_WIFI_SETUP), wifi_setup_action, true, 0);
        break;
    case UI_ERR_SERVER_UNREACHABLE:
        ui_msg_show(ICON_UNLINK, amber, ui_str(STR_ERR_SERVER_T), ui_str(STR_ERR_SERVER_B), NULL,
                    ui_str(STR_RETRY), retry_action, true, 8000);
        break;
    case UI_ERR_NO_SERVER:
        ui_msg_show(ICON_UNLINK, amber, ui_str(STR_ERR_NO_SERVER_T), ui_str(STR_ERR_NO_SERVER_B), NULL,
                    ui_str(STR_WIFI_SETUP), wifi_setup_action, true, 0);
        break;
    case UI_ERR_UNPAIRED:
        ui_msg_show(ICON_LOCK, amber, ui_str(STR_ERR_UNPAIRED_T), ui_str(STR_ERR_UNPAIRED_B), NULL,
                    NULL, NULL, false, 0);
        break;
    case UI_ERR_AI:
        if (detail && detail[0]) {
            snprintf(body, sizeof(body), "%s\n\n%s", ui_str(STR_ERR_AI_B), detail);
        } else {
            strlcpy(body, ui_str(STR_ERR_AI_B), sizeof(body));
        }
        ui_msg_show(ICON_WARNING, amber, ui_str(STR_ERR_AI_T), body, NULL, NULL, NULL, true, 4000);
        break;
    case UI_ERR_PROTOCOL:
        ui_msg_show(ICON_REFRESH, red, ui_str(STR_ERR_PROTO_T), ui_str(STR_ERR_PROTO_B), NULL,
                    NULL, NULL, true, 0);
        break;
    case UI_ERR_LOW_BATTERY:
        ui_msg_show(ICON_BATTERY_EMPTY, red, ui_str(STR_ERR_BATT_T), ui_str(STR_ERR_BATT_B), NULL,
                    NULL, NULL, true, 10000);
        break;
    case UI_ERR_BUSY:
        ui_msg_show(ICON_WARNING, amber, ui_str(STR_ERR_BUSY_T), ui_str(STR_ERR_BUSY_B), NULL,
                    NULL, NULL, true, 4000);
        break;
    case UI_ERR_SUBSCRIPTION:
        ui_msg_show(ICON_LOCK, amber, ui_str(STR_ERR_SUB_T), ui_str(STR_ERR_SUB_B), NULL,
                    NULL, NULL, true, 6000);
        break;
    case UI_ERR_LIMIT:
        ui_msg_show(ICON_WARNING, amber, ui_str(STR_ERR_LIMIT_T), ui_str(STR_ERR_LIMIT_B), NULL,
                    NULL, NULL, true, 6000);
        break;
    case UI_ERR_ACCOUNT_INACTIVE:
        ui_msg_show(ICON_LOCK, red, ui_str(STR_ERR_INACTIVE_T), ui_str(STR_ERR_INACTIVE_B), NULL,
                    NULL, NULL, true, 10000);
        break;
    }
    UNLOCK();
}

static void reset_confirm_action(void)
{
    if (g_ui_cb.on_factory_reset) {
        g_ui_cb.on_factory_reset();
    }
}

void ui_show_reset_confirm(void)
{
    LOCK();
    /* Not dismissable by a stray tap: only the button confirms; the screen
     * returns to the watchface by itself after 10 s (= cancel). */
    ui_msg_show(ICON_WARNING, lv_palette_main(LV_PALETTE_RED), ui_str(STR_RESET_T), ui_str(STR_RESET_B),
                NULL, ui_str(STR_RESET_BTN), reset_confirm_action, false, 10000);
    UNLOCK();
}

void ui_show_resetting(void)
{
    LOCK();
    ui_msg_show(ICON_REFRESH, lv_palette_main(LV_PALETTE_RED), ui_str(STR_RESETTING), "", NULL,
                NULL, NULL, false, 0);
    UNLOCK();
}

void ui_show_ota(int pct)
{
    LOCK();
    char code[16];
    if (pct < 0) {
        ui_msg_show(ICON_REFRESH, lv_palette_main(LV_PALETTE_RED), ui_str(STR_OTA_FAIL), "", NULL,
                    NULL, NULL, true, 5000);
    } else {
        snprintf(code, sizeof(code), "%d", pct);
        ui_msg_show(ICON_REFRESH, g_ui_theme.accent, ui_str(STR_OTA_T), "%", code, NULL, NULL, false, 0);
    }
    UNLOCK();
}
