/*
 * ola - Wi-Fi screen (Settings › Wi-Fi, and the "No Wi-Fi" message).
 *
 * Full screen in the style of the language picker: title, X in the top-right corner, one scrolling list:
 *  - a status card: the network the watch is on, or what happened to the one just chosen here;
 *  - Saved networks (most recent first), without the one the watch is on (it is in the status card):
 *    tap one to join it, the bin forgets it (tap it twice);
 *  - Nearby: a scan, strongest first, without the saved ones; a lock marks a password. Tap an open one
 *    to join it; one with a password opens the keyboard screen;
 *  - "Set up with phone": the setup screen with the QR code (g_ui_cb.on_wifi_setup), as before.
 * Joining (net_wifi_join) saves a network only once it connects; a wrong password is not saved and the
 * saved networks take over again. While the screen is open a timer follows the scan and the join.
 */
#include <string.h>
#include "board.h"
#include "net.h"
#include "prov_util.h"
#include "ui_priv.h"

#define CONTENT_W       340
#define LIST_Y          96
#define FIELD_Y         96
#define FIELD_H         48
#define KEYBOARD_H      210
#define ROW_BG          0x1a1f28
#define PILL_BG         0x252b36
#define DANGER          0xe5534b
#define OK_GREEN        0x2fbf71
#define POLL_MS         500
#define FORGET_ARM_MS   3000
#define NEARBY_MAX      16
#define SCAN_TRIES      20          /* polls waiting for a busy radio before showing "No networks found" */
#define AUTH_OPEN       0           /* wifi_auth_mode_t WIFI_AUTH_OPEN */

static lv_obj_t   *s_scr, *s_title, *s_list;
static lv_obj_t   *s_pw_scr, *s_pw_title, *s_pw_field, *s_pw_hint, *s_pw_kb;
static lv_timer_t *s_poll;

static wifi_saved_list_t s_saved;       /* a copy while the screen is open (wiped on close) */
static net_ap_info_t     s_near[NEARBY_MAX];
static int               s_near_count;
static char              s_cur[33];     /* the network the watch is on, "" = none */
static char              s_join_ssid[33];
static net_join_state_t  s_join_shown;
static bool              s_scan_wanted, s_scanning;
static int               s_scan_tries;
static int               s_forget_armed = -1;
static uint32_t          s_forget_ms;
static char              s_pw_ssid[33];
static bool              s_from_settings;

static void rebuild(void);

/* ------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* ------------------------------------------------------------------------- */

static bool is_saved(const char *ssid)
{
    for (int i = 0; i < s_saved.count; i++) {
        if (strcmp(s_saved.net[i].ssid, ssid) == 0) {
            return true;
        }
    }
    return false;
}

static void join(const char *ssid, const char *pass)
{
    strlcpy(s_join_ssid, ssid, sizeof(s_join_ssid));
    net_wifi_join(ssid, pass);
    s_join_shown = net_wifi_join_state();
    lv_obj_scroll_to_y(s_list, 0, LV_ANIM_OFF);     /* the status card shows how it goes */
    rebuild();
}

static lv_obj_t *section(const char *text)
{
    lv_obj_t *l = lv_label_create(s_list);
    lv_obj_set_style_text_opa(l, LV_OPA_60, 0);
    lv_obj_set_style_pad_top(l, 12, 0);
    lv_label_set_text(l, text);
    return l;
}

