/*
 * ola - notes and reminders screens (PROTOCOL.md section 3.3).
 *
 * Two separate lists, each opened from its own icon under the date on the
 * watchface (pen = notes, calendar = reminders) or by the server when the user
 * asks to see them (items_open):
 *  - Notes: "#n  first line"; tapping asks the server for the full text
 *    (item_open -> item_show).
 *  - Reminders: "#n  text", due time below, red "Overdue" once the time has
 *    passed, green "Completed" in its place once marked done (Complete is on
 *    the detail screen). The snapshot already carries the whole (short) text.
 *
 * Detail: full screen with an X in the top-right corner, "Note #n" /
 * "Reminder #n", due time + Overdue / Completed (reminders), scrollable text,
 * then Complete / Reopen (reminders) and Delete (tap twice). The X goes back
 * to that list, or to the watchface when the item was opened by voice or by a
 * due reminder (reminder_fire: screen wakes and stays on until closed or
 * ALERT_HOLD_MS passes).
 *
 * The snapshot rows live in PSRAM; JSON is parsed outside the LVGL lock.
 */
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "board.h"
#include "ui_priv.h"

static const char *TAG = "ui_items";

#define LOCK()      board_display_lock(0)
#define UNLOCK()    board_display_unlock()

#define CONTENT_W       340
#define MAX_ROWS        200
#define ROW_TEXT_MAX    328         /* reminder text: 80 chars, up to 4 bytes each */
#define DUE_MAX         20
#define ALERT_HOLD_MS   60000
#define DELETE_ARM_MS   3000
#define ROW_BG          0x1a1f28
#define PILL_BG         0x252b36
#define DANGER          0xe5534b
#define SCREEN_PAD_TOP  34
#define CLOSE_Y         10          /* X: px from the top edge of the display */
#define ROW_LINES       2           /* text lines per list row, then "…" */
#define DONE_GREEN      0x2fbf71
#define PILL_H          48

typedef struct {
    bool reminder;
    bool overdue;
    bool done;                  /* reminders: completed */
    int  number;
    char text[ROW_TEXT_MAX];    /* note preview or reminder text */
    char due[DUE_MAX];          /* "YYYY-MM-DD HH:MM" local, "" for notes */
} item_row_t;

typedef struct {
    bool      reminder;
    lv_obj_t *scr;
    lv_obj_t *title;
    lv_obj_t *body;             /* rows are rebuilt in here */
} list_screen_t;

/* ---- snapshot ---- */
static item_row_t *s_rows;      /* PSRAM, MAX_ROWS */
static int         s_row_count;
static bool        s_have_snapshot;

/* ---- lists ---- */
static list_screen_t s_lists[2];    /* [0] notes, [1] reminders */
static bool          s_row_swallow;

/* ---- detail ---- */
static lv_obj_t   *s_det_scr;
static lv_obj_t   *s_det_header;
static lv_obj_t   *s_det_due;
static lv_obj_t   *s_det_text;
static lv_obj_t   *s_det_done_btn;
static lv_obj_t   *s_det_done_lbl;
static lv_obj_t   *s_det_del;
static lv_obj_t   *s_det_del_lbl;
static bool        s_det_reminder;
static bool        s_det_done;
static int         s_det_number;
static char        s_det_due_str[DUE_MAX];
static bool        s_det_from_list;
static bool        s_det_alert;
static bool        s_del_armed;
static lv_timer_t *s_del_timer;
static lv_timer_t *s_alert_timer;

/* ------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* ------------------------------------------------------------------------- */

static void copy_str(char *dst, size_t len, const char *src)
{
    if (!src) {
        dst[0] = '\0';
        return;
    }
    size_t n = strlen(src);
    if (n >= len) {
        n = len - 1;
        while (n > 0 && ((unsigned char)src[n] & 0xC0) == 0x80) {
            n--;                /* don't cut a UTF-8 sequence in half */
        }
    }
    memcpy(dst, src, n);
    dst[n] = '\0';
}

static const char *jstr(const cJSON *o, const char *key)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, key);
    return cJSON_IsString(v) ? v->valuestring : NULL;
}

static int jint(const cJSON *o, const char *key)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, key);
    return cJSON_IsNumber(v) ? v->valueint : 0;
}

