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

#define CONTENT_W   340

/* ---- settings screen ---- */
static lv_obj_t *s_set_scr;
static lv_obj_t *s_set_title;
static lv_obj_t *s_lbl_vol;
static lv_obj_t *s_lbl_bri;
static lv_obj_t *s_lbl_lang;
static lv_obj_t *s_lbl_theme;
static lv_obj_t *s_sld_vol;
static lv_obj_t *s_sld_bri;
static lv_obj_t *s_lang_row;
static lv_obj_t *s_btn_lang_en;     /* English */
static lv_obj_t *s_btn_lang_other;  /* "Other" or the chosen foreign language: opens the picker */
static lv_obj_t *s_lbl_lang_other;
static lv_obj_t *s_btn_wifi;
static lv_obj_t *s_lbl_wifi_btn;
static lv_obj_t *s_theme_btns[8];
static int       s_theme_count;

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
        if (sld == s_sld_bri && s_slider_dragged) {
            board_display_set_brightness(v);        /* live preview */
        }
        break;
    case LV_EVENT_RELEASED:
    case LV_EVENT_PRESS_LOST:
        if (code == LV_EVENT_RELEASED && s_slider_dragged && v != slider_setting(sld)) {
            send_change_int(sld == s_sld_vol ? "volume" : "brightness", v);
        } else {
            lv_slider_set_value(sld, slider_setting(sld), LV_ANIM_OFF);
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

static lv_obj_t *section_label(lv_obj_t *parent)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_opa(l, LV_OPA_80, 0);
    lv_obj_set_style_pad_top(l, 10, 0);
    return l;
}

#define SLIDER_H       44
#define SHEEN_W        24

/* A soft light band sweeping left→right across the green fill, so it reads as flowing liquid.
 * It is clamped to the filled part; the slider clips it to the box. */