/* A list row: a button with [icon] [name + optional line under it]. Returns the text column. */
static lv_obj_t *row(lv_event_cb_t cb, int idx, const char *icon, lv_color_t icon_color, lv_opa_t icon_opa,
                     const char *name, lv_obj_t **out_row)
{
    lv_obj_t *b = lv_button_create(s_list);
    lv_obj_remove_style_all(b);
    lv_obj_set_size(b, CONTENT_W, LV_SIZE_CONTENT);
    lv_obj_set_style_min_height(b, 60, 0);
    lv_obj_set_style_radius(b, 16, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(ROW_BG), 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(PILL_BG), LV_STATE_PRESSED);
    lv_obj_set_style_pad_hor(b, 16, 0);
    lv_obj_set_style_pad_ver(b, 8, 0);
    lv_obj_set_style_pad_column(b, 12, 0);
    lv_obj_set_flex_flow(b, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(b, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    if (cb) {
        lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, (void *)(intptr_t)idx);
    }
    lv_obj_t *ic = lv_label_create(b);
    lv_obj_set_style_text_color(ic, icon_color, 0);
    lv_obj_set_style_text_opa(ic, icon_opa, 0);
    lv_label_set_text(ic, icon);

    lv_obj_t *col = lv_obj_create(b);
    lv_obj_remove_style_all(col);
    lv_obj_remove_flag(col, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_height(col, LV_SIZE_CONTENT);
    lv_obj_set_flex_grow(col, 1);
    lv_obj_set_flex_flow(col, LV_FLEX_FLOW_COLUMN);
    lv_obj_t *n = lv_label_create(col);
    lv_obj_set_width(n, LV_PCT(100));
    lv_obj_set_height(n, lv_font_get_line_height(&buddy_font_20));
    lv_label_set_long_mode(n, LV_LABEL_LONG_DOT);
    lv_label_set_text(n, name);
    if (out_row) {
        *out_row = b;
    }
    return col;
}

static lv_obj_t *sub_line(lv_obj_t *col, const char *text, lv_color_t color)
{
    lv_obj_t *l = lv_label_create(col);
    lv_obj_set_style_text_color(l, color, 0);
    lv_label_set_text(l, text);
    return l;
}

/* ------------------------------------------------------------------------- */
/* Password screen                                                            */
/* ------------------------------------------------------------------------- */

static void pw_back(void)
{
    lv_textarea_set_text(s_pw_field, "");
    ui_note_activity();
    ui_load_screen(s_scr, false);
}

static void pw_close_cb(lv_event_t *e)
{
    pw_back();
}

static void pw_kb_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_CANCEL) {
        pw_back();
    } else if (code == LV_EVENT_READY) {
        const char *pw = lv_textarea_get_text(s_pw_field);
        if (wifi_cred_validate(s_pw_ssid, strlen(s_pw_ssid), pw, strlen(pw)) != WIFI_CRED_OK) {
            lv_label_set_text(s_pw_hint, ui_str(STR_WIFI_PASS_SHORT));
            return;
        }
        join(s_pw_ssid, pw);
        pw_back();
    } else if (code == LV_EVENT_VALUE_CHANGED) {
        lv_label_set_text(s_pw_hint, "");
        ui_note_activity();
    }
}

static void pw_open(const char *ssid)
{
    strlcpy(s_pw_ssid, ssid, sizeof(s_pw_ssid));
    lv_label_set_text(s_pw_title, ssid);
    lv_textarea_set_text(s_pw_field, "");
    lv_textarea_set_placeholder_text(s_pw_field, ui_str(STR_WIFI_PASS));
    lv_label_set_text(s_pw_hint, "");
    lv_keyboard_set_textarea(s_pw_kb, s_pw_field);
    lv_keyboard_set_mode(s_pw_kb, LV_KEYBOARD_MODE_TEXT_LOWER);
    ui_note_activity();
    ui_load_screen(s_pw_scr, true);
}

static void build_password_screen(void)
{
    s_pw_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_pw_scr, ui_style_screen(), 0);
    lv_obj_remove_flag(s_pw_scr, LV_OBJ_FLAG_SCROLLABLE);

    s_pw_title = lv_label_create(s_pw_scr);
    lv_obj_set_style_text_font(s_pw_title, &buddy_font_28, 0);
    lv_obj_set_width(s_pw_title, 270);
    lv_label_set_long_mode(s_pw_title, LV_LABEL_LONG_DOT);
    lv_obj_set_height(s_pw_title, lv_font_get_line_height(&buddy_font_28));
    lv_obj_align(s_pw_title, LV_ALIGN_TOP_LEFT, 36, 42);

    lv_obj_t *x = ui_add_close_x(s_pw_scr, pw_close_cb);

    /* White box, black text, as the language search. The password stays readable: typed on a small
     * keyboard, it is easier to check. */
    s_pw_field = lv_textarea_create(s_pw_scr);
    lv_obj_set_size(s_pw_field, CONTENT_W, FIELD_H);
    lv_obj_align(s_pw_field, LV_ALIGN_TOP_MID, 0, FIELD_Y);
    lv_textarea_set_one_line(s_pw_field, true);
    lv_textarea_set_max_length(s_pw_field, WIFI_SAVED_PASS_MAX - 1);
    lv_obj_set_style_bg_color(s_pw_field, lv_color_white(), 0);
    lv_obj_set_style_bg_opa(s_pw_field, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_pw_field, 0, 0);
    lv_obj_set_style_radius(s_pw_field, 24, 0);
    lv_obj_set_style_pad_hor(s_pw_field, 18, 0);
    lv_obj_set_style_text_color(s_pw_field, lv_color_black(), 0);
    lv_obj_set_style_text_color(s_pw_field, lv_color_hex(0x8a8f99), LV_PART_TEXTAREA_PLACEHOLDER);
    lv_obj_set_style_bg_color(s_pw_field, lv_color_black(), LV_PART_CURSOR);
    lv_obj_set_style_border_color(s_pw_field, lv_color_black(), LV_PART_CURSOR);
    lv_obj_add_state(s_pw_field, LV_STATE_FOCUSED);     /* the cursor shows */

    s_pw_hint = lv_label_create(s_pw_scr);
    lv_obj_set_style_text_color(s_pw_hint, lv_color_hex(DANGER), 0);
    lv_obj_align(s_pw_hint, LV_ALIGN_TOP_MID, 0, FIELD_Y + FIELD_H + 12);
    lv_label_set_text(s_pw_hint, "");

    s_pw_kb = lv_keyboard_create(s_pw_scr);
    lv_obj_set_size(s_pw_kb, BOARD_LCD_H_RES, KEYBOARD_H);
    lv_obj_align(s_pw_kb, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_text_font(s_pw_kb, &buddy_font_20, LV_PART_ITEMS);
    lv_obj_add_event_cb(s_pw_kb, pw_kb_cb, LV_EVENT_ALL, NULL);
    lv_keyboard_set_textarea(s_pw_kb, s_pw_field);
    lv_obj_move_foreground(x);
}

