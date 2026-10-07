/*
 * ola - quick settings screen and message screens (pairing, Wi-Fi setup,
 * errors, OTA).
 */
#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "board.h"
#include "ui_priv.h"
#include "prov_util.h"

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
#define LEVEL_SEGMENTS  10      /* brightness / volume: a row of blocks, one per 10 % */
#define LEVEL_H         30
#define LEVEL_GAP       5
#define VALUE_W         54
/* The close X (72 px + 28 px touch margin) reaches ~102 px down: the first card starts below it. */
#define HEADER_GAP      36
#define LANG_PILL_W     100

/* ---- settings screen ---- */
static lv_obj_t *s_set_scr;
static lv_obj_t *s_set_title;
static lv_obj_t *s_set_wifi;
static lv_obj_t *s_set_batt;
static lv_obj_t *s_lbl_theme;
static lv_obj_t *s_theme_sw;            /* off = blue ("midnight"), on = white ("mono") */
static lv_obj_t *s_lbl_vol;
static lv_obj_t *s_lbl_bri;
static lv_obj_t *s_val_vol;
static lv_obj_t *s_val_bri;
static lv_obj_t *s_sld_vol;
static lv_obj_t *s_sld_bri;
static lv_obj_t *s_seg_vol[LEVEL_SEGMENTS];
static lv_obj_t *s_seg_bri[LEVEL_SEGMENTS];
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
static lv_style_t s_st_seg;         /* an empty level block */
static lv_style_t s_st_seg_on;      /* a filled level block */

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

