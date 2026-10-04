/*
 * ola - notes and reminders screens (PROTOCOL.md section 3.3).
 *
 * Two separate lists, each opened from its own icon under the date on the
 * watchface (pen = notes, calendar = reminders) or by the server when the user
 * asks to see them (items_open):
 *  - Notes: "#n  first line"; tapping asks the server for the full text
 *    (item_open -> item_show).
 *  - Reminders: grouped by day under a "Today" / "Tomorrow" / weekday header
 *    with the date on the right, in time order (completed ones last in their
 *    day). Each row: a coloured "#n" badge, a bar in the same colour, the text
 *    and "09:30" or "09:30 – 10:00" below, then a chevron. Days before today
 *    are not shown on the watch (the web app still lists them). "Overdue" (red)
 *    only for today's open reminders whose time (the end of a range) has
 *    passed; "Completed" (green) once marked done (Complete is on the detail
 *    screen). A bell marks a reminder with an advance notice (it also alerts
 *    that many minutes before the start; the server sends that alert). The snapshot already carries the whole (short) text.
 *
 * Notes list: like the reminders list without days - one box per note: a
 * coloured "#n" badge, the note's first line, a pin on pinned notes (they come
 * first), a chevron.
 * Note screen: header (battery | "Note #n" | X), the note's lines
 * numbered 1. 2. 3. in a scrolling card, and a fixed bar: Delete (tap twice),
 * the mic and Pin. The mic opens the note edit mode: it stays open (red, white
 * stop square, pulsing ring) until tapped again, the screen is left or 2 min
 * pass without speech (protocol_client.c); every sentence is applied to this
 * note by the server and the changed line flashes.
 * Reminder detail: header like quick settings (battery | "Reminder #n"
 * | X); one card from the header down to the buttons (it scrolls when taller):
 * a calendar tile with the date, the time ("09:30 – 10:15") and a status line
 * (Overdue / Completed / "Starts in 10 min" for the advance alert), then rows
 * Location (if set), Description (always: the reminder's text), Reminder (the
 * advance notice, if set) and Participants (if set); location and participants
 * come from the text, set by the assistant. Fixed buttons below: Done / Reopen
 * and Delete (tap twice). The X goes back
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
#define LIST_W          370         /* reminders list: day headers and rows */
#define BADGE_SIZE      46
#define DONE_GREY       0x6b7280
#define LOC_MAX         256         /* location: 120 chars, UTF-8 */
#define PEOPLE_MAX      400         /* participants: 200 chars, UTF-8 */
/* Reminder screen geometry (410 x 502) */
#define REM_W           370
#define REM_HDR_Y       12
#define REM_HDR_H       48
#define REM_BODY_Y      70
#define REM_STATUS_X    40          /* battery clear of the rounded corner */
#define REM_TITLE_MAX_W 170         /* wider titles use the smaller font */

typedef struct {
    bool reminder;
    bool done;                  /* reminders: completed */
    int  number;
    char text[ROW_TEXT_MAX];    /* note preview or reminder text */
    char due[DUE_MAX];          /* "YYYY-MM-DD HH:MM" local, "" for notes */
    char end[DUE_MAX];          /* reminders: end of a time range, "" for a single time */
    int  notify_before;         /* reminders: advance notice in minutes, 0 = none */
    char location[LOC_MAX];     /* reminders, "" = none */
    char participants[PEOPLE_MAX];  /* reminders: "Ana, Mihai", "" = none */
    bool pinned;                /* notes: kept at the top */
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
static lv_style_t    s_st_rcard;            /* reminder row, theme tinted */
static lv_style_t    s_st_rcard_pressed;
static int           s_order[MAX_ROWS];     /* reminders shown, in display order */

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
static char        s_det_end_str[DUE_MAX];
static int         s_det_notify;
static bool        s_det_from_list;
static bool        s_det_alert;
static bool        s_del_armed;
static lv_timer_t *s_del_timer;
static lv_timer_t *s_alert_timer;
static lv_obj_t   *s_del_btn;       /* the Delete button on the screen shown (note or reminder) */
static lv_obj_t   *s_del_btn_lbl;
static bool        s_del_outlined;  /* reminder screen: outlined Delete */

/* ---- reminder detail ---- */
static lv_obj_t   *s_rem_scr;
static lv_obj_t   *s_rem_title;
static lv_obj_t   *s_rem_batt;
static lv_obj_t   *s_rem_body;      /* scrolls */
static lv_obj_t   *s_rem_card;
static lv_obj_t   *s_rem_tile;
static lv_obj_t   *s_rem_date;
static lv_obj_t   *s_rem_time;
static lv_obj_t   *s_rem_status;
static lv_obj_t   *s_rem_rows;
static lv_obj_t   *s_rem_del;
static lv_obj_t   *s_rem_del_icon;
static lv_obj_t   *s_rem_mic;
static lv_obj_t   *s_rem_mic_icon;
static lv_obj_t   *s_rem_stop;
static lv_obj_t   *s_rem_ring;
static lv_obj_t   *s_rem_check;     /* Completed on / off */
static lv_obj_t   *s_rem_check_icon;
static lv_obj_t   *s_rem_live;      /* transcript / question / help, under the rows */
static item_row_t *s_rem_row;       /* the reminder shown (PSRAM) */
static bool        s_rem_early;     /* shown by its advance alert */
static lv_obj_t   *s_rem_cal;       /* calendar icon on the tile */

/* ---- note screen ---- */
#define NOTE_TEXT_MAX   24576       /* note text: 10000 chars, UTF-8 */
#define NOTE_MAX_LINES  120
#define NOTE_BTN        64
#define NOTE_MIC        88
#define NOTE_BAR_BOTTOM 16
#define NOTE_BODY_H     (BOARD_LCD_V_RES - NOTE_BAR_BOTTOM - NOTE_MIC - 14 - REM_BODY_Y)
#define NOTE_FLASH_MS   1400
static lv_obj_t   *s_note_scr;
static lv_obj_t   *s_note_title;
static lv_obj_t   *s_note_batt;
static lv_obj_t   *s_note_body;
static lv_obj_t   *s_note_card;
static lv_obj_t   *s_note_tile;
static lv_obj_t   *s_note_doc;
static lv_obj_t   *s_note_del_icon;
static lv_obj_t   *s_note_head;      /* the note's title (its first line) */
static lv_obj_t   *s_note_divider;
static lv_obj_t   *s_note_lines;
static lv_obj_t   *s_note_live;      /* transcript / question / help, under the lines */
static lv_obj_t   *s_note_del;
static lv_obj_t   *s_note_mic;
static lv_obj_t   *s_note_mic_icon;
static lv_obj_t   *s_note_stop;      /* white square on the red mic while open */
static lv_obj_t   *s_note_ring;      /* pulsing ring while open */
static lv_obj_t   *s_note_pin;
static lv_obj_t   *s_note_pin_icon;
static char       *s_note_text;      /* PSRAM */
static int         s_note_number;
static bool        s_note_pinned;
static bool        s_note_loading;
static bool        s_note_mic_open;
static int         s_note_flash = -1;    /* highlight: -1 none, 0 the title, n numbered line n */
static lv_timer_t *s_note_flash_timer;
static bool        s_del_icon;       /* the note screen's Delete is an icon */
/* The edit mic of the item screen shown (note or reminder): one session at a time. */
static lv_obj_t   *s_mic;
static lv_obj_t   *s_mic_icon;
static lv_obj_t   *s_mic_stop;
static lv_obj_t   *s_mic_ring;
static lv_obj_t   *s_live;
static bool        s_mic_reminder;   /* the open session's item */
static int         s_mic_number;
static void note_mic_visuals(void);
static void rem_mic_cb(lv_event_t *e);
static void item_bar(lv_obj_t *scr, lv_event_cb_t mic_cb, lv_event_cb_t right_cb, const char *right_icon,
                     const lv_font_t *right_font, lv_obj_t **del, lv_obj_t **del_icon, lv_obj_t **mic,
                     lv_obj_t **mic_icon, lv_obj_t **stop, lv_obj_t **ring, lv_obj_t **right,
                     lv_obj_t **right_icon_out);
static void note_live_reset(void);
static void note_live_set(const char *text, lv_color_t color);
static void note_render(void);
static void note_show_json(const cJSON *item, int number);
static void build_note_screen(void);

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

static bool parse_local(const char *s, int *y, int *mo, int *d, int *h, int *mi)
{
    return s && sscanf(s, "%d-%d-%d %d:%d", y, mo, d, h, mi) == 5;
}

/* Today as YYYYMMDD, 0 while the clock is not set. */
static int today_key(void)
{
    time_t now = time(NULL);
    struct tm t;
    localtime_r(&now, &t);
    return t.tm_year < 124 ? 0 : (t.tm_year + 1900) * 10000 + (t.tm_mon + 1) * 100 + t.tm_mday;
}

/* Day of a "YYYY-MM-DD HH:MM" as YYYYMMDD, 0 if it does not parse. */
static int day_key(const char *due)
{
    int y, mo, d, h, mi;
    return parse_local(due, &y, &mo, &d, &h, &mi) ? y * 10000 + mo * 100 + d : 0;
}

/* "09:30" or "09:30 – 10:00" */
static void fmt_range(const char *due, const char *end, char *out, size_t len)
{
    int y, mo, d, h, mi, h2, mi2;
    if (!parse_local(due, &y, &mo, &d, &h, &mi)) {
        copy_str(out, len, due ? due : "");
        return;
    }
    if (parse_local(end, &y, &mo, &d, &h2, &mi2)) {
        snprintf(out, len, "%02d:%02d \xE2\x80\x93 %02d:%02d", h, mi, h2, mi2);
    } else {
        snprintf(out, len, "%02d:%02d", h, mi);
    }
}

/* "YYYY-MM-DD HH:MM" (+ end) -> "Today 15:30" / "Tomorrow 09:00 – 10:00" / "3 Oct 18:00". */
static void fmt_due(const char *due, const char *end, char *out, size_t len)
{
    int y, mo, d, h, mi;
    if (!parse_local(due, &y, &mo, &d, &h, &mi)) {
        copy_str(out, len, due ? due : "");
        return;
    }
    char range[24];
    fmt_range(due, end, range, sizeof(range));
    time_t now = time(NULL);
    struct tm today, tomorrow;
    localtime_r(&now, &today);
    time_t next = now + 24 * 3600;
    localtime_r(&next, &tomorrow);
    if (today.tm_year + 1900 == y && today.tm_mon + 1 == mo && today.tm_mday == d) {
        snprintf(out, len, "%s %s", ui_str(STR_TODAY), range);
    } else if (tomorrow.tm_year + 1900 == y && tomorrow.tm_mon + 1 == mo && tomorrow.tm_mday == d) {
        snprintf(out, len, "%s %s", ui_str(STR_TOMORROW), range);
    } else {
        ui_format_date(out, len, -1, d, mo - 1, false);     /* "3 October" / "3. Oktober" */
        strlcat(out, " ", len);
        strlcat(out, range, len);
    }
}

/* The X, top right. Callers bring it to the front once the screen is built, so nothing created later
 * covers its touch area. */
static lv_obj_t *add_close_button(lv_obj_t *scr, lv_event_cb_t cb)
{
    return ui_add_close_x(scr, cb);
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

/* Overdue: an open reminder of today whose time (the end of a range) has passed. Earlier days are
 * not shown at all, later ones cannot be overdue. */
static bool reminder_overdue(const char *due, const char *end, bool done)
{
    int today = today_key();
    if (done || !today || day_key(due) != today) {
        return false;
    }
    return due_passed(end && end[0] ? end : due);
}

/* Shown on the watch: today and later (everything while the clock is not set). */
static bool reminder_visible(const item_row_t *r)
{
    int today = today_key(), day = day_key(r->due);
    return !today || !day || day >= today;
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
        } else if (reminder_visible(&s_rows[i]) && !s_rows[i].done &&
                   !reminder_overdue(s_rows[i].due, s_rows[i].end, false)) {
            reminders++;
        }
    }
    ui_shortcut_counts(notes, reminders);
}
static void show_detail(bool reminder, int number, const char *text, const char *due, const char *end,
                        int notify, bool done, bool from_list, bool alert);