/* "YYYY-MM-DD HH:MM" -> "Today 15:30" / "Tomorrow 09:00" / "3 Oct 18:00". */
static void fmt_due(const char *due, char *out, size_t len)
{
    int y, mo, d, h, mi;
    if (!due || sscanf(due, "%d-%d-%d %d:%d", &y, &mo, &d, &h, &mi) != 5) {
        copy_str(out, len, due ? due : "");
        return;
    }
    static const char *const mon[] = { "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };
    time_t now = time(NULL);
    struct tm today, tomorrow;
    localtime_r(&now, &today);
    time_t next = now + 24 * 3600;
    localtime_r(&next, &tomorrow);
    if (today.tm_year + 1900 == y && today.tm_mon + 1 == mo && today.tm_mday == d) {
        snprintf(out, len, "%s %02d:%02d", ui_str(STR_TODAY), h, mi);
    } else if (tomorrow.tm_year + 1900 == y && tomorrow.tm_mon + 1 == mo && tomorrow.tm_mday == d) {
        snprintf(out, len, "%s %02d:%02d", ui_str(STR_TOMORROW), h, mi);
    } else {
        snprintf(out, len, "%d %s %02d:%02d", d, mon[(mo - 1 + 12) % 12], h, mi);
    }
}

static void add_close_button(lv_obj_t *scr, lv_event_cb_t cb)
{
    lv_obj_t *x = lv_button_create(scr);
    lv_obj_remove_style_all(x);
    lv_obj_add_flag(x, LV_OBJ_FLAG_FLOATING);   /* outside the flex column and scrolling */
    lv_obj_set_size(x, 56, 56);
    /* Alignment is relative to the padded content area: undo the top padding. */
    lv_obj_align(x, LV_ALIGN_TOP_RIGHT, -30, CLOSE_Y - SCREEN_PAD_TOP);
    lv_obj_set_style_radius(x, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(x, lv_color_hex(PILL_BG), 0);
    lv_obj_set_style_bg_opa(x, LV_OPA_TRANSP, 0);    /* just the X; the 56 px touch area stays */
    lv_obj_set_ext_click_area(x, 12);
    lv_obj_add_event_cb(x, cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *l = lv_label_create(x);
    lv_obj_set_style_text_font(l, &buddy_font_28, 0);
    lv_obj_set_style_text_color(l, lv_color_white(), 0);
    lv_label_set_text(l, ICON_CLOSE);
    lv_obj_center(l);
}

static lv_obj_t *column_screen(lv_event_cb_t gesture_cb, void *user_data)
{
    lv_obj_t *scr = lv_obj_create(NULL);
    lv_obj_add_style(scr, ui_style_screen(), 0);
    lv_obj_add_event_cb(scr, gesture_cb, LV_EVENT_GESTURE, user_data);
    lv_obj_set_scroll_dir(scr, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(scr, LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_flex_flow(scr, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(scr, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_top(scr, SCREEN_PAD_TOP, 0);
    lv_obj_set_style_pad_bottom(scr, 48, 0);
    lv_obj_set_style_pad_row(scr, 10, 0);
    return scr;
}

static lv_obj_t *wrap_label(lv_obj_t *parent, const lv_font_t *font)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_width(l, CONTENT_W);
    lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_add_flag(l, LV_OBJ_FLAG_GESTURE_BUBBLE);
    return l;
}

/* Red "! Overdue" tag. */
static void overdue_tag(lv_obj_t *l)
{
    lv_obj_set_style_text_color(l, lv_color_hex(DANGER), 0);
    lv_label_set_text_fmt(l, ICON_WARNING " %s", ui_str(STR_OVERDUE));
}

/* Green "✓ Completed" tag, where Overdue would be. */
static void done_tag(lv_obj_t *l)
{
    lv_obj_set_style_text_color(l, lv_color_hex(DONE_GREEN), 0);
    lv_label_set_text_fmt(l, ICON_OK " %s", ui_str(STR_COMPLETED));
}

/* "YYYY-MM-DD HH:MM" (local) is now or earlier. False while the clock is not set. */
static bool due_passed(const char *due)
{
    int y, mo, d, h, mi;
    time_t now = time(NULL);
    struct tm today;
    localtime_r(&now, &today);
    if (!due || today.tm_year < 124 || sscanf(due, "%d-%d-%d %d:%d", &y, &mo, &d, &h, &mi) != 5) {
        return false;
    }
    struct tm t = { .tm_year = y - 1900, .tm_mon = mo - 1, .tm_mday = d, .tm_hour = h, .tm_min = mi,
                    .tm_isdst = -1 };
    return mktime(&t) <= now;
}

/* A full-width rounded action button with a centred label. */
static lv_obj_t *make_pill(lv_obj_t *parent, lv_event_cb_t cb, lv_obj_t **label_out)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_size(b, CONTENT_W - 60, PILL_H);
    lv_obj_set_style_radius(b, PILL_H / 2, 0);
    lv_obj_set_style_shadow_width(b, 0, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(PILL_BG), 0);
    lv_obj_add_flag(b, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *l = lv_label_create(b);
    lv_obj_set_style_text_color(l, lv_color_white(), 0);
    lv_obj_center(l);
    *label_out = l;
    return b;
}

/* ------------------------------------------------------------------------- */
/* Lists                                                                      */
/* ------------------------------------------------------------------------- */

static void open_list(bool reminder, bool slide_from_left);

/* Watchface counters: all notes, reminders that are neither overdue nor completed. */
static void update_counts(void)
{
    int notes = 0, reminders = 0;
    for (int i = 0; i < s_row_count; i++) {
        if (!s_rows[i].reminder) {
            notes++;
        } else if (!s_rows[i].overdue && !s_rows[i].done) {
            reminders++;
        }
    }
    ui_shortcut_counts(notes, reminders);
}
static void show_detail(bool reminder, int number, const char *text, const char *due, bool overdue,
                        bool done, bool from_list, bool alert);
static void set_done(int number, bool done);

static void list_close_cb(lv_event_t *e)
{
    ui_go_watchface();
}

static void list_gesture_cb(lv_event_t *e)
{
    lv_dir_t dir = lv_indev_get_gesture_dir(lv_indev_active());
    if (dir == LV_DIR_RIGHT) {
        lv_indev_wait_release(lv_indev_active());
        ui_go_watchface();
    }
}

static void row_event_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_PRESSED) {
        s_row_swallow = !ui_screen_awake();     /* a tap on a dim screen only wakes it */
        return;
    }
    if (code != LV_EVENT_CLICKED || s_row_swallow) {
        return;
    }
    int idx = (int)(intptr_t)lv_event_get_user_data(e);
    if (idx < 0 || idx >= s_row_count) {
        return;
    }
    item_row_t r = s_rows[idx];
    if (r.reminder) {
        /* The snapshot has the whole reminder: show it right away. */
        show_detail(true, r.number, r.text, r.due, r.overdue, r.done, true, false);
    } else {
        show_detail(false, r.number, NULL, NULL, false, false, true, false);  /* "Loading…" until item_show */
        if (g_ui_cb.on_item_open) {
            g_ui_cb.on_item_open(false, r.number);
        }
    }
}

static void add_row(lv_obj_t *parent, int idx)
{
    const item_row_t *r = &s_rows[idx];
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_set_size(b, CONTENT_W, LV_SIZE_CONTENT);
    lv_obj_set_style_bg_color(b, lv_color_hex(ROW_BG), 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(PILL_BG), LV_STATE_PRESSED);
    lv_obj_set_style_radius(b, 18, 0);
    lv_obj_set_style_pad_hor(b, 16, 0);
    lv_obj_set_style_pad_ver(b, 12, 0);
    lv_obj_set_style_pad_row(b, 4, 0);
    lv_obj_set_flex_flow(b, LV_FLEX_FLOW_COLUMN);
    lv_obj_add_flag(b, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(b, row_event_cb, LV_EVENT_PRESSED, (void *)(intptr_t)idx);
    lv_obj_add_event_cb(b, row_event_cb, LV_EVENT_CLICKED, (void *)(intptr_t)idx);

    lv_obj_t *line = lv_obj_create(b);
    lv_obj_remove_style_all(line);
    lv_obj_set_size(line, CONTENT_W - 32, LV_SIZE_CONTENT);
    lv_obj_remove_flag(line, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(line, LV_OBJ_FLAG_EVENT_BUBBLE | LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_flex_flow(line, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(line, 10, 0);
    lv_obj_t *num = lv_label_create(line);
    lv_obj_set_style_text_color(num, g_ui_theme.accent, 0);
    lv_label_set_text_fmt(num, "#%d", r->number);
    lv_obj_t *l = lv_label_create(line);
    lv_obj_set_flex_grow(l, 1);
    /* As much of the text as fits in ROW_LINES lines, then "…" */
    lv_label_set_long_mode(l, LV_LABEL_LONG_DOT);
    lv_obj_set_height(l, LV_SIZE_CONTENT);
    lv_obj_set_style_max_height(l, ROW_LINES * lv_font_get_line_height(&buddy_font_20), 0);
    lv_label_set_text(l, r->text[0] ? r->text : "…");

    if (r->reminder && r->due[0]) {
        lv_obj_t *meta = lv_obj_create(b);
        lv_obj_remove_style_all(meta);
        lv_obj_set_size(meta, CONTENT_W - 32, LV_SIZE_CONTENT);
        lv_obj_remove_flag(meta, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_flag(meta, LV_OBJ_FLAG_EVENT_BUBBLE | LV_OBJ_FLAG_GESTURE_BUBBLE);
        lv_obj_set_flex_flow(meta, LV_FLEX_FLOW_ROW);
        lv_obj_set_style_pad_column(meta, 12, 0);
        char due[32];
        fmt_due(r->due, due, sizeof(due));
        lv_obj_t *d = lv_label_create(meta);
        lv_obj_set_style_text_opa(d, LV_OPA_70, 0);
        lv_label_set_text_fmt(d, ICON_CALENDAR " %s", due);
        if (r->done) {
            done_tag(lv_label_create(meta));
        } else if (r->overdue) {
            overdue_tag(lv_label_create(meta));
        }
    }
}

static void rebuild_list(list_screen_t *ls)
{
    lv_obj_clean(ls->body);
    lv_label_set_text(ls->title, ui_str(ls->reminder ? STR_REMINDERS : STR_NOTES));
    int count = 0;
    for (int i = 0; s_have_snapshot && i < s_row_count; i++) {
        if (s_rows[i].reminder == ls->reminder) {
            add_row(ls->body, i);
            count++;
        }
    }
    if (count == 0) {
        lv_obj_t *l = wrap_label(ls->body, &buddy_font_20);
        lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_style_text_opa(l, LV_OPA_70, 0);
        lv_obj_set_style_pad_top(l, 40, 0);
        lv_label_set_text(l, !s_have_snapshot ? ui_str(STR_LOADING)
                             : ui_str(ls->reminder ? STR_NO_REMINDERS : STR_NO_NOTES));
    }
}

static void build_list(list_screen_t *ls, bool reminder)
{
    ls->reminder = reminder;
    ls->scr = column_screen(list_gesture_cb, ls);
    add_close_button(ls->scr, list_close_cb);

    ls->title = lv_label_create(ls->scr);
    lv_obj_set_width(ls->title, CONTENT_W);
    lv_obj_set_style_text_font(ls->title, &buddy_font_28, 0);
    lv_obj_set_style_pad_top(ls->title, 8, 0);
    lv_obj_set_style_pad_bottom(ls->title, 6, 0);
    lv_obj_add_flag(ls->title, LV_OBJ_FLAG_GESTURE_BUBBLE);

    ls->body = lv_obj_create(ls->scr);
    lv_obj_remove_style_all(ls->body);
    lv_obj_set_size(ls->body, CONTENT_W, LV_SIZE_CONTENT);
    lv_obj_remove_flag(ls->body, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(ls->body, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_flex_flow(ls->body, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(ls->body, 10, 0);
}

static void open_list(bool reminder, bool slide_from_left)
{
    list_screen_t *ls = &s_lists[reminder ? 1 : 0];
    rebuild_list(ls);
    lv_obj_scroll_to_y(ls->scr, 0, LV_ANIM_OFF);
    ui_note_activity();
    ui_load_screen(ls->scr, !slide_from_left);
}

/* ------------------------------------------------------------------------- */
/* Detail                                                                     */
/* ------------------------------------------------------------------------- */

static void end_alert(void)
{
    if (s_alert_timer) {
        lv_timer_delete(s_alert_timer);
        s_alert_timer = NULL;
    }
    if (s_det_alert) {
        s_det_alert = false;
        ui_hold_awake(false);
    }
}

static void disarm_delete(void)
{
    if (s_del_timer) {
        lv_timer_delete(s_del_timer);
        s_del_timer = NULL;
    }
    s_del_armed = false;
    if (s_det_del) {
        lv_obj_set_style_bg_color(s_det_del, lv_color_hex(PILL_BG), 0);
        lv_label_set_text(s_det_del_lbl, ui_str(STR_DELETE));
    }
}

static void close_detail(void)
{
    end_alert();
    disarm_delete();
    if (s_det_from_list) {
        open_list(s_det_reminder, true);
    } else {
        ui_go_watchface();
    }
}

static void det_close_cb(lv_event_t *e)
{
    close_detail();
}

static void det_gesture_cb(lv_event_t *e)
{
    lv_dir_t dir = lv_indev_get_gesture_dir(lv_indev_active());
    if (dir == LV_DIR_RIGHT) {
        lv_indev_wait_release(lv_indev_active());
        close_detail();
    }
}

static void del_timer_cb(lv_timer_t *t)
{
    s_del_timer = NULL;
    disarm_delete();
}

static void alert_timer_cb(lv_timer_t *t)
{
    s_alert_timer = NULL;
    if (s_det_alert) {
        s_det_alert = false;
        ui_hold_awake(false);       /* screen may dim again; the reminder stays shown */
    }
}

static void del_cb(lv_event_t *e)
{
    if (!s_del_armed) {
        s_del_armed = true;
        lv_obj_set_style_bg_color(s_det_del, lv_color_hex(DANGER), 0);
        lv_label_set_text(s_det_del_lbl, ui_str(STR_DELETE_CONFIRM));
        s_del_timer = lv_timer_create(del_timer_cb, DELETE_ARM_MS, NULL);
        lv_timer_set_repeat_count(s_del_timer, 1);
        return;
    }
    if (g_ui_cb.on_item_delete) {
        g_ui_cb.on_item_delete(s_det_reminder, s_det_number);
    }
    /* Drop the row locally so the list is right before the server's snapshot arrives. */
    for (int i = 0; i < s_row_count; i++) {
        if (s_rows[i].reminder == s_det_reminder && s_rows[i].number == s_det_number) {
            memmove(&s_rows[i], &s_rows[i + 1], (size_t)(s_row_count - i - 1) * sizeof(item_row_t));
            s_row_count--;
            break;
        }
    }
    update_counts();
    close_detail();
}

static void done_btn_cb(lv_event_t *e)
{
    set_done(s_det_number, !s_det_done);
}

static void build_detail(void)
{
    s_det_scr = column_screen(det_gesture_cb, NULL);
    add_close_button(s_det_scr, det_close_cb);

    s_det_header = wrap_label(s_det_scr, &buddy_font_28);
    lv_obj_set_style_pad_top(s_det_header, 8, 0);
    lv_obj_set_style_pad_right(s_det_header, 70, 0);     /* keep clear of the X */

    s_det_due = lv_obj_create(s_det_scr);
    lv_obj_remove_style_all(s_det_due);
    lv_obj_set_size(s_det_due, CONTENT_W, LV_SIZE_CONTENT);
    lv_obj_remove_flag(s_det_due, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(s_det_due, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_flex_flow(s_det_due, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_style_pad_column(s_det_due, 14, 0);

    s_det_text = wrap_label(s_det_scr, &buddy_font_20);
    lv_obj_set_style_text_line_space(s_det_text, 2, 0);
    lv_obj_set_style_pad_top(s_det_text, 8, 0);

    /* Complete / Reopen (reminders), Delete */
    s_det_done_btn = make_pill(s_det_scr, done_btn_cb, &s_det_done_lbl);
    lv_obj_set_style_margin_top(s_det_done_btn, 24, 0);
    s_det_del = make_pill(s_det_scr, del_cb, &s_det_del_lbl);
}

/* Complete / Reopen button and the due line's tag follow s_det_done. */
static void detail_apply_done(bool overdue)
{
    lv_obj_set_flag(s_det_done_btn, LV_OBJ_FLAG_HIDDEN, !s_det_reminder || lv_obj_has_flag(s_det_del, LV_OBJ_FLAG_HIDDEN));
    lv_obj_set_style_bg_color(s_det_done_btn, s_det_done ? lv_color_hex(PILL_BG) : lv_color_hex(DONE_GREEN), 0);
    if (s_det_done) {
        lv_label_set_text_fmt(s_det_done_lbl, ICON_REFRESH "  %s", ui_str(STR_REOPEN));
    } else {
        lv_label_set_text_fmt(s_det_done_lbl, ICON_OK "  %s", ui_str(STR_COMPLETE));
    }
    lv_obj_clean(s_det_due);
    if (s_det_reminder && s_det_due_str[0]) {
        char buf[32];
        fmt_due(s_det_due_str, buf, sizeof(buf));
        lv_obj_t *d = lv_label_create(s_det_due);
        lv_label_set_text_fmt(d, ICON_CALENDAR " %s", buf);
        if (s_det_done) {
            done_tag(lv_label_create(s_det_due));
        } else if (overdue) {
            overdue_tag(lv_label_create(s_det_due));
        }
        lv_obj_remove_flag(s_det_due, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_det_due, LV_OBJ_FLAG_HIDDEN);
    }
}

/* Fill and show the detail screen. `text` NULL = note still loading. */
static void show_detail(bool reminder, int number, const char *text, const char *due, bool overdue,
                        bool done, bool from_list, bool alert)
{
    disarm_delete();
    s_det_reminder = reminder;
    s_det_number = number;
    s_det_from_list = from_list;
    s_det_done = reminder && done;
    copy_str(s_det_due_str, sizeof(s_det_due_str), reminder ? due : NULL);

    lv_obj_set_style_text_color(s_det_header, g_ui_theme.accent, 0);
    lv_label_set_text_fmt(s_det_header, "%s #%d", ui_str(reminder ? STR_REMINDER : STR_NOTE), number);

    lv_obj_set_style_text_font(s_det_text, reminder ? &buddy_font_28 : &buddy_font_20, 0);
    lv_obj_set_style_text_opa(s_det_text, text ? LV_OPA_COVER : LV_OPA_50, 0);
    lv_label_set_text(s_det_text, text ? text : ui_str(STR_LOADING));
    lv_obj_set_flag(s_det_del, LV_OBJ_FLAG_HIDDEN, text == NULL);
    detail_apply_done(overdue);

    if (alert) {
        end_alert();
        s_det_alert = true;
        ui_hold_awake(true);
        s_alert_timer = lv_timer_create(alert_timer_cb, ALERT_HOLD_MS, NULL);
        lv_timer_set_repeat_count(s_alert_timer, 1);
    }
    lv_obj_scroll_to_y(s_det_scr, 0, LV_ANIM_OFF);
    ui_note_activity();
    ui_load_screen(s_det_scr, true);
}

/* Parse an item_show / reminder_fire item and show it. Caller holds the lock. */
static void show_item_json(const cJSON *item, bool alert)
{
    const char *kind = jstr(item, "kind");
    bool reminder = alert || (kind && strcmp(kind, "reminder") == 0);
    int number = jint(item, "number");
    const char *text = jstr(item, "text");
    bool overdue = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(item, "overdue"));
    bool done = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(item, "done"));
    /* Opened from the list and still waiting for this note: keep "back" going to the list. */
    bool from_list = !alert && lv_screen_active() == s_det_scr && s_det_from_list &&
                     s_det_reminder == reminder && s_det_number == number;
    show_detail(reminder, number, text ? text : "", jstr(item, "due_local"), overdue, done, from_list, alert);
}

static void rebuild_reminders_async(void *arg)
{
    if (lv_screen_active() == s_lists[1].scr) {
        int32_t y = lv_obj_get_scroll_y(s_lists[1].scr);
        rebuild_list(&s_lists[1]);
        lv_obj_scroll_to_y(s_lists[1].scr, y, LV_ANIM_OFF);
    }
}

/* Complete / reopen a reminder: tell the server, update the row and whatever
 * shows it right away (the server's snapshot follows). Caller holds the lock. */
static void set_done(int number, bool done)
{
    if (g_ui_cb.on_item_done) {
        g_ui_cb.on_item_done(number, done);
    }
    for (int i = 0; i < s_row_count; i++) {
        if (s_rows[i].reminder && s_rows[i].number == number) {
            s_rows[i].done = done;
            s_rows[i].overdue = !done && due_passed(s_rows[i].due);    /* reopened: red again if past */
        }
    }
    update_counts();
    if (lv_screen_active() == s_lists[1].scr) {
        lv_async_call(rebuild_reminders_async, NULL);   /* not from inside a button's own event */
    }
    if (s_det_reminder && s_det_number == number) {
        s_det_done = done;
        detail_apply_done(!done && due_passed(s_det_due_str));
    }
    ui_note_activity();
}

/* ------------------------------------------------------------------------- */
/* Public / internal API                                                      */
/* ------------------------------------------------------------------------- */

void ui_items_init(void)
{
    s_rows = heap_caps_calloc(MAX_ROWS, sizeof(item_row_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_rows) {
        ESP_LOGE(TAG, "no memory for item rows");
    }
    build_list(&s_lists[0], false);
    build_list(&s_lists[1], true);
    build_detail();
    lv_label_set_text(s_det_del_lbl, ui_str(STR_DELETE));
}

void ui_items_open(bool reminder)
{
    open_list(reminder, false);
}

void ui_items_refresh_theme(void)
{
    for (int i = 0; i < 2; i++) {
        if (s_lists[i].scr && lv_screen_active() == s_lists[i].scr) {
            rebuild_list(&s_lists[i]);
        }
    }
}

void ui_items_show_list(bool reminder)
{
    LOCK();
    open_list(reminder, false);
    UNLOCK();
}

void ui_items_set(const char *json)
{
    cJSON *j = cJSON_Parse(json);
    if (!j || !s_rows) {
        cJSON_Delete(j);
        return;
    }
    item_row_t *tmp = heap_caps_calloc(MAX_ROWS, sizeof(item_row_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!tmp) {
        cJSON_Delete(j);
        return;
    }
    int n = 0;
    const char *keys[2] = { "notes", "reminders" };
    for (int k = 0; k < 2; k++) {
        const cJSON *arr = cJSON_GetObjectItemCaseSensitive(j, keys[k]);
        const cJSON *it;
        cJSON_ArrayForEach(it, arr) {
            if (n >= MAX_ROWS) {
                break;
            }
            item_row_t *r = &tmp[n++];
            r->reminder = (k == 1);
            r->number = jint(it, "number");
            r->overdue = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(it, "overdue"));
            r->done = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(it, "done"));
            copy_str(r->text, sizeof(r->text), jstr(it, r->reminder ? "text" : "preview"));
            copy_str(r->due, sizeof(r->due), jstr(it, "due_local"));
        }
    }
    cJSON_Delete(j);

    LOCK();
    memcpy(s_rows, tmp, (size_t)n * sizeof(item_row_t));
    s_row_count = n;
    s_have_snapshot = true;
    update_counts();
    for (int i = 0; i < 2; i++) {
        if (lv_screen_active() == s_lists[i].scr) {
            rebuild_list(&s_lists[i]);
        }
    }
    UNLOCK();
    free(tmp);
    ESP_LOGI(TAG, "%d items", n);
}

void ui_item_show(const char *json)
{
    cJSON *j = cJSON_Parse(json);
    const cJSON *item = j ? cJSON_GetObjectItemCaseSensitive(j, "item") : NULL;
    if (cJSON_IsObject(item)) {
        LOCK();
        show_item_json(item, false);
        UNLOCK();
    }
    cJSON_Delete(j);
}

void ui_reminder_alert(const char *json)
{
    cJSON *j = cJSON_Parse(json);
    const cJSON *item = j ? cJSON_GetObjectItemCaseSensitive(j, "item") : NULL;
    if (cJSON_IsObject(item)) {
        ui_wake();
        LOCK();
        show_item_json(item, true);
        UNLOCK();
    }
    cJSON_Delete(j);
}