/* Value label + blocks: one filled block per started 10 % (5 % brightness = one block, 0 % volume = none). */
static void show_slider_value(lv_obj_t *sld, int v)
{
    lv_label_set_text_fmt(sld == s_sld_vol ? s_val_vol : s_val_bri, "%d%%", v);
    lv_obj_t **seg = sld == s_sld_vol ? s_seg_vol : s_seg_bri;
    int on = (v + 100 / LEVEL_SEGMENTS - 1) / (100 / LEVEL_SEGMENTS);
    for (int i = 0; i < LEVEL_SEGMENTS; i++) {
        lv_obj_set_state(seg[i], LV_STATE_CHECKED, i < on);
    }
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

/* The theme switch: blue <-> white. Only the preset is sent; its colours are fixed. */
static void theme_cb(lv_event_t *e)
{
    if (!g_ui_cb.on_settings_change) {
        return;
    }
    const char *name = lv_obj_has_state(s_theme_sw, LV_STATE_CHECKED) ? "mono" : "midnight";
    if (strcmp(name, g_ui_settings.theme.preset) == 0) {
        return;
    }
    cJSON *c = cJSON_CreateObject();
    cJSON *th = cJSON_AddObjectToObject(c, "theme");
    cJSON_AddStringToObject(th, "preset", name);
    g_ui_cb.on_settings_change(c);
    cJSON_Delete(c);
}

/* A small dot in a theme's accent colour, either side of the switch. */
static void add_theme_dot(lv_obj_t *parent, uint32_t rgb)
{
    lv_obj_t *d = lv_obj_create(parent);
    lv_obj_remove_style_all(d);
    lv_obj_set_size(d, 18, 18);
    lv_obj_set_style_radius(d, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(d, lv_color_hex(rgb), 0);
    lv_obj_set_style_bg_opa(d, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(d, lv_color_hex(0x555B66), 0);
    lv_obj_set_style_border_width(d, 1, 0);
    lv_obj_remove_flag(d, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
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

/* Brightness / volume: [icon] [name ... value] over a full-width row of flat blocks filled up to the
 * value. An invisible slider on top of the blocks takes the drag; the blocks only show the value. */
static lv_obj_t *make_level_card(lv_obj_t *parent, const char *icon, lv_obj_t **name_out, lv_obj_t **value_out,
                                 lv_obj_t **seg_out, int min, int max)
{
    lv_obj_t *c = make_card(parent);
    lv_obj_set_flex_flow(c, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(c, 10, 0);

    lv_obj_t *top = make_box(c);
    lv_obj_set_size(top, CARD_INNER_W, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(top, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(top, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(top, 8, 0);
    lv_obj_t *i = lv_label_create(top);
    lv_obj_add_style(i, &s_st_icon, 0);
    lv_obj_set_width(i, ICON_COL_W);
    lv_obj_set_style_text_align(i, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_text(i, icon);
    lv_obj_t *n = lv_label_create(top);
    lv_obj_set_flex_grow(n, 1);
    lv_label_set_long_mode(n, LV_LABEL_LONG_DOT);
    *name_out = n;
    lv_obj_t *v = lv_label_create(top);
    lv_obj_set_width(v, VALUE_W);
    lv_obj_set_style_text_align(v, LV_TEXT_ALIGN_RIGHT, 0);
    *value_out = v;

    lv_obj_t *bar = make_box(c);
    lv_obj_set_size(bar, CARD_INNER_W, LEVEL_H);
    lv_obj_set_flex_flow(bar, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(bar, LEVEL_GAP, 0);
    for (int k = 0; k < LEVEL_SEGMENTS; k++) {
        lv_obj_t *seg = make_box(bar);
        lv_obj_add_style(seg, &s_st_seg, 0);
        lv_obj_add_style(seg, &s_st_seg_on, LV_STATE_CHECKED);
        lv_obj_set_height(seg, LEVEL_H);
        lv_obj_set_flex_grow(seg, 1);
        seg_out[k] = seg;
    }

    lv_obj_t *s = lv_slider_create(bar);
    lv_obj_remove_style_all(s);                 /* invisible: no track, no fill, no knob */
    lv_obj_add_flag(s, LV_OBJ_FLAG_IGNORE_LAYOUT);
    lv_obj_set_size(s, CARD_INNER_W, LEVEL_H);
    lv_obj_align(s, LV_ALIGN_TOP_LEFT, 0, 0);
    lv_slider_set_range(s, min, max);
    lv_obj_remove_flag(s, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_ext_click_area(s, 10);
    lv_obj_add_event_cb(s, slider_cb, LV_EVENT_ALL, NULL);
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
    lv_style_init(&s_st_seg);                       /* flat blocks: no border, no shadow, no gradient */
    lv_style_set_radius(&s_st_seg, 4);
    lv_style_set_bg_opa(&s_st_seg, LV_OPA_COVER);
    lv_style_init(&s_st_seg_on);

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
    lv_obj_set_style_margin_bottom(hdr, HEADER_GAP, 0);
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

    /* Theme: a switch between the two themes, blue (off) and white (on) */
    lv_obj_t *trow = make_row_card(s_set_scr, SET_ICON_PALETTE, &s_lbl_theme);
    settings_theme_t blue, white;
    settings_theme_preset("midnight", &blue);
    settings_theme_preset("mono", &white);
    add_theme_dot(trow, blue.accent);
    s_theme_sw = lv_switch_create(trow);
    lv_obj_set_size(s_theme_sw, 64, 34);
    lv_obj_set_style_bg_color(s_theme_sw, lv_color_hex(blue.accent), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_theme_sw, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_theme_sw, lv_color_hex(white.accent), LV_PART_INDICATOR | LV_STATE_CHECKED);
    lv_obj_set_style_bg_color(s_theme_sw, lv_color_white(), LV_PART_KNOB);
    lv_obj_set_style_bg_color(s_theme_sw, lv_color_hex(0x2A2F38), LV_PART_KNOB | LV_STATE_CHECKED);
    lv_obj_set_ext_click_area(s_theme_sw, 12);
    lv_obj_add_flag(s_theme_sw, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(s_theme_sw, theme_cb, LV_EVENT_VALUE_CHANGED, NULL);
    add_theme_dot(trow, white.accent);

    s_sld_bri = make_level_card(s_set_scr, SET_ICON_SUN, &s_lbl_bri, &s_val_bri, s_seg_bri, 5, 100);
    s_sld_vol = make_level_card(s_set_scr, SET_ICON_VOLUME, &s_lbl_vol, &s_val_vol, s_seg_vol, 0, 100);
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
    lv_style_set_bg_color(&s_st_seg, lv_color_mix(a, bg, 45));
    lv_style_set_bg_color(&s_st_seg_on, a);
    lv_style_t *all[] = { &s_st_card, &s_st_card_pressed, &s_st_icon, &s_st_chip, &s_st_chip_on,
                          &s_st_seg, &s_st_seg_on };
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

    lv_obj_set_state(s_theme_sw, LV_STATE_CHECKED, strcmp(g_ui_settings.theme.preset, "mono") == 0);
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

/* Wi-Fi setup: the setup network, its password (also the Bluetooth setup password) and a Wi-Fi QR code
 * that an iPhone camera joins directly. The password exists only on this screen. */
static lv_obj_t *s_wifi_scr, *s_wifi_qr, *s_wifi_net, *s_wifi_pass, *s_wifi_hint;

static void build_wifi_setup(void)
{
    s_wifi_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_wifi_scr, ui_style_screen(), 0);
    lv_obj_remove_flag(s_wifi_scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_flex_flow(s_wifi_scr, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(s_wifi_scr, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(s_wifi_scr, 8, 0);
    lv_obj_set_style_pad_hor(s_wifi_scr, 24, 0);

    lv_obj_t *title = lv_label_create(s_wifi_scr);
    lv_obj_set_style_text_font(title, &buddy_font_28, 0);
    lv_label_set_text(title, ui_str(STR_WIFI_TITLE));

    s_wifi_qr = lv_qrcode_create(s_wifi_scr);
    lv_qrcode_set_size(s_wifi_qr, 168);
    lv_qrcode_set_dark_color(s_wifi_qr, lv_color_black());
    lv_qrcode_set_light_color(s_wifi_qr, lv_color_white());
    lv_obj_set_style_border_color(s_wifi_qr, lv_color_white(), 0);
    lv_obj_set_style_border_width(s_wifi_qr, 6, 0);   /* quiet zone: cameras need it */

    s_wifi_net = lv_label_create(s_wifi_scr);
    lv_obj_set_style_text_align(s_wifi_net, LV_TEXT_ALIGN_CENTER, 0);

    s_wifi_pass = lv_label_create(s_wifi_scr);
    lv_obj_set_style_text_font(s_wifi_pass, &buddy_font_28, 0);
    lv_obj_set_style_text_letter_space(s_wifi_pass, 4, 0);

    s_wifi_hint = lv_label_create(s_wifi_scr);
    lv_obj_set_width(s_wifi_hint, LV_PCT(100));
    lv_label_set_long_mode(s_wifi_hint, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(s_wifi_hint, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_opa(s_wifi_hint, LV_OPA_70, 0);
}

void ui_show_wifi_setup(const char *ap_ssid, const char *setup_pass)
{
    LOCK();
    if (!s_wifi_scr) {
        build_wifi_setup();
    }
    const char *ssid = ap_ssid ? ap_ssid : "ola";
    char qr[96];
    if (setup_pass && wifi_qr_payload(ssid, setup_pass, qr, sizeof(qr))) {
        lv_qrcode_update(s_wifi_qr, qr, strlen(qr));
        lv_obj_remove_flag(s_wifi_qr, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_wifi_qr, LV_OBJ_FLAG_HIDDEN);
    }
    memset(qr, 0, sizeof(qr));
    char line[96];
    snprintf(line, sizeof(line), "%s\n%s", ui_str(STR_WIFI_BODY), ssid);
    lv_label_set_text(s_wifi_net, line);
    char grouped[12];
    setup_pass_grouped(setup_pass, grouped, sizeof(grouped));
    snprintf(line, sizeof(line), "%s  %s", ui_str(STR_WIFI_PASS), grouped[0] ? grouped : "-");
    lv_label_set_text(s_wifi_pass, line);
    lv_obj_set_style_text_color(s_wifi_pass, g_ui_theme.accent, 0);
    lv_label_set_text(s_wifi_hint, ui_str(STR_WIFI_HINT));
    ui_note_activity();
    if (lv_screen_active() != s_wifi_scr) {
        ui_load_screen(s_wifi_scr, true);
    }
    UNLOCK();
}

void ui_show_error(ui_error_t err, const char *detail)
{
    const lv_color_t red = lv_palette_main(LV_PALETTE_RED);
    const lv_color_t amber = lv_palette_main(LV_PALETTE_AMBER);
    (void)detail;  /* server error texts are never shown */

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
        /* No money left with a provider, a timeout, a lost connection...: the reason is never shown,
         * only "try again later" (the server log has the details). */
        ui_msg_show(ICON_WARNING, amber, ui_str(STR_ERR_AI_T), ui_str(STR_ERR_AI_B), NULL, NULL, NULL, true, 5000);
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
    case UI_ERR_CONCURRENT:
        ui_msg_show(ICON_WARNING, amber, ui_str(STR_ERR_CONC_T), ui_str(STR_ERR_CONC_B), NULL,
                    NULL, NULL, true, 5000);
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