/* ------------------------------------------------------------------------- */
/* List                                                                       */
/* ------------------------------------------------------------------------- */

static void saved_cb(lv_event_t *e)
{
    int i = (int)(intptr_t)lv_event_get_user_data(e);
    ui_note_activity();
    if (i < 0 || i >= s_saved.count || (s_cur[0] && strcmp(s_cur, s_saved.net[i].ssid) == 0)) {
        return;     /* already on it */
    }
    join(s_saved.net[i].ssid, s_saved.net[i].pass);
}

static void forget_cb(lv_event_t *e)
{
    int i = (int)(intptr_t)lv_event_get_user_data(e);
    ui_note_activity();
    if (i < 0 || i >= s_saved.count) {
        return;
    }
    if (s_forget_armed == i && lv_tick_elaps(s_forget_ms) < FORGET_ARM_MS) {
        settings_forget_wifi(s_saved.net[i].ssid, s_saved.net[i].pass);
        settings_get_wifi_list(&s_saved);
        s_forget_armed = -1;
    } else {
        s_forget_armed = i;
        s_forget_ms = lv_tick_get();
    }
    rebuild();
}

static void near_cb(lv_event_t *e)
{
    int i = (int)(intptr_t)lv_event_get_user_data(e);
    ui_note_activity();
    if (i < 0 || i >= s_near_count) {
        return;
    }
    if (s_near[i].auth == AUTH_OPEN) {
        join(s_near[i].ssid, "");
    } else {
        pw_open(s_near[i].ssid);
    }
}

static void rescan_cb(lv_event_t *e)
{
    ui_note_activity();
    if (!s_scanning) {
        s_scan_wanted = true;
        s_scan_tries = 0;
        lv_timer_ready(s_poll);
    }
}

static void phone_cb(lv_event_t *e)
{
    memset(&s_saved, 0, sizeof(s_saved));
    lv_timer_pause(s_poll);
    if (g_ui_cb.on_wifi_setup) {
        g_ui_cb.on_wifi_setup();    /* the setup screen with the QR code (the watch restarts into it) */
    }
}