static void sheen_anim_cb(void *var, int32_t t)
{
    lv_obj_t *sheen = var;
    lv_obj_t *sld = lv_obj_get_parent(sheen);
    int32_t min = lv_slider_get_min_value(sld);
    int32_t max = lv_slider_get_max_value(sld);
    int32_t fill = max > min ? lv_obj_get_width(sld) * (lv_slider_get_value(sld) - min) / (max - min) : 0;
    int32_t x = t * (fill + SHEEN_W) / 1000 - SHEEN_W;
    int32_t x0 = LV_MAX(x, 0);
    int32_t x1 = LV_MIN(x + SHEEN_W, fill);
    if (x1 <= x0) {
        lv_obj_add_flag(sheen, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    lv_obj_remove_flag(sheen, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_pos(sheen, x0, 0);
    lv_obj_set_width(sheen, x1 - x0);
}

static lv_obj_t *make_slider(lv_obj_t *parent, int min, int max)
{
    lv_obj_t *s = lv_slider_create(parent);
    lv_obj_set_width(s, CONTENT_W - 30);
    lv_obj_set_height(s, SLIDER_H);
    lv_slider_set_range(s, min, max);
    /* the box */
    lv_obj_set_style_radius(s, 12, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s, lv_color_hex(0x1c2230), LV_PART_MAIN);
    lv_obj_set_style_border_width(s, 1, LV_PART_MAIN);
    lv_obj_set_style_border_color(s, lv_color_hex(0x303642), LV_PART_MAIN);
    lv_obj_set_style_pad_all(s, 0, LV_PART_MAIN);
    lv_obj_set_style_clip_corner(s, true, LV_PART_MAIN);
    /* the green liquid */
    lv_obj_set_style_radius(s, 0, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(s, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(s, lv_color_hex(0x5ee0b0), LV_PART_INDICATOR);
    lv_obj_set_style_bg_grad_color(s, lv_color_hex(0x1a8f68), LV_PART_INDICATOR);
    lv_obj_set_style_bg_grad_dir(s, LV_GRAD_DIR_VER, LV_PART_INDICATOR);
    /* no knob: the fill edge is the handle */
    lv_obj_set_style_bg_opa(s, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_shadow_width(s, 0, LV_PART_KNOB);
    lv_obj_set_style_pad_all(s, 0, LV_PART_KNOB);
    lv_obj_remove_flag(s, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_ext_click_area(s, 8);
    lv_obj_add_event_cb(s, slider_cb, LV_EVENT_ALL, NULL);

    lv_obj_t *sheen = lv_obj_create(s);
    lv_obj_remove_style_all(sheen);
    lv_obj_remove_flag(sheen, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(sheen, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_size(sheen, SHEEN_W, LV_PCT(100));
    lv_obj_set_style_bg_opa(sheen, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(sheen, lv_color_white(), 0);
    lv_obj_set_style_bg_main_opa(sheen, LV_OPA_TRANSP, 0);
    lv_obj_set_style_bg_grad_opa(sheen, LV_OPA_40, 0);
    lv_obj_set_style_bg_grad_color(sheen, lv_color_white(), 0);
    lv_obj_set_style_bg_grad_dir(sheen, LV_GRAD_DIR_HOR, 0);

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, sheen);
    lv_anim_set_exec_cb(&a, sheen_anim_cb);
    lv_anim_set_values(&a, 0, 1000);
    lv_anim_set_duration(&a, 1400);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_start(&a);
    return s;
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
    lv_obj_set_style_text_color(l, lv_color_white(), 0);
    lv_obj_center(l);
    if (label_out) {
        *label_out = l;
    }
    return b;
}

static void build_settings(void)
{
    s_set_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_set_scr, ui_style_screen(), 0);
    lv_obj_add_event_cb(s_set_scr, settings_gesture_cb, LV_EVENT_GESTURE, NULL);
    lv_obj_set_scroll_dir(s_set_scr, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(s_set_scr, LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_flex_flow(s_set_scr, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(s_set_scr, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_top(s_set_scr, 26, 0);
    lv_obj_set_style_pad_bottom(s_set_scr, 40, 0);
    lv_obj_set_style_pad_row(s_set_scr, 10, 0);

    /* Title row with back button */
    lv_obj_t *row = lv_obj_create(s_set_scr);
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, CONTENT_W, 44);
    lv_obj_add_flag(row, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_t *back = lv_button_create(row);
    lv_obj_remove_style_all(back);
    lv_obj_set_size(back, 44, 44);
    lv_obj_align(back, LV_ALIGN_LEFT_MID, 0, 0);
    lv_obj_add_event_cb(back, back_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *bl = lv_label_create(back);
    lv_obj_set_style_text_font(bl, &buddy_font_28, 0);
    lv_label_set_text(bl, ICON_LEFT);
    lv_obj_center(bl);
    s_set_title = lv_label_create(row);
    lv_obj_set_style_text_font(s_set_title, &buddy_font_28, 0);
    lv_obj_center(s_set_title);

    s_lbl_vol = section_label(s_set_scr);
    s_sld_vol = make_slider(s_set_scr, 0, 100);
    s_lbl_bri = section_label(s_set_scr);
    s_sld_bri = make_slider(s_set_scr, 5, 100);

    s_lbl_lang = section_label(s_set_scr);
    /* Language pills, built from quick_languages in ui_settings_refresh().
     * Pills size to their label and wrap to a second row if needed. */
    s_lang_row = lv_obj_create(s_set_scr);
    lv_obj_remove_style_all(s_lang_row);
    lv_obj_set_size(s_lang_row, CONTENT_W, LV_SIZE_CONTENT);
    lv_obj_set_style_pad_ver(s_lang_row, 2, 0);
    lv_obj_set_style_pad_row(s_lang_row, 8, 0);
    lv_obj_set_style_pad_column(s_lang_row, 8, 0);
    lv_obj_add_flag(s_lang_row, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_remove_flag(s_lang_row, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_flex_flow(s_lang_row, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_flex_align(s_lang_row, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    s_btn_lang_en = make_pill_button(s_lang_row, "English", NULL);
    lv_obj_set_width(s_btn_lang_en, 150);
    lv_obj_add_flag(s_btn_lang_en, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(s_btn_lang_en, lang_en_cb, LV_EVENT_CLICKED, NULL);
    s_btn_lang_other = make_pill_button(s_lang_row, "", &s_lbl_lang_other);
    lv_obj_set_width(s_btn_lang_other, 150);
    lv_obj_set_style_pad_hor(s_btn_lang_other, 12, 0);
    lv_obj_set_width(s_lbl_lang_other, 126);
    lv_obj_set_style_text_align(s_lbl_lang_other, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_lbl_lang_other, LV_LABEL_LONG_DOT);
    lv_obj_add_flag(s_btn_lang_other, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(s_btn_lang_other, lang_other_cb, LV_EVENT_CLICKED, NULL);

    s_lbl_theme = section_label(s_set_scr);
    lv_obj_t *theme_row = lv_obj_create(s_set_scr);
    lv_obj_remove_style_all(theme_row);
    lv_obj_set_size(theme_row, CONTENT_W, 60);
    lv_obj_set_flex_flow(theme_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(theme_row, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    const char *const *names = settings_theme_preset_names();
    s_theme_count = 0;
    for (int i = 0; names[i] && s_theme_count < (int)(sizeof(s_theme_btns) / sizeof(s_theme_btns[0])); i++) {
        settings_theme_t t;
        settings_theme_preset(names[i], &t);
        lv_obj_t *b = lv_button_create(theme_row);
        lv_obj_remove_style_all(b);
        lv_obj_set_size(b, 48, 48);
        lv_obj_set_style_radius(b, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_color(b, lv_color_hex(t.accent), 0);
        lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
        lv_obj_set_style_border_color(b, lv_color_hex(t.background == 0 ? 0x333333 : t.background), 0);
        lv_obj_set_style_border_width(b, 4, 0);
        lv_obj_set_style_border_color(b, lv_color_white(), LV_STATE_CHECKED);
        lv_obj_add_event_cb(b, theme_cb, LV_EVENT_CLICKED, (void *)names[i]);
        s_theme_btns[s_theme_count++] = b;
    }

    s_btn_wifi = make_pill_button(s_set_scr, "", &s_lbl_wifi_btn);
    lv_obj_set_width(s_btn_wifi, CONTENT_W - 40);
    lv_obj_set_style_margin_top(s_btn_wifi, 14, 0);
    lv_obj_add_event_cb(s_btn_wifi, wifi_btn_cb, LV_EVENT_CLICKED, NULL);
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

void ui_settings_refresh(void)
{
    if (!s_set_scr) {
        return;
    }
    lv_label_set_text(s_set_title, ui_str(STR_SETTINGS));
    lv_label_set_text_fmt(s_lbl_vol, ICON_VOLUME "  %s", ui_str(STR_VOLUME));
    lv_label_set_text_fmt(s_lbl_bri, ICON_BRIGHTNESS "  %s", ui_str(STR_BRIGHTNESS));
    lv_label_set_text_fmt(s_lbl_lang, ICON_LANGUAGE "  %s", ui_str(STR_LANGUAGE));
    lv_label_set_text_fmt(s_lbl_theme, ICON_THEME "  %s", ui_str(STR_THEME));
    lv_label_set_text_fmt(s_lbl_wifi_btn, ICON_WIFI "  %s", ui_str(STR_WIFI_SETUP));

    lv_slider_set_value(s_sld_vol, g_ui_settings.volume, LV_ANIM_OFF);
    lv_slider_set_value(s_sld_bri, g_ui_settings.brightness, LV_ANIM_OFF);

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