static void show_reminder(const item_row_t *r, bool from_list, bool alert, bool early);
static void rem_render(void);
static void show_note(int number, bool from_list);
static void note_close_mic(void);
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
    if (s_rows[idx].reminder) {
        /* The snapshot has the whole reminder: show it right away. */
        show_reminder(&s_rows[idx], true, false, false);
    } else {
        int number = s_rows[idx].number;
        show_note(number, true);    /* "Loading…" until item_show */
        if (g_ui_cb.on_item_open) {
            g_ui_cb.on_item_open(false, number);
        }
    }
}

/* A plain container: no style, no scrolling; taps and gestures go to the parent. */
static lv_obj_t *plain_box(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(o, LV_OBJ_FLAG_EVENT_BUBBLE | LV_OBJ_FLAG_GESTURE_BUBBLE);
    return o;
}

/* Day header: "Today" / "Tomorrow" / weekday on the left, the date on the right. */
static void add_day_header(lv_obj_t *parent, const char *due, bool first)
{
    int y, mo, d, h, mi;
    if (!parse_local(due, &y, &mo, &d, &h, &mi)) {
        return;
    }
    struct tm t = { .tm_year = y - 1900, .tm_mon = mo - 1, .tm_mday = d, .tm_hour = 12, .tm_isdst = -1 };
    mktime(&t);                                 /* fills tm_wday */
    int key = day_key(due), today = today_key();
    time_t next = time(NULL) + 24 * 3600;
    struct tm tm_tomorrow;
    localtime_r(&next, &tm_tomorrow);
    int tomorrow = (tm_tomorrow.tm_year + 1900) * 10000 + (tm_tomorrow.tm_mon + 1) * 100 + tm_tomorrow.tm_mday;

    lv_obj_t *row = plain_box(parent);
    lv_obj_set_size(row, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_style_pad_hor(row, 4, 0);
    if (!first) {
        lv_obj_set_style_margin_top(row, 10, 0);
    }
    lv_obj_t *l = lv_label_create(row);
    lv_obj_set_style_text_font(l, &buddy_font_28, 0);
    lv_obj_align(l, LV_ALIGN_LEFT_MID, 0, 0);
    lv_obj_t *r = lv_label_create(row);
    lv_obj_set_style_text_opa(r, LV_OPA_70, 0);
    lv_obj_align(r, LV_ALIGN_RIGHT_MID, 0, 0);
    char date[48];
    bool near = today && (key == today || key == tomorrow);
    ui_format_date(date, sizeof(date), t.tm_wday, t.tm_mday, t.tm_mon, near);
    lv_label_set_text(l, !near ? ui_weekday_name(t.tm_wday)
                           : ui_str(key == today ? STR_TODAY : STR_TOMORROW));
    lv_label_set_text(r, date);
}

/* Row colours, in turn: the theme accent, then a few that read well on a dark background. */
static lv_color_t row_color(int pos)
{
    static const uint32_t palette[] = { 0, 0xff8a3d, 0x34c759, 0xaf52de, 0xff4f81, 0x32d2f5 };
    uint32_t c = palette[pos % (int)(sizeof(palette) / sizeof(palette[0]))];
    return c ? lv_color_hex(c) : g_ui_theme.accent;
}

/* "15 min" / "2 h" / "1 h 30 min", units in the UI language */
static void fmt_notice(int min, char *out, size_t len)
{
    const char *h = ui_str(STR_UNIT_H), *m = ui_str(STR_UNIT_MIN);
    if (min >= 60 && min % 60 == 0) {
        snprintf(out, len, "%d %s", min / 60, h);
    } else if (min > 60) {
        snprintf(out, len, "%d %s %d %s", min / 60, h, min % 60, m);
    } else {
        snprintf(out, len, "%d %s", min, m);
    }
}

/* Bell (advance notice), optionally followed by "15 min before". */
static void add_bell(lv_obj_t *parent, int minutes)
{
    lv_obj_t *box = plain_box(parent);
    lv_obj_set_size(box, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(box, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(box, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(box, 6, 0);
    lv_obj_t *bell = lv_label_create(box);
    lv_obj_set_style_text_font(bell, &buddy_font_set, 0);
    lv_obj_set_style_text_color(bell, g_ui_theme.accent, 0);
    lv_label_set_text(bell, SET_ICON_BELL);
    if (minutes > 0) {
        char buf[24];
        fmt_notice(minutes, buf, sizeof(buf));
        lv_obj_t *l = lv_label_create(box);
        char text[48];
        snprintf(text, sizeof(text), ui_str(STR_NOTICE_FMT), buf);     /* "15 min before" */
        lv_label_set_text(l, text);
    }
}

/* Reminder row: [#n badge] [bar] text / time (+ Overdue / Completed) [>]. Flat fills only. */
static void add_reminder_row(lv_obj_t *parent, int idx, int pos)
{
    const item_row_t *r = &s_rows[idx];
    const bool overdue = reminder_overdue(r->due, r->end, r->done);
    const lv_color_t c = r->done ? lv_color_hex(DONE_GREY) : row_color(pos);
    const lv_color_t bg = g_ui_theme.background;

    lv_obj_t *b = lv_button_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_add_style(b, &s_st_rcard, 0);
    lv_obj_add_style(b, &s_st_rcard_pressed, LV_STATE_PRESSED);
    lv_obj_set_size(b, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_style_min_height(b, 76, 0);
    lv_obj_set_flex_flow(b, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(b, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_add_flag(b, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(b, row_event_cb, LV_EVENT_PRESSED, (void *)(intptr_t)idx);
    lv_obj_add_event_cb(b, row_event_cb, LV_EVENT_CLICKED, (void *)(intptr_t)idx);

    lv_obj_t *badge = plain_box(b);
    lv_obj_set_size(badge, BADGE_SIZE, BADGE_SIZE);
    lv_obj_set_style_radius(badge, 12, 0);
    lv_obj_set_style_bg_opa(badge, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(badge, lv_color_mix(c, bg, 90), 0);
    lv_obj_set_style_border_width(badge, 2, 0);
    lv_obj_set_style_border_color(badge, c, 0);
    lv_obj_t *num = lv_label_create(badge);
    lv_obj_set_style_text_color(num, lv_color_white(), 0);
    lv_label_set_text_fmt(num, "#%d", r->number);
    lv_obj_center(num);

    lv_obj_t *bar = plain_box(b);
    lv_obj_set_size(bar, 4, 36);
    lv_obj_set_style_radius(bar, 2, 0);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(bar, c, 0);

    lv_obj_t *col = plain_box(b);
    lv_obj_set_height(col, LV_SIZE_CONTENT);
    lv_obj_set_flex_grow(col, 1);
    lv_obj_set_flex_flow(col, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(col, 2, 0);
    lv_obj_t *t = lv_label_create(col);
    lv_obj_set_width(t, LV_PCT(100));
    lv_label_set_long_mode(t, LV_LABEL_LONG_DOT);
    lv_obj_set_style_max_height(t, ROW_LINES * lv_font_get_line_height(&buddy_font_20), 0);
    lv_obj_set_style_text_opa(t, r->done ? LV_OPA_60 : LV_OPA_COVER, 0);
    lv_label_set_text(t, r->text[0] ? r->text : "…");
    lv_obj_t *meta = plain_box(col);
    lv_obj_set_size(meta, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(meta, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_style_pad_column(meta, 10, 0);
    char range[24];
    fmt_range(r->due, r->end, range, sizeof(range));
    lv_obj_t *d = lv_label_create(meta);
    lv_obj_set_style_text_opa(d, LV_OPA_60, 0);
    lv_label_set_text(d, range);
    if (r->notify_before > 0) {
        add_bell(meta, 0);
    }
    if (r->done) {
        done_tag(lv_label_create(meta));
    } else if (overdue) {
        overdue_tag(lv_label_create(meta));
    }

    lv_obj_t *chev = lv_label_create(b);
    lv_obj_set_style_text_opa(chev, LV_OPA_60, 0);
    lv_label_set_text(chev, ICON_RIGHT);
}

/* Note row: [#n badge] [bar] first line [pin] [>], like a reminder row. */
static void add_note_row(lv_obj_t *parent, int idx, int pos)
{
    const item_row_t *r = &s_rows[idx];
    const lv_color_t c = row_color(pos), bg = g_ui_theme.background;
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_add_style(b, &s_st_rcard, 0);
    lv_obj_add_style(b, &s_st_rcard_pressed, LV_STATE_PRESSED);
    lv_obj_set_size(b, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_style_min_height(b, 76, 0);
    lv_obj_set_flex_flow(b, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(b, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_add_flag(b, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(b, row_event_cb, LV_EVENT_PRESSED, (void *)(intptr_t)idx);
    lv_obj_add_event_cb(b, row_event_cb, LV_EVENT_CLICKED, (void *)(intptr_t)idx);

    lv_obj_t *badge = plain_box(b);
    lv_obj_set_size(badge, BADGE_SIZE, BADGE_SIZE);
    lv_obj_set_style_radius(badge, 12, 0);
    lv_obj_set_style_bg_opa(badge, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(badge, lv_color_mix(c, bg, 90), 0);
    lv_obj_set_style_border_width(badge, 2, 0);
    lv_obj_set_style_border_color(badge, c, 0);
    lv_obj_t *num = lv_label_create(badge);
    lv_obj_set_style_text_color(num, lv_color_white(), 0);
    lv_label_set_text_fmt(num, "#%d", r->number);
    lv_obj_center(num);

    lv_obj_t *bar = plain_box(b);
    lv_obj_set_size(bar, 4, 36);
    lv_obj_set_style_radius(bar, 2, 0);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(bar, c, 0);

    lv_obj_t *col = plain_box(b);
    lv_obj_set_height(col, LV_SIZE_CONTENT);
    lv_obj_set_flex_grow(col, 1);
    lv_obj_set_flex_flow(col, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(col, 2, 0);
    lv_obj_t *t = lv_label_create(col);
    lv_obj_set_width(t, LV_PCT(100));
    lv_label_set_long_mode(t, LV_LABEL_LONG_DOT);
    lv_obj_set_style_max_height(t, ROW_LINES * lv_font_get_line_height(&buddy_font_20), 0);
    lv_label_set_text(t, r->text[0] ? r->text : "…");
    if (r->location[0] || r->pinned) {
        lv_obj_t *meta = plain_box(col);
        lv_obj_set_size(meta, LV_PCT(100), LV_SIZE_CONTENT);
        lv_obj_set_flex_flow(meta, LV_FLEX_FLOW_ROW);
        lv_obj_set_flex_align(meta, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_set_style_pad_column(meta, 10, 0);
        if (r->pinned) {
            lv_obj_t *pin = lv_label_create(meta);
            lv_obj_set_style_text_font(pin, &buddy_font_set, 0);
            lv_obj_set_style_text_color(pin, g_ui_theme.accent, 0);
            lv_label_set_text(pin, SET_ICON_PIN_NOTE);
        }
        if (r->location[0]) {   /* notes: the line under the title */
            lv_obj_t *sub = lv_label_create(meta);
            lv_obj_set_flex_grow(sub, 1);
            lv_label_set_long_mode(sub, LV_LABEL_LONG_DOT);
            lv_obj_set_style_text_opa(sub, LV_OPA_60, 0);
            lv_label_set_text(sub, r->location);
        }
    }
    lv_obj_t *chev = lv_label_create(b);
    lv_obj_set_style_text_opa(chev, LV_OPA_60, 0);
    lv_label_set_text(chev, ICON_RIGHT);
}

/* Reminders by day, then completed last, then time, then number. */
static bool reminder_before(const item_row_t *a, const item_row_t *b)
{
    int da = day_key(a->due), db = day_key(b->due);
    if (da != db) {
        return da < db;
    }
    if (a->done != b->done) {
        return !a->done;
    }
    int c = strcmp(a->due, b->due);
    return c ? c < 0 : a->number < b->number;
}

/* Row card colours from the theme: shared by the notes and reminders lists. */
static void row_card_colors(void)
{
    const lv_color_t a = g_ui_theme.accent, bg = g_ui_theme.background;
    lv_style_set_bg_color(&s_st_rcard, lv_color_mix(a, bg, 22));
    lv_style_set_border_color(&s_st_rcard, lv_color_mix(a, bg, 70));
    lv_style_set_bg_color(&s_st_rcard_pressed, lv_color_mix(a, bg, 60));
}

/* Day headers + reminder rows. Returns how many rows were added. */
static int build_reminder_rows(lv_obj_t *body)
{
    const lv_color_t a = g_ui_theme.accent, bg = g_ui_theme.background;
    row_card_colors();

    int n = 0;
    for (int i = 0; s_have_snapshot && i < s_row_count; i++) {
        if (!s_rows[i].reminder || !reminder_visible(&s_rows[i])) {
            continue;
        }
        int j = n++;
        while (j > 0 && reminder_before(&s_rows[i], &s_rows[s_order[j - 1]])) {
            s_order[j] = s_order[j - 1];
            j--;
        }
        s_order[j] = i;
    }
    int last_day = -1, today = today_key();
    lv_obj_t *parent = body;
    for (int k = 0; k < n; k++) {
        const item_row_t *r = &s_rows[s_order[k]];
        int day = day_key(r->due);
        if (day != last_day && today) {
            /* Today's reminders always sit together in their own accent frame. */
            parent = body;
            if (day == today) {
                parent = plain_box(body);
                lv_obj_set_size(parent, LV_PCT(100), LV_SIZE_CONTENT);
                lv_obj_set_flex_flow(parent, LV_FLEX_FLOW_COLUMN);
                lv_obj_set_style_pad_all(parent, 8, 0);
                lv_obj_set_style_pad_row(parent, 10, 0);
                lv_obj_set_style_radius(parent, 24, 0);
                lv_obj_set_style_bg_opa(parent, LV_OPA_COVER, 0);
                lv_obj_set_style_bg_color(parent, lv_color_mix(a, bg, 12), 0);
                lv_obj_set_style_border_width(parent, 2, 0);
                lv_obj_set_style_border_color(parent, a, 0);
            }
            add_day_header(parent, r->due, k == 0 || parent != body);
        }
        last_day = day;
        add_reminder_row(parent, s_order[k], k);
    }
    return n;
}

static void rebuild_list(list_screen_t *ls)
{
    lv_obj_clean(ls->body);
    lv_label_set_text(ls->title, ui_str(ls->reminder ? STR_REMINDERS : STR_NOTES));
    int count = 0;
    if (ls->reminder) {
        count = build_reminder_rows(ls->body);
    } else {
        row_card_colors();
        for (int i = 0; s_have_snapshot && i < s_row_count; i++) {
            if (!s_rows[i].reminder) {
                add_note_row(ls->body, i, count++);     /* pinned first (server order) */
            }
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
    lv_obj_t *x = add_close_button(ls->scr, list_close_cb);

    ls->title = lv_label_create(ls->scr);
    lv_obj_set_width(ls->title, CONTENT_W);
    lv_obj_set_style_text_font(ls->title, &buddy_font_28, 0);
    lv_obj_set_style_pad_top(ls->title, 8, 0);
    lv_obj_set_style_pad_bottom(ls->title, 6, 0);
    lv_obj_add_flag(ls->title, LV_OBJ_FLAG_GESTURE_BUBBLE);

    ls->body = lv_obj_create(ls->scr);
    lv_obj_remove_style_all(ls->body);
    lv_obj_remove_flag(ls->body, LV_OBJ_FLAG_CLICKABLE);   /* only its rows take taps */
    lv_obj_set_size(ls->body, LIST_W, LV_SIZE_CONTENT);
    /* No big title (the rows say what they are); start below the X. */
    lv_obj_add_flag(ls->title, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_pad_top(ls->body, 30, 0);
    if (!s_st_rcard.prop_cnt) {     /* once, for both lists */
        lv_style_init(&s_st_rcard);
        lv_style_set_radius(&s_st_rcard, 18);
        lv_style_set_bg_opa(&s_st_rcard, LV_OPA_COVER);
        lv_style_set_border_width(&s_st_rcard, 1);
        lv_style_set_pad_left(&s_st_rcard, 12);
        lv_style_set_pad_right(&s_st_rcard, 12);
        lv_style_set_pad_top(&s_st_rcard, 10);
        lv_style_set_pad_bottom(&s_st_rcard, 10);
        lv_style_set_pad_column(&s_st_rcard, 12);
        lv_style_init(&s_st_rcard_pressed);
    }
    lv_obj_remove_flag(ls->body, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(ls->body, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_flex_flow(ls->body, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(ls->body, 10, 0);
    lv_obj_move_foreground(x);
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
    if (s_del_btn && s_del_icon) {
        lv_obj_set_style_bg_color(s_del_btn, g_ui_theme.accent, 0);
        note_live_reset();
    } else if (s_del_btn) {
        if (s_del_outlined) {
            lv_obj_set_style_bg_opa(s_del_btn, LV_OPA_TRANSP, 0);
        } else {
            lv_obj_set_style_bg_color(s_del_btn, lv_color_hex(PILL_BG), 0);
        }
        lv_label_set_text(s_del_btn_lbl, ui_str(STR_DELETE));
    }
}

static void close_detail(void)
{
    end_alert();
    note_close_mic();
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
        lv_obj_set_style_bg_color(s_del_btn, lv_color_hex(DANGER), 0);
        lv_obj_set_style_bg_opa(s_del_btn, LV_OPA_COVER, 0);
        if (s_del_icon) {
            note_live_set(ui_str(STR_DELETE_CONFIRM), lv_color_hex(DANGER));
        } else if (s_del_outlined) {
            /* the reminder screen's narrow button: "Delete?" */
            lv_label_set_text_fmt(s_del_btn_lbl, "%s?", ui_str(STR_DELETE));
        } else {
            lv_label_set_text(s_del_btn_lbl, ui_str(STR_DELETE_CONFIRM));
        }
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
    lv_obj_t *x = add_close_button(s_det_scr, det_close_cb);

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
    lv_obj_move_foreground(x);
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
        char buf[48];
        fmt_due(s_det_due_str, s_det_end_str, buf, sizeof(buf));
        lv_obj_t *d = lv_label_create(s_det_due);
        lv_label_set_text_fmt(d, ICON_CALENDAR " %s", buf);
        if (s_det_notify > 0) {
            add_bell(s_det_due, s_det_notify);
        }
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
static void show_detail(bool reminder, int number, const char *text, const char *due, const char *end,
                        int notify, bool done, bool from_list, bool alert)
{
    const bool overdue = reminder && reminder_overdue(due, end, done);
    disarm_delete();
    s_del_btn = s_det_del;
    s_del_btn_lbl = s_det_del_lbl;
    s_del_outlined = false;
    s_del_icon = false;
    s_det_reminder = reminder;
    s_det_number = number;
    s_det_from_list = from_list;
    s_det_done = reminder && done;
    copy_str(s_det_due_str, sizeof(s_det_due_str), reminder ? due : NULL);
    copy_str(s_det_end_str, sizeof(s_det_end_str), reminder ? end : NULL);
    s_det_notify = reminder ? notify : 0;

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
/* An item / snapshot row's reminder fields into a row. */
static void parse_reminder(const cJSON *it, item_row_t *r)
{
    r->reminder = true;
    r->number = jint(it, "number");
    r->done = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(it, "done"));
    copy_str(r->text, sizeof(r->text), jstr(it, "text"));
    copy_str(r->due, sizeof(r->due), jstr(it, "due_local"));
    copy_str(r->end, sizeof(r->end), jstr(it, "end_local"));
    r->notify_before = jint(it, "notify_before");
    copy_str(r->location, sizeof(r->location), jstr(it, "location"));
    copy_str(r->participants, sizeof(r->participants), jstr(it, "participants"));
}

static void show_item_json(const cJSON *item, bool alert, bool early)
{
    const char *kind = jstr(item, "kind");
    bool reminder = alert || (kind && strcmp(kind, "reminder") == 0);
    int number = jint(item, "number");
    const char *text = jstr(item, "text");
    bool done = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(item, "done"));
    if (reminder && s_rem_row) {
        /* the reminder on screen refreshed (e.g. changed by voice): "back" still goes where it did */
        bool again = !alert && lv_screen_active() == s_rem_scr && s_det_reminder && s_det_from_list &&
                     s_det_number == number;
        item_row_t *r = heap_caps_calloc(1, sizeof(item_row_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (r) {
            parse_reminder(item, r);
            show_reminder(r, again, alert, early);
            free(r);
            return;
        }
    }
    if (!reminder && s_note_text) {
        note_show_json(item, number);
        return;
    }
    /* Opened from the list and still waiting for this note: keep "back" going to the list. */
    bool from_list = !alert && lv_screen_active() == s_det_scr && s_det_from_list &&
                     s_det_reminder == reminder && s_det_number == number;
    show_detail(reminder, number, text ? text : "", jstr(item, "due_local"), jstr(item, "end_local"),
                jint(item, "notify_before"), done, from_list, alert);
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
        }
    }
    update_counts();
    if (lv_screen_active() == s_lists[1].scr) {
        lv_async_call(rebuild_reminders_async, NULL);   /* not from inside a button's own event */
    }
    if (s_det_reminder && s_det_number == number && s_rem_row) {
        s_det_done = done;
        s_rem_row->done = done;
        rem_render();
    }
    ui_note_activity();
}

/* ------------------------------------------------------------------------- */
/* Reminder detail                                                            */
/* ------------------------------------------------------------------------- */

static void rem_btn_done_cb(lv_event_t *e)
{
    set_done(s_det_number, !s_det_done);
}

/* A labelled row of the card: [icon circle] label / value. A 1 px line above it. */
static void add_info_row(const char *icon, ui_str_t label, const char *value)
{
    const lv_color_t a = g_ui_theme.accent, bg = g_ui_theme.background;
    lv_obj_t *row = plain_box(s_rem_rows);
    lv_obj_set_size(row, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_ver(row, 12, 0);
    lv_obj_set_style_pad_column(row, 14, 0);
    lv_obj_set_style_border_side(row, LV_BORDER_SIDE_TOP, 0);
    lv_obj_set_style_border_width(row, 1, 0);
    lv_obj_set_style_border_color(row, lv_color_mix(a, bg, 70), 0);

    lv_obj_t *circle = plain_box(row);
    lv_obj_set_size(circle, 46, 46);
    lv_obj_set_style_radius(circle, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(circle, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(circle, lv_color_mix(a, bg, 150), 0);   /* like the calendar tile */
    lv_obj_set_style_border_width(circle, 2, 0);
    lv_obj_set_style_border_color(circle, lv_color_mix(lv_color_white(), a, 90), 0);
    lv_obj_t *i = lv_label_create(circle);
    lv_obj_set_style_text_font(i, &buddy_font_set, 0);
    lv_obj_set_style_text_color(i, ui_on_color(lv_color_mix(a, bg, 150)), 0);
    lv_label_set_text(i, icon);
    lv_obj_center(i);

    lv_obj_t *col = plain_box(row);
    lv_obj_set_height(col, LV_SIZE_CONTENT);
    lv_obj_set_flex_grow(col, 1);
    lv_obj_set_flex_flow(col, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(col, 2, 0);
    lv_obj_t *l = lv_label_create(col);
    lv_obj_set_style_text_color(l, lv_color_mix(lv_color_white(), a, 110), 0);
    lv_label_set_text(l, ui_str(label));
    lv_obj_t *v = lv_label_create(col);
    lv_obj_set_width(v, LV_PCT(100));
    lv_label_set_long_mode(v, LV_LABEL_LONG_WRAP);
    lv_label_set_text(v, value);
}

/* Fill the reminder screen from s_rem_row. Caller holds the lock. */
static void rem_render(void)
{
    if (!s_rem_scr || !s_rem_row) {
        return;
    }
    const item_row_t *r = s_rem_row;
    const lv_color_t a = g_ui_theme.accent, bg = g_ui_theme.background;
    char buf[64];

    /* title: "Reminder #3"; long translations use the smaller font */
    snprintf(buf, sizeof(buf), "%s #%d", ui_str(STR_REMINDER), r->number);
    lv_obj_set_style_text_font(s_rem_title, &buddy_font_28, 0);
    lv_point_t size;
    lv_text_get_size(&size, buf, &buddy_font_28, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    if (size.x > REM_TITLE_MAX_W) {
        lv_obj_set_style_text_font(s_rem_title, &buddy_font_20, 0);
    }
    lv_label_set_text(s_rem_title, buf);
    lv_obj_set_style_text_color(s_rem_title, a, 0);

    lv_obj_set_style_bg_color(s_rem_card, lv_color_mix(a, bg, 22), 0);
    lv_obj_set_style_border_color(s_rem_card, lv_color_mix(a, bg, 70), 0);
    lv_obj_set_style_bg_color(s_rem_tile, lv_color_mix(a, bg, 150), 0);
    lv_obj_set_style_text_color(s_rem_cal, ui_on_color(lv_color_mix(a, bg, 150)), 0);
    lv_obj_set_style_border_color(s_rem_tile, lv_color_mix(lv_color_white(), a, 90), 0);

    /* date + time */
    int y, mo, d, h, mi;
    if (parse_local(r->due, &y, &mo, &d, &h, &mi)) {
        struct tm t = { .tm_year = y - 1900, .tm_mon = mo - 1, .tm_mday = d, .tm_hour = 12, .tm_isdst = -1 };
        mktime(&t);
        ui_format_date(buf, sizeof(buf), t.tm_wday, d, mo - 1, true);
        lv_label_set_text(s_rem_date, buf);
    } else {
        lv_label_set_text(s_rem_date, "");
    }
    fmt_range(r->due, r->end, buf, sizeof(buf));
    lv_label_set_text(s_rem_time, buf);

    /* status: Completed / Overdue / "Starts in 10 min" (advance alert) */
    lv_obj_remove_flag(s_rem_status, LV_OBJ_FLAG_HIDDEN);
    if (r->done) {
        done_tag(s_rem_status);
    } else if (reminder_overdue(r->due, r->end, false)) {
        overdue_tag(s_rem_status);
    } else if (s_rem_early && parse_local(r->due, &y, &mo, &d, &h, &mi)) {
        struct tm t = { .tm_year = y - 1900, .tm_mon = mo - 1, .tm_mday = d, .tm_hour = h, .tm_min = mi,
                        .tm_isdst = -1 };
        int mins = (int)((mktime(&t) - time(NULL) + 59) / 60);
        if (mins > 0) {
            char left[32];
            fmt_notice(mins, left, sizeof(left));
            snprintf(buf, sizeof(buf), ui_str(STR_STARTS_IN_FMT), left);
            lv_obj_set_style_text_color(s_rem_status, a, 0);
            lv_label_set_text(s_rem_status, buf);
        } else {
            lv_obj_add_flag(s_rem_status, LV_OBJ_FLAG_HIDDEN);
        }
    } else {
        lv_obj_add_flag(s_rem_status, LV_OBJ_FLAG_HIDDEN);
    }

    /* rows: Location, Description (always), Reminder (advance notice), Participants */
    lv_obj_clean(s_rem_rows);
    if (r->location[0]) {
        add_info_row(SET_ICON_PIN, STR_LOCATION, r->location);
    }
    add_info_row(SET_ICON_DOC, STR_DESCRIPTION, r->text[0] ? r->text : "…");
    if (r->notify_before > 0) {
        char n[32];
        fmt_notice(r->notify_before, n, sizeof(n));
        snprintf(buf, sizeof(buf), ui_str(STR_NOTICE_FMT), n);
        add_info_row(SET_ICON_BELL, STR_NOTICE_LABEL, buf);
    }
    if (r->participants[0]) {
        add_info_row(SET_ICON_USERS, STR_PARTICIPANTS, r->participants);
    }

    /* bar: Delete | Mic | Completed (green with a white check when done) */
    const lv_color_t rim = lv_color_mix(lv_color_white(), a, 90), on_a = ui_on_color(a);
    lv_obj_set_style_border_color(s_rem_del, rim, 0);
    lv_obj_set_style_border_color(s_rem_mic, rim, 0);
    lv_obj_set_style_border_color(s_rem_check, rim, 0);
    if (!s_del_armed) {
        lv_obj_set_style_bg_color(s_rem_del, a, 0);
    }
    lv_obj_set_style_text_color(s_rem_del_icon, s_del_armed ? lv_color_white() : on_a, 0);
    lv_obj_set_style_bg_color(s_rem_check, r->done ? lv_color_hex(DONE_GREEN) : a, 0);
    lv_obj_set_style_text_color(s_rem_check_icon, r->done ? lv_color_white() : on_a, 0);
    lv_obj_set_style_border_color(s_rem_ring, lv_color_hex(DANGER), 0);
    if (s_mic == s_rem_mic) {
        note_mic_visuals();
    }
}

static void build_reminder_screen(void)
{
    s_rem_row = heap_caps_calloc(1, sizeof(item_row_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_rem_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_rem_scr, ui_style_screen(), 0);
    lv_obj_remove_flag(s_rem_scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_rem_scr, det_gesture_cb, LV_EVENT_GESTURE, NULL);

    /* header: battery | title | X (Wi-Fi only on the watchface and quick settings) */
    lv_obj_t *hdr = plain_box(s_rem_scr);
    lv_obj_set_size(hdr, 350, REM_HDR_H);
    lv_obj_align(hdr, LV_ALIGN_TOP_MID, 0, REM_HDR_Y);
    lv_obj_add_flag(hdr, LV_OBJ_FLAG_OVERFLOW_VISIBLE);
    lv_obj_t *status = plain_box(hdr);
    lv_obj_set_size(status, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(status, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(status, 6, 0);
    lv_obj_align(status, LV_ALIGN_LEFT_MID, REM_STATUS_X, 0);
    s_rem_batt = lv_label_create(status);
    lv_label_set_text(s_rem_batt, "");
    s_rem_title = lv_label_create(hdr);
    lv_obj_center(s_rem_title);

    /* body: one card reaching the buttons; scrolls when taller */
    s_rem_body = lv_obj_create(s_rem_scr);
    lv_obj_remove_style_all(s_rem_body);
    lv_obj_set_size(s_rem_body, REM_W, NOTE_BODY_H);
    lv_obj_align(s_rem_body, LV_ALIGN_TOP_MID, 0, REM_BODY_Y);
    lv_obj_set_scroll_dir(s_rem_body, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(s_rem_body, LV_SCROLLBAR_MODE_OFF);
    /* Clickable on purpose: LVGL scrolls the pressed object or its parents, and the card inside is not
     * clickable - without this a drag reached the (fixed) screen and nothing scrolled. */
    lv_obj_add_flag(s_rem_body, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLL_ELASTIC);
    lv_obj_add_flag(s_rem_body, LV_OBJ_FLAG_GESTURE_BUBBLE);

    s_rem_card = plain_box(s_rem_body);
    lv_obj_set_size(s_rem_card, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_style_min_height(s_rem_card, NOTE_BODY_H, 0);
    lv_obj_set_style_radius(s_rem_card, 22, 0);
    lv_obj_set_style_bg_opa(s_rem_card, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_rem_card, 1, 0);
    lv_obj_set_style_pad_hor(s_rem_card, 16, 0);
    lv_obj_set_style_pad_top(s_rem_card, 16, 0);
    lv_obj_set_style_pad_bottom(s_rem_card, 6, 0);
    lv_obj_set_flex_flow(s_rem_card, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_rem_card, 14, 0);

    lv_obj_t *top = plain_box(s_rem_card);
    lv_obj_set_size(top, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(top, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(top, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(top, 16, 0);
    s_rem_tile = plain_box(top);
    lv_obj_set_size(s_rem_tile, 72, 72);
    lv_obj_set_style_radius(s_rem_tile, 20, 0);
    lv_obj_set_style_bg_opa(s_rem_tile, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_rem_tile, 2, 0);
    lv_obj_t *cal = s_rem_cal = lv_label_create(s_rem_tile);
    lv_obj_set_style_text_font(cal, &buddy_font_set_lg, 0);
    lv_obj_set_style_text_color(cal, lv_color_white(), 0);
    lv_label_set_text(cal, SET_ICON_CALENDAR);
    lv_obj_center(cal);
    lv_obj_t *col = plain_box(top);
    lv_obj_set_height(col, LV_SIZE_CONTENT);
    lv_obj_set_flex_grow(col, 1);
    lv_obj_set_flex_flow(col, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(col, 2, 0);
    s_rem_date = lv_label_create(col);
    lv_obj_set_width(s_rem_date, LV_PCT(100));
    lv_label_set_long_mode(s_rem_date, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_opa(s_rem_date, LV_OPA_80, 0);
    s_rem_time = lv_label_create(col);
    lv_obj_set_style_text_font(s_rem_time, &buddy_font_28, 0);
    s_rem_status = lv_label_create(col);

    s_rem_rows = plain_box(s_rem_card);
    lv_obj_set_size(s_rem_rows, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(s_rem_rows, LV_FLEX_FLOW_COLUMN);

    s_rem_live = lv_label_create(s_rem_card);
    lv_obj_set_width(s_rem_live, LV_PCT(100));
    lv_label_set_long_mode(s_rem_live, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_pad_hor(s_rem_live, 6, 0);
    lv_obj_set_style_pad_bottom(s_rem_live, 8, 0);
    lv_obj_add_flag(s_rem_live, LV_OBJ_FLAG_HIDDEN);

    /* bar: Delete | Mic | Completed - as on the note screen */
    item_bar(s_rem_scr, rem_mic_cb, rem_btn_done_cb, ICON_OK, &buddy_font_28,
             &s_rem_del, &s_rem_del_icon, &s_rem_mic, &s_rem_mic_icon, &s_rem_stop, &s_rem_ring,
             &s_rem_check, &s_rem_check_icon);

    lv_obj_move_foreground(hdr);
    lv_obj_move_foreground(ui_add_close_x(s_rem_scr, det_close_cb));
}

/* Show a reminder full screen (from the list, item_show, or its alert). */
static void show_reminder(const item_row_t *r, bool from_list, bool alert, bool early)
{
    if (!s_rem_row) {
        return;
    }
    if (s_note_mic_open && !(s_mic_reminder && s_mic_number == r->number)) {
        note_close_mic();       /* another item's edit mic */
    }
    disarm_delete();
    if (r != s_rem_row) {
        memcpy(s_rem_row, r, sizeof(*s_rem_row));
    }
    s_del_btn = s_rem_del;
    s_del_btn_lbl = NULL;
    s_del_outlined = false;
    s_del_icon = true;
    s_mic = s_rem_mic;
    s_mic_icon = s_rem_mic_icon;
    s_mic_stop = s_rem_stop;
    s_mic_ring = s_rem_ring;
    s_live = s_rem_live;
    s_rem_early = early;
    s_det_reminder = true;
    s_det_number = r->number;
    s_det_from_list = from_list;
    s_det_done = r->done;
    copy_str(s_det_due_str, sizeof(s_det_due_str), r->due);
    copy_str(s_det_end_str, sizeof(s_det_end_str), r->end);
    s_det_notify = r->notify_before;
    note_live_reset();
    rem_render();
    if (alert) {
        end_alert();
        s_det_alert = true;
        ui_hold_awake(true);
        s_alert_timer = lv_timer_create(alert_timer_cb, ALERT_HOLD_MS, NULL);
        lv_timer_set_repeat_count(s_alert_timer, 1);
    }
    lv_obj_scroll_to_y(s_rem_body, 0, LV_ANIM_OFF);
    ui_note_activity();
    ui_load_screen(s_rem_scr, true);
}

void ui_items_status(const char *batt, lv_color_t batt_color)
{
    if (!s_rem_scr) {
        return;
    }
    lv_label_set_text(s_rem_batt, batt);
    lv_obj_set_style_text_color(s_rem_batt, batt_color, 0);
    if (s_note_scr) {
        lv_label_set_text(s_note_batt, batt);
        lv_obj_set_style_text_color(s_note_batt, batt_color, 0);
    }
}

/* ------------------------------------------------------------------------- */
/* Note screen                                                                */
/* ------------------------------------------------------------------------- */

static void note_live_set(const char *text, lv_color_t color)
{
    if (!s_live) {
        return;
    }
    lv_obj_set_style_text_color(s_live, color, 0);
    lv_label_set_text(s_live, text ? text : "");
    lv_obj_set_flag(s_live, LV_OBJ_FLAG_HIDDEN, !text || !text[0]);
}

/* What the line under the note / reminder says when nothing else is going on. */
static void note_live_reset(void)
{
    if (s_note_mic_open) {
        note_live_set(ui_str(s_det_reminder ? STR_REMINDER_HELP : STR_NOTE_HELP),
                      lv_color_mix(lv_color_white(), g_ui_theme.accent, 110));
    } else {
        note_live_set(NULL, lv_color_white());
    }
}

static void note_flash_cb(lv_timer_t *t)
{
    s_note_flash_timer = NULL;
    s_note_flash = -1;
    note_render();
}

/* Mic: accent with a mic icon when closed; red with a white square and a pulsing ring when open. */
static void note_ring_anim_cb(void *var, int32_t v)
{
    lv_obj_t *ring = var;
    int32_t size = NOTE_MIC + 4 + v * 26 / 1000;
    lv_obj_set_size(ring, size, size);
    lv_obj_align_to(ring, s_mic, LV_ALIGN_CENTER, 0, 0);
    lv_obj_set_style_border_opa(ring, (lv_opa_t)(LV_OPA_80 - v * LV_OPA_70 / 1000), 0);
}

static void note_mic_visuals(void)
{
    const lv_color_t a = g_ui_theme.accent;
    if (!s_mic) {
        return;
    }
    lv_anim_delete(s_note_ring, note_ring_anim_cb);
    lv_anim_delete(s_rem_ring, note_ring_anim_cb);
    if (s_note_mic_open) {
        lv_obj_set_style_bg_color(s_mic, lv_color_hex(DANGER), 0);
        lv_obj_add_flag(s_mic_icon, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_mic_stop, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_mic_ring, LV_OBJ_FLAG_HIDDEN);
        lv_anim_t an;
        lv_anim_init(&an);
        lv_anim_set_var(&an, s_mic_ring);
        lv_anim_set_exec_cb(&an, note_ring_anim_cb);
        lv_anim_set_values(&an, 0, 1000);
        lv_anim_set_duration(&an, 1100);
        lv_anim_set_repeat_count(&an, LV_ANIM_REPEAT_INFINITE);
        lv_anim_start(&an);
    } else {
        lv_obj_set_style_bg_color(s_mic, a, 0);
        lv_obj_set_style_text_color(s_mic_icon, ui_on_color(a), 0);   /* dark mic on a white accent */
        lv_obj_remove_flag(s_mic_icon, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_mic_stop, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_mic_ring, LV_OBJ_FLAG_HIDDEN);
    }
}

/* One numbered line of the note. */
static lv_obj_t *note_add_line(int n, const char *text, size_t len)
{
    const lv_color_t a = g_ui_theme.accent, bg = g_ui_theme.background;
    lv_obj_t *row = plain_box(s_note_lines);
    lv_obj_set_size(row, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_ver(row, 6, 0);
    lv_obj_set_style_pad_hor(row, 6, 0);
    lv_obj_set_style_pad_column(row, 8, 0);
    lv_obj_set_style_radius(row, 10, 0);
    if (n == s_note_flash) {
        lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(row, lv_color_mix(a, bg, 90), 0);
    }
    lv_obj_t *num = lv_label_create(row);
    lv_obj_set_width(num, 38);
    lv_obj_set_style_text_color(num, a, 0);
    lv_label_set_text_fmt(num, "%d.", n);
    lv_obj_t *t = lv_label_create(row);
    lv_obj_set_flex_grow(t, 1);
    lv_label_set_long_mode(t, LV_LABEL_LONG_WRAP);
    char buf[512];
    copy_str(buf, len + 1 < sizeof(buf) ? len + 1 : sizeof(buf), text);
    lv_label_set_text(t, buf);
    return row;
}

/* Skip a list marker someone typed ("- ", "• ", "3. ") so numbering is ours. */
static const char *skip_marker(const char *p, const char *end)
{
    const char *q = p;
    while (q < end && (*q == ' ' || *q == '\t')) {
        q++;
    }
    if (q < end && (*q == '-' || *q == '*')) {
        q++;
    } else if (end - q >= 3 && (unsigned char)q[0] == 0xE2 && (unsigned char)q[1] == 0x80 &&
               ((unsigned char)q[2] == 0xA2 || (unsigned char)q[2] == 0x93 || (unsigned char)q[2] == 0x94)) {
        q += 3;     /* • – — */
    } else {
        const char *d = q;
        while (d < end && *d >= '0' && *d <= '9' && d - q < 3) {
            d++;
        }
        if (d == q || d >= end || (*d != '.' && *d != ')')) {
            return p;
        }
        q = d + 1;
    }
    if (q < end && *q != ' ') {
        return p;
    }
    while (q < end && *q == ' ') {
        q++;
    }
    return q;
}

static void note_render(void)
{
    if (!s_note_scr) {
        return;
    }
    const lv_color_t a = g_ui_theme.accent, bg = g_ui_theme.background;
    lv_label_set_text_fmt(s_note_title, "%s #%d", ui_str(STR_NOTE), s_note_number);
    lv_obj_set_style_text_color(s_note_title, a, 0);
    lv_obj_set_style_bg_color(s_note_card, lv_color_mix(a, bg, 22), 0);
    lv_obj_set_style_border_color(s_note_card, lv_color_mix(a, bg, 70), 0);
    const lv_color_t rim = lv_color_mix(lv_color_white(), a, 90);
    lv_obj_set_style_border_color(s_note_del, rim, 0);
    lv_obj_set_style_border_color(s_note_pin, rim, 0);
    lv_obj_set_style_border_color(s_note_mic, rim, 0);
    if (!s_del_armed) {
        lv_obj_set_style_bg_color(s_note_del, a, 0);
    }
    const lv_color_t on_a = ui_on_color(a);   /* dark on a white ("mono") accent */
    lv_obj_set_style_text_color(s_note_del_icon, s_del_armed ? lv_color_white() : on_a, 0);
    lv_obj_set_style_bg_color(s_note_pin, s_note_pinned ? on_a : a, 0);
    lv_obj_set_style_text_color(s_note_pin_icon, s_note_pinned ? a : on_a, 0);
    lv_obj_set_style_border_color(s_note_ring, lv_color_hex(DANGER), 0);
    if (s_mic == s_note_mic) {
        note_mic_visuals();
    }

    lv_obj_set_style_bg_color(s_note_tile, lv_color_mix(a, bg, 150), 0);
    lv_obj_set_style_text_color(s_note_doc, ui_on_color(lv_color_mix(a, bg, 150)), 0);
    lv_obj_set_style_border_color(s_note_tile, lv_color_mix(lv_color_white(), a, 90), 0);
    lv_obj_set_style_bg_color(s_note_divider, lv_color_mix(a, bg, 120), 0);
    lv_obj_set_style_bg_color(s_note_head, lv_color_mix(a, bg, 90), 0);
    lv_obj_set_style_bg_opa(s_note_head, s_note_flash == 0 ? LV_OPA_COVER : LV_OPA_TRANSP, 0);

    lv_obj_clean(s_note_lines);
    lv_obj_t *flash_row = s_note_flash == 0 ? s_note_head : NULL;
    bool has_title = false;
    if (s_note_loading) {
        lv_obj_set_style_text_opa(s_note_head, LV_OPA_50, 0);
        lv_label_set_text(s_note_head, ui_str(STR_LOADING));
    } else {
        int n = -1;     /* -1: the next line is the title */
        const char *p = s_note_text;
        while (*p && n < NOTE_MAX_LINES) {
            const char *nl = strchr(p, '\n');
            const char *end = nl ? nl : p + strlen(p);
            const char *start = skip_marker(p, end);
            const char *stop = end;
            while (stop > start && (stop[-1] == ' ' || stop[-1] == '\r')) {
                stop--;
            }
            if (stop > start && n < 0) {
                char title[512];
                size_t len = (size_t)(stop - start);
                copy_str(title, len + 1 < sizeof(title) ? len + 1 : sizeof(title), start);
                lv_obj_set_style_text_opa(s_note_head, LV_OPA_COVER, 0);
                lv_label_set_text(s_note_head, title);
                has_title = true;
                n = 0;
            } else if (stop > start) {
                lv_obj_t *row = note_add_line(++n, start, (size_t)(stop - start));
                if (n == s_note_flash) {
                    flash_row = row;
                }
            }
            p = nl ? nl + 1 : end;
        }
        if (!has_title) {   /* empty note: how to fill it, where the title goes */
            lv_obj_set_style_text_opa(s_note_head, LV_OPA_70, 0);
            lv_label_set_text(s_note_head, ui_str(STR_NOTE_EMPTY));
        }
    }
    lv_obj_set_flag(s_note_divider, LV_OBJ_FLAG_HIDDEN, !has_title);
    if (flash_row) {
        lv_obj_update_layout(s_note_body);
        lv_obj_scroll_to_view_recursive(flash_row, LV_ANIM_ON);
    }
}

static void note_mic_cb(lv_event_t *e)
{
    if (s_note_loading || !g_ui_cb.on_note_session) {
        return;
    }
    disarm_delete();
    s_mic_reminder = false;
    s_mic_number = s_note_number;
    g_ui_cb.on_note_session(!s_note_mic_open, s_note_number);  /* ui_note_session() follows */
}

/* The reminder screen's mic: edit only this reminder, as the note mic does for a note. */
static void rem_mic_cb(lv_event_t *e)
{
    if (!g_ui_cb.on_reminder_session) {
        return;
    }
    disarm_delete();
    s_mic_reminder = true;
    s_mic_number = s_det_number;
    g_ui_cb.on_reminder_session(!s_note_mic_open, s_det_number);  /* ui_note_session() follows */
}

static void note_pin_cb(lv_event_t *e)
{
    if (s_note_loading) {
        return;
    }
    s_note_pinned = !s_note_pinned;
    for (int i = 0; i < s_row_count; i++) {
        if (!s_rows[i].reminder && s_rows[i].number == s_note_number) {
            s_rows[i].pinned = s_note_pinned;
        }
    }
    if (g_ui_cb.on_item_pin) {
        g_ui_cb.on_item_pin(s_note_number, s_note_pinned);
    }
    note_render();
}

/* Leaving the note / reminder screen (or deleting it) closes the edit mic; the sentence in progress
 * is kept. */
static void note_close_mic(void)
{
    if (!s_note_mic_open) {
        return;
    }
    if (s_mic_reminder && g_ui_cb.on_reminder_session) {
        g_ui_cb.on_reminder_session(false, s_mic_number);
    } else if (!s_mic_reminder && g_ui_cb.on_note_session) {
        g_ui_cb.on_note_session(false, s_mic_number);
    }
}

static lv_obj_t *note_round_button(lv_obj_t *parent, int32_t size, lv_event_cb_t cb, const char *icon,
                                   const lv_font_t *font, lv_obj_t **icon_out)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_set_size(b, size, size);
    lv_obj_set_style_radius(b, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(b, 2, 0);
    lv_obj_set_style_transform_scale(b, 235, LV_STATE_PRESSED);
    lv_obj_set_style_transform_pivot_x(b, size / 2, 0);
    lv_obj_set_style_transform_pivot_y(b, size / 2, 0);
    lv_obj_set_ext_click_area(b, 10);
    lv_obj_add_flag(b, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *i = lv_label_create(b);
    lv_obj_set_style_text_font(i, font, 0);
    lv_obj_set_style_text_color(i, lv_color_white(), 0);
    lv_label_set_text(i, icon);
    lv_obj_center(i);
    if (icon_out) {
        *icon_out = i;
    }
    return b;
}

/* The bottom bar of the note and reminder screens: Delete | Mic (red with a white square and a
 * pulsing ring while open) | a screen-specific button on the right. */
static void item_bar(lv_obj_t *scr, lv_event_cb_t mic_cb, lv_event_cb_t right_cb, const char *right_icon,
                     const lv_font_t *right_font, lv_obj_t **del, lv_obj_t **del_icon, lv_obj_t **mic,
                     lv_obj_t **mic_icon, lv_obj_t **stop, lv_obj_t **ring, lv_obj_t **right,
                     lv_obj_t **right_icon_out)
{
    lv_obj_t *bar = plain_box(scr);
    lv_obj_set_size(bar, 2 * NOTE_BTN + NOTE_MIC + 2 * 22, NOTE_MIC);
    lv_obj_align(bar, LV_ALIGN_BOTTOM_MID, 0, -NOTE_BAR_BOTTOM);
    lv_obj_add_flag(bar, LV_OBJ_FLAG_OVERFLOW_VISIBLE);
    lv_obj_set_flex_flow(bar, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(bar, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    *del = note_round_button(bar, NOTE_BTN, del_cb, SET_ICON_TRASH, &buddy_font_set, del_icon);
    *ring = plain_box(bar);     /* behind the mic, outside the layout */
    lv_obj_add_flag(*ring, LV_OBJ_FLAG_FLOATING | LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_radius(*ring, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(*ring, 3, 0);
    *mic = note_round_button(bar, NOTE_MIC, mic_cb, ICON_MIC, &buddy_font_28, mic_icon);
    lv_obj_set_style_border_width(*mic, 3, 0);
    *stop = plain_box(*mic);
    lv_obj_set_size(*stop, 28, 28);
    lv_obj_set_style_radius(*stop, 5, 0);
    lv_obj_set_style_bg_opa(*stop, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(*stop, lv_color_white(), 0);
    lv_obj_center(*stop);
    lv_obj_add_flag(*stop, LV_OBJ_FLAG_HIDDEN);
    *right = note_round_button(bar, NOTE_BTN, right_cb, right_icon, right_font, right_icon_out);
}

static void build_note_screen(void)
{
    s_note_text = heap_caps_calloc(1, NOTE_TEXT_MAX, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_note_text) {
        ESP_LOGE(TAG, "no memory for the note screen");
        return;
    }
    s_note_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_note_scr, ui_style_screen(), 0);
    lv_obj_remove_flag(s_note_scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_note_scr, det_gesture_cb, LV_EVENT_GESTURE, NULL);

    /* header: battery | "Note #n" | X */
    lv_obj_t *hdr = plain_box(s_note_scr);
    lv_obj_set_size(hdr, 350, REM_HDR_H);
    lv_obj_align(hdr, LV_ALIGN_TOP_MID, 0, REM_HDR_Y);
    lv_obj_add_flag(hdr, LV_OBJ_FLAG_OVERFLOW_VISIBLE);
    lv_obj_t *status = plain_box(hdr);
    lv_obj_set_size(status, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(status, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(status, 6, 0);
    lv_obj_align(status, LV_ALIGN_LEFT_MID, REM_STATUS_X, 0);
    s_note_batt = lv_label_create(status);
    lv_label_set_text(s_note_batt, "");
    s_note_title = lv_label_create(hdr);
    lv_obj_set_style_text_font(s_note_title, &buddy_font_28, 0);
    lv_obj_center(s_note_title);

    /* body: one card reaching the buttons, scrolling when taller */
    s_note_body = lv_obj_create(s_note_scr);
    lv_obj_remove_style_all(s_note_body);
    lv_obj_set_size(s_note_body, REM_W, NOTE_BODY_H);
    lv_obj_align(s_note_body, LV_ALIGN_TOP_MID, 0, REM_BODY_Y);
    lv_obj_set_scroll_dir(s_note_body, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(s_note_body, LV_SCROLLBAR_MODE_OFF);
    lv_obj_add_flag(s_note_body, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLL_ELASTIC);   /* see s_rem_body */
    lv_obj_add_flag(s_note_body, LV_OBJ_FLAG_GESTURE_BUBBLE);
    s_note_card = plain_box(s_note_body);
    lv_obj_set_size(s_note_card, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_style_min_height(s_note_card, NOTE_BODY_H, 0);
    lv_obj_set_style_radius(s_note_card, 22, 0);
    lv_obj_set_style_bg_opa(s_note_card, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_note_card, 1, 0);
    lv_obj_set_style_pad_all(s_note_card, 12, 0);
    lv_obj_set_flex_flow(s_note_card, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_note_card, 8, 0);
    lv_obj_t *top = plain_box(s_note_card);
    lv_obj_set_size(top, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(top, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(top, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(top, 14, 0);
    s_note_tile = plain_box(top);
    lv_obj_set_size(s_note_tile, 64, 64);
    lv_obj_set_style_radius(s_note_tile, 18, 0);
    lv_obj_set_style_bg_opa(s_note_tile, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_note_tile, 2, 0);
    lv_obj_t *doc = s_note_doc = lv_label_create(s_note_tile);
    lv_obj_set_style_text_font(doc, &buddy_font_set_lg, 0);
    lv_obj_set_style_text_color(doc, lv_color_white(), 0);
    lv_label_set_text(doc, SET_ICON_DOC);
    lv_obj_center(doc);
    s_note_head = lv_label_create(top);
    lv_obj_set_flex_grow(s_note_head, 1);
    lv_label_set_long_mode(s_note_head, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_font(s_note_head, &buddy_font_28, 0);
    lv_obj_set_style_radius(s_note_head, 10, 0);
    lv_obj_set_style_pad_hor(s_note_head, 4, 0);
    s_note_divider = plain_box(s_note_card);
    lv_obj_set_size(s_note_divider, LV_PCT(100), 1);
    lv_obj_set_style_bg_opa(s_note_divider, LV_OPA_COVER, 0);
    s_note_lines = plain_box(s_note_card);
    lv_obj_set_size(s_note_lines, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(s_note_lines, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_note_lines, 2, 0);
    s_note_live = lv_label_create(s_note_card);
    lv_obj_set_width(s_note_live, LV_PCT(100));
    lv_label_set_long_mode(s_note_live, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_pad_hor(s_note_live, 6, 0);
    lv_obj_add_flag(s_note_live, LV_OBJ_FLAG_HIDDEN);

    /* bar: Delete | Mic | Pin */
    item_bar(s_note_scr, note_mic_cb, note_pin_cb, SET_ICON_PIN_NOTE, &buddy_font_set,
             &s_note_del, &s_note_del_icon, &s_note_mic, &s_note_mic_icon, &s_note_stop, &s_note_ring,
             &s_note_pin, &s_note_pin_icon);

    lv_obj_move_foreground(hdr);
    lv_obj_move_foreground(ui_add_close_x(s_note_scr, det_close_cb));
}

/* Show note `number` (its text arrives with item_show; "Loading…" until then). */
static void show_note(int number, bool from_list)
{
    if (!s_note_scr) {
        return;
    }
    disarm_delete();
    s_del_btn = s_note_del;
    s_del_btn_lbl = NULL;
    s_del_outlined = false;
    s_del_icon = true;
    s_mic = s_note_mic;
    s_mic_icon = s_note_mic_icon;
    s_mic_stop = s_note_stop;
    s_mic_ring = s_note_ring;
    s_live = s_note_live;
    s_det_reminder = false;
    s_det_number = number;
    s_det_from_list = from_list;
    s_note_number = number;
    s_note_loading = true;
    s_note_text[0] = '\0';
    s_note_flash = -1;
    s_note_pinned = false;
    for (int i = 0; i < s_row_count; i++) {
        if (!s_rows[i].reminder && s_rows[i].number == number) {
            s_note_pinned = s_rows[i].pinned;
        }
    }
    note_live_reset();
    note_render();
    lv_obj_scroll_to_y(s_note_body, 0, LV_ANIM_OFF);
    ui_note_activity();
    ui_load_screen(s_note_scr, true);
}

/* item_show for a note: fill the note screen (opening it if needed), flash the changed line. */
static void note_show_json(const cJSON *item, int number)
{
    bool here = lv_screen_active() == s_note_scr && s_note_number == number;
    if (!here) {
        note_close_mic();
        show_note(number, false);
    }
    copy_str(s_note_text, NOTE_TEXT_MAX, jstr(item, "text"));
    s_note_loading = false;
    s_note_pinned = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(item, "pinned"));
    const cJSON *changed = cJSON_GetObjectItemCaseSensitive(item, "changed_line");
    s_note_flash = cJSON_IsNumber(changed) ? changed->valueint : -1;     /* 0 = the title */
    if (s_note_flash_timer) {
        lv_timer_delete(s_note_flash_timer);
        s_note_flash_timer = NULL;
    }
    if (s_note_flash >= 0) {
        s_note_flash_timer = lv_timer_create(note_flash_cb, NOTE_FLASH_MS, NULL);
        lv_timer_set_repeat_count(s_note_flash_timer, 1);
    }
    note_live_reset();
    note_render();
}

void ui_note_session(bool open)
{
    LOCK();
    if (open && s_note_scr && lv_screen_active() == s_note_scr) {         /* also when the server opened it */
        s_mic_reminder = false;
        s_mic_number = s_note_number;
    } else if (open && s_rem_scr && lv_screen_active() == s_rem_scr) {
        s_mic_reminder = true;
        s_mic_number = s_det_number;
    }
    s_note_mic_open = open;
    ui_hold_awake(open);        /* the screen stays on while the mic is open */
    note_live_reset();
    note_mic_visuals();
    UNLOCK();
}

void ui_note_state(ui_conv_t state)
{
    LOCK();
    if (state == UI_CONV_THINKING && s_live) {
        lv_obj_set_style_text_opa(s_live, LV_OPA_60, 0);  /* being applied */
    } else if (s_live) {
        lv_obj_set_style_text_opa(s_live, LV_OPA_COVER, 0);
    }
    UNLOCK();
}

void ui_note_text(const char *text, bool question)
{
    LOCK();
    if (s_live && text && text[0]) {
        note_live_set(text, question ? g_ui_theme.accent : lv_color_white());
        if (!question) {
            lv_obj_scroll_to_view_recursive(s_live, LV_ANIM_ON);
        }
    }
    UNLOCK();
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
    build_reminder_screen();
    build_note_screen();
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
    if (s_rem_scr && lv_screen_active() == s_rem_scr) {
        rem_render();
    }
    if (s_note_scr && lv_screen_active() == s_note_scr) {
        note_render();
    }
}

void ui_items_show_list(bool reminder)
{
    LOCK();
    note_close_mic();       /* e.g. a reminder deleted by voice from its own screen */
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
            if (k == 1) {
                parse_reminder(it, r);
                continue;
            }
            r->reminder = false;
            r->number = jint(it, "number");
            r->pinned = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(it, "pinned"));
            copy_str(r->text, sizeof(r->text), jstr(it, "preview"));
            copy_str(r->location, sizeof(r->location), jstr(it, "subtitle"));   /* notes: line 2 */
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
    if (s_rem_scr && s_rem_row && lv_screen_active() == s_rem_scr) {
        /* the reminder shown may have changed on the server - or been deleted (then back to the list) */
        bool found = false;
        for (int i = 0; i < s_row_count; i++) {
            if (s_rows[i].reminder && s_rows[i].number == s_det_number) {
                memcpy(s_rem_row, &s_rows[i], sizeof(*s_rem_row));
                s_det_done = s_rem_row->done;
                found = true;
                break;
            }
        }
        if (found) {
            rem_render();
        } else {
            note_close_mic();
            open_list(true, true);
        }
    } else if (s_note_scr && lv_screen_active() == s_note_scr) {
        bool found = false;
        for (int i = 0; i < s_row_count && !found; i++) {
            found = !s_rows[i].reminder && s_rows[i].number == s_note_number;
        }
        if (!found) {   /* the open note was deleted (another watch, the web, a confirmed voice delete) */
            note_close_mic();
            open_list(false, true);
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
        show_item_json(item, false, false);
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
        show_item_json(item, true, cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(j, "early")));
        UNLOCK();
    }
    cJSON_Delete(j);
}