static void status_card(void)
{
    const net_join_state_t j = net_wifi_join_state();
    const char *title, *icon = ICON_WIFI;
    const char *ssid = s_join_ssid[0] ? s_join_ssid : "";
    lv_color_t color = lv_color_white();
    lv_opa_t opa = LV_OPA_COVER;
    if (j == NET_JOIN_TRYING) {
        title = ui_str(STR_WIFI_CONNECTING);
        color = g_ui_theme.accent;
    } else if (j == NET_JOIN_WRONG_PASSWORD || j == NET_JOIN_NOT_FOUND) {
        title = ui_str(j == NET_JOIN_WRONG_PASSWORD ? STR_WIFI_WRONG_PASS : STR_WIFI_NOT_FOUND);
        icon = ICON_WARNING;
        color = lv_color_hex(DANGER);
    } else if (s_cur[0]) {
        title = ui_str(STR_WIFI_CONNECTED);
        ssid = s_cur;
        color = lv_color_hex(OK_GREEN);
    } else {
        title = ui_str(STR_WIFI_OFFLINE);
        ssid = "";
        opa = LV_OPA_50;
    }
    lv_obj_t *card;
    lv_obj_t *col = row(NULL, 0, icon, color, opa, title, &card);
    lv_obj_remove_flag(card, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_border_width(card, 2, 0);
    lv_obj_set_style_border_color(card, color, 0);
    lv_obj_set_style_border_opa(card, opa == LV_OPA_COVER ? LV_OPA_COVER : LV_OPA_30, 0);
    if (ssid[0]) {
        lv_obj_t *l = sub_line(col, ssid, lv_color_white());
        lv_obj_set_width(l, LV_PCT(100));
        lv_label_set_long_mode(l, LV_LABEL_LONG_DOT);
        lv_obj_set_height(l, lv_font_get_line_height(&buddy_font_20));
        lv_obj_set_style_text_opa(l, LV_OPA_70, 0);
    }
}

static void rebuild(void)
{
    int32_t y = lv_obj_get_scroll_y(s_list);
    lv_obj_clean(s_list);
    status_card();

    bool header = false;
    for (int i = 0; i < s_saved.count; i++) {
        const wifi_saved_t *n = &s_saved.net[i];
        if (s_cur[0] && strcmp(s_cur, n->ssid) == 0) {
            continue;   /* the network the watch is on: shown in the status card only */
        }
        if (!header) {
            section(ui_str(STR_WIFI_SAVED));
            header = true;
        }
        const bool armed = s_forget_armed == i;
        lv_obj_t *r;
        lv_obj_t *col = row(saved_cb, i, ICON_WIFI, lv_color_white(), LV_OPA_60, n->ssid, &r);
        if (armed) {
            sub_line(col, ui_str(STR_WIFI_FORGET_CONFIRM), lv_color_hex(DANGER));
        }
        lv_obj_t *bin = lv_button_create(r);
        lv_obj_remove_style_all(bin);
        lv_obj_set_size(bin, 44, 44);
        lv_obj_set_style_radius(bin, 22, 0);
        lv_obj_set_style_bg_opa(bin, LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(bin, armed ? lv_color_hex(DANGER) : lv_color_hex(PILL_BG), 0);
        lv_obj_set_ext_click_area(bin, 8);
        lv_obj_add_event_cb(bin, forget_cb, LV_EVENT_CLICKED, (void *)(intptr_t)i);
        lv_obj_t *t = lv_label_create(bin);
        lv_obj_set_style_text_font(t, &buddy_font_set, 0);
        lv_label_set_text(t, SET_ICON_TRASH);
        lv_obj_center(t);
    }

    lv_obj_t *hdr = section(ui_str(STR_WIFI_NEARBY));
    if (!s_scanning && !s_scan_wanted) {
        /* the header doubles as the "search again" button */
        lv_label_set_text_fmt(hdr, "%s   " ICON_REFRESH, ui_str(STR_WIFI_NEARBY));
        lv_obj_add_flag(hdr, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_ext_click_area(hdr, 16);
        lv_obj_add_event_cb(hdr, rescan_cb, LV_EVENT_CLICKED, NULL);
    }
    int shown = 0;
    for (int i = 0; i < s_near_count; i++) {
        const net_ap_info_t *a = &s_near[i];
        if (!a->ssid[0] || is_saved(a->ssid)) {
            continue;
        }
        const lv_opa_t opa = a->rssi > -60 ? LV_OPA_COVER : a->rssi > -75 ? LV_OPA_70 : LV_OPA_40;
        lv_obj_t *r;
        row(near_cb, i, ICON_WIFI, lv_color_white(), opa, a->ssid, &r);
        if (a->auth != AUTH_OPEN) {
            lv_obj_t *lock = lv_label_create(r);
            lv_obj_set_style_text_opa(lock, LV_OPA_60, 0);
            lv_label_set_text(lock, ICON_LOCK);
        }
        shown++;
    }
    if (shown == 0) {
        lv_obj_t *l = lv_label_create(s_list);
        lv_obj_set_style_text_opa(l, LV_OPA_50, 0);
        lv_label_set_text(l, ui_str(s_scanning || s_scan_wanted ? STR_WIFI_SCANNING : STR_WIFI_NONE));
    }

    lv_obj_t *b = lv_button_create(s_list);
    lv_obj_remove_style_all(b);
    lv_obj_set_size(b, CONTENT_W, 56);
    lv_obj_set_style_radius(b, 28, 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(PILL_BG), 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(ROW_BG), LV_STATE_PRESSED);
    lv_obj_set_style_margin_top(b, 14, 0);
    lv_obj_add_event_cb(b, phone_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *t = lv_label_create(b);
    lv_label_set_text(t, ui_str(STR_WIFI_PHONE));
    lv_obj_center(t);

    lv_obj_update_layout(s_list);
    lv_obj_scroll_to_y(s_list, y, LV_ANIM_OFF);
}

/* ------------------------------------------------------------------------- */
/* Screen                                                                     */
/* ------------------------------------------------------------------------- */

static void poll_cb(lv_timer_t *t)
{
    lv_obj_t *active = lv_screen_active();
    if (active != s_scr && active != s_pw_scr) {
        memset(&s_saved, 0, sizeof(s_saved));
        lv_timer_pause(t);
        return;
    }
    bool changed = false;
    if (s_scan_wanted) {
        if (!net_scan_running() && net_scan_refresh() == ESP_OK) {
            s_scan_wanted = false;
            s_scanning = true;
        } else if (++s_scan_tries >= SCAN_TRIES) {
            s_scan_wanted = false;      /* the radio stayed busy: "No networks found", the header retries */
            changed = true;
        }
    } else if (s_scanning && !net_scan_running()) {
        s_scanning = false;
        s_near_count = net_scan_results(s_near, NEARBY_MAX);
        changed = true;
    }
    char cur[33];
    net_wifi_current_ssid(cur);
    const net_join_state_t j = net_wifi_join_state();
    if (strcmp(cur, s_cur) != 0 || j != s_join_shown) {
        strlcpy(s_cur, cur, sizeof(s_cur));
        s_join_shown = j;
        settings_get_wifi_list(&s_saved);   /* a network that just connected is saved now */
        changed = true;
    }
    if (s_forget_armed >= 0 && lv_tick_elaps(s_forget_ms) >= FORGET_ARM_MS) {
        s_forget_armed = -1;
        changed = true;
    }
    if (changed && active == s_scr) {
        rebuild();
    }
}

static void close_cb(lv_event_t *e)
{
    memset(&s_saved, 0, sizeof(s_saved));
    lv_timer_pause(s_poll);
    if (s_from_settings) {
        ui_settings_return();
    } else {
        ui_go_watchface();
    }
}

void ui_wifi_init(void)
{
    s_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_scr, ui_style_screen(), 0);
    lv_obj_remove_flag(s_scr, LV_OBJ_FLAG_SCROLLABLE);

    s_title = lv_label_create(s_scr);
    lv_obj_set_style_text_font(s_title, &buddy_font_28, 0);
    lv_obj_align(s_title, LV_ALIGN_TOP_LEFT, 36, 42);

    lv_obj_t *x = ui_add_close_x(s_scr, close_cb);

    s_list = lv_obj_create(s_scr);
    lv_obj_remove_style_all(s_list);
    lv_obj_set_size(s_list, CONTENT_W, BOARD_LCD_V_RES - LIST_Y);
    lv_obj_align(s_list, LV_ALIGN_TOP_MID, 0, LIST_Y);
    lv_obj_set_scroll_dir(s_list, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(s_list, LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_flex_flow(s_list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_list, 8, 0);
    lv_obj_set_style_pad_bottom(s_list, 40, 0);
    lv_obj_move_foreground(x);

    build_password_screen();

    s_poll = lv_timer_create(poll_cb, POLL_MS, NULL);
    lv_timer_pause(s_poll);
}

void ui_wifi_open(bool from_settings)
{
    s_from_settings = from_settings;
    settings_get_wifi_list(&s_saved);
    net_wifi_current_ssid(s_cur);
    net_wifi_join_ack();                /* an old result is not news any more */
    s_join_shown = net_wifi_join_state();
    s_join_ssid[0] = '\0';
    s_forget_armed = -1;
    s_near_count = net_scan_results(s_near, NEARBY_MAX);   /* the last scan, until the new one ends */
    s_scan_wanted = true;
    s_scanning = false;
    s_scan_tries = 0;
    lv_label_set_text(s_title, ui_str(STR_WIFI));
    rebuild();
    lv_obj_scroll_to_y(s_list, 0, LV_ANIM_OFF);
    lv_timer_resume(s_poll);
    lv_timer_ready(s_poll);
    ui_note_activity();
    ui_load_screen(s_scr, true);
}
