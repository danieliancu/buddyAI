/*
 * BuddyAI - conversation screen (chat bubbles).
 *
 * Opens full screen as soon as a turn starts (mic tap on the watchface). Same
 * visual system as the notes / reminders screens: title top-left, X top-right.
 * The dialog shows as bubbles - the user's words on the right (accent), Buddy's
 * replies on the left - in a scrollable area that always follows the newest
 * text. A small mic button stays at the bottom (tap = talk / interrupt) and
 * shows the turn state: ring (listening), spinner (thinking), speaker icon
 * (speaking).
 *
 * The last CHAT_MAX_BUBBLES bubbles are kept while the watch runs, so tapping
 * the mic again continues the same dialog on screen.
 */
#include <string.h>
#include "esp_log.h"
#include "board.h"
#include "ui_priv.h"

#define LOCK()      board_display_lock(0)
#define UNLOCK()    board_display_unlock()

#define CHAT_MAX_BUBBLES    40
#define DISPLAY_BIG_MAX     10          /* characters shown in the 56 px font; longer values use 28 px */
#define LIST_W              370
#define LIST_Y              84
#define LIST_H              292
#define BUBBLE_MAX_W        290
#define MIC_SIZE            76
#define MIC_BOTTOM          24
#define RING_BASE           (MIC_SIZE + 14)
#define SPINNER_SIZE        (MIC_SIZE + 16)
#define PILL_BG             0x252b36
#define REPLY_BG            0x1f2530
#define DOT_SIZE            10          /* typing indicator: three bouncing dots, no bubble */
#define DOT_GAP             7
#define DOT_BOUNCE          7           /* px up */
#define DOT_BOUNCE_MS       300
#define DOT_STAGGER_MS      150

static lv_obj_t  *s_scr;
static lv_obj_t  *s_title;
static lv_obj_t  *s_list;
static lv_obj_t  *s_mic;
static lv_obj_t  *s_mic_lbl;
static lv_obj_t  *s_ring;
static lv_obj_t  *s_spinner;
static lv_obj_t  *s_cur_user;       /* label of this turn's user bubble (NULL until words arrive) */
static lv_obj_t  *s_typing;         /* bouncing dots while listening, before the first words */
static lv_obj_t  *s_cur_reply;      /* label of this turn's reply bubble */
static bool       s_cur_display;    /* the reply bubble shows a short value: ignore the text */
static ui_conv_t  s_state = UI_CONV_IDLE;
static bool       s_mic_swallow;

/* ------------------------------------------------------------------------- */
/* Bubbles                                                                    */
/* ------------------------------------------------------------------------- */

static void scroll_to_end(void)
{
    lv_obj_update_layout(s_list);
    int32_t below = lv_obj_get_scroll_bottom(s_list);
    if (below > 0) {
        lv_obj_scroll_by(s_list, 0, -below, LV_ANIM_OFF);
    }
}

static void trim_history(void)
{
    while (lv_obj_get_child_count(s_list) > CHAT_MAX_BUBBLES) {
        lv_obj_t *old = lv_obj_get_child(s_list, 0);
        if (old == s_typing || lv_obj_get_child(old, 0) == s_cur_user || lv_obj_get_child(old, 0) == s_cur_reply) {
            break;
        }
        lv_obj_delete(old);
    }
}

/* Size a bubble to its text: one line if it fits, else BUBBLE_MAX_W and the label wraps.
 * (A content-sized label does not wrap at max_width, so long replies ran off the screen.) */
static void fit_width(lv_obj_t *l)
{
    lv_point_t size;
    lv_text_get_size(&size, lv_label_get_text(l), lv_obj_get_style_text_font(l, 0), 0, 0, LV_COORD_MAX,
                     LV_TEXT_FLAG_NONE);
    int32_t w = size.x + lv_obj_get_style_pad_left(l, 0) + lv_obj_get_style_pad_right(l, 0) + 2;
    lv_obj_set_width(l, w < BUBBLE_MAX_W ? w : BUBBLE_MAX_W);
}

/* One full-width row holding a bubble aligned right (user) or left (Buddy). Returns the label. */
static lv_obj_t *add_bubble(bool user, const char *text)
{
    lv_obj_t *row = lv_obj_create(s_list);
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, LIST_W, LV_SIZE_CONTENT);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(row, LV_OBJ_FLAG_GESTURE_BUBBLE | LV_OBJ_FLAG_SCROLL_ON_FOCUS);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, user ? LV_FLEX_ALIGN_END : LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START,
                          LV_FLEX_ALIGN_START);

    lv_obj_t *l = lv_label_create(row);
    lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_pad_hor(l, 14, 0);
    lv_obj_set_style_pad_ver(l, 9, 0);
    lv_obj_set_style_radius(l, 18, 0);
    lv_obj_set_style_bg_opa(l, LV_OPA_COVER, 0);
    lv_obj_set_style_text_line_space(l, 1, 0);
    if (user) {
        lv_obj_set_style_bg_color(l, g_ui_theme.accent, 0);
        lv_obj_set_style_text_color(l, lv_color_white(), 0);
    } else {
        lv_obj_set_style_bg_color(l, lv_color_hex(REPLY_BG), 0);
    }
    lv_obj_add_flag(l, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_label_set_text(l, text ? text : "");
    fit_width(l);
    trim_history();
    return l;
}

static void dot_y_cb(void *dot, int32_t v)
{
    lv_obj_set_style_translate_y(dot, v, 0);
}

/* WhatsApp-style "typing": three dots bouncing in turn, on the user's side, no background. */
static void show_typing(void)
{
    s_typing = lv_obj_create(s_list);
    lv_obj_remove_style_all(s_typing);
    lv_obj_set_size(s_typing, LIST_W, DOT_SIZE + DOT_BOUNCE + 12);
    lv_obj_remove_flag(s_typing, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(s_typing, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_flex_flow(s_typing, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(s_typing, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_END);
    lv_obj_set_style_pad_column(s_typing, DOT_GAP, 0);
    lv_obj_set_style_pad_right(s_typing, 14, 0);
    lv_obj_set_style_pad_bottom(s_typing, 4, 0);
    for (int i = 0; i < 3; i++) {
        lv_obj_t *dot = lv_obj_create(s_typing);
        lv_obj_remove_style_all(dot);
        lv_obj_set_size(dot, DOT_SIZE, DOT_SIZE);
        lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_color(dot, g_ui_theme.accent, 0);
        lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
        lv_anim_t a;
        lv_anim_init(&a);
        lv_anim_set_var(&a, dot);
        lv_anim_set_exec_cb(&a, dot_y_cb);
        lv_anim_set_values(&a, 0, -DOT_BOUNCE);
        lv_anim_set_duration(&a, DOT_BOUNCE_MS);
        lv_anim_set_reverse_duration(&a, DOT_BOUNCE_MS);
        lv_anim_set_delay(&a, i * DOT_STAGGER_MS);
        lv_anim_set_repeat_delay(&a, 2 * DOT_STAGGER_MS);
        lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
        lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
        lv_anim_start(&a);
    }
    trim_history();
}

static void hide_typing(void)
{
    if (s_typing) {
        lv_obj_delete(s_typing);    /* also stops the dots' animations */
        s_typing = NULL;
    }
}

/* ------------------------------------------------------------------------- */
/* Mic button                                                                 */
/* ------------------------------------------------------------------------- */

static void apply_state_visuals(void)
{
    lv_obj_set_flag(s_ring, LV_OBJ_FLAG_HIDDEN, s_state != UI_CONV_LISTENING);
    lv_obj_set_flag(s_spinner, LV_OBJ_FLAG_HIDDEN, s_state != UI_CONV_THINKING);
    lv_label_set_text(s_mic_lbl, s_state == UI_CONV_SPEAKING ? ICON_VOLUME : ICON_MIC);
    lv_obj_set_style_bg_opa(s_mic, s_state == UI_CONV_THINKING ? LV_OPA_60 : LV_OPA_COVER, 0);
}

static void mic_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_PRESSED) {
        s_mic_swallow = !ui_screen_awake();     /* a tap on a dim screen only wakes it */
    } else if (code == LV_EVENT_CLICKED && !s_mic_swallow && g_ui_cb.on_mic_tap) {
        g_ui_cb.on_mic_tap();
    }
}

/* ------------------------------------------------------------------------- */
/* Screen                                                                     */
/* ------------------------------------------------------------------------- */

static void leave(void)
{
    if (g_ui_cb.on_chat_closed) {
        g_ui_cb.on_chat_closed();   /* aborts a turn that is still listening */
    }
    ui_go_watchface();
}

static void close_cb(lv_event_t *e)
{
    leave();
}

static void gesture_cb(lv_event_t *e)
{
    if (lv_indev_get_gesture_dir(lv_indev_active()) == LV_DIR_RIGHT) {
        lv_indev_wait_release(lv_indev_active());
        leave();
    }
}

void ui_chat_init(void)
{
    s_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_scr, ui_style_screen(), 0);
    lv_obj_remove_flag(s_scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_scr, gesture_cb, LV_EVENT_GESTURE, NULL);

    s_title = lv_label_create(s_scr);
    lv_obj_set_style_text_font(s_title, &buddy_font_28, 0);
    lv_obj_align(s_title, LV_ALIGN_TOP_LEFT, 36, 42);
    lv_label_set_text(s_title, "Buddy");

    /* X: same as the notes / reminders screens */
    lv_obj_t *x = lv_button_create(s_scr);
    lv_obj_remove_style_all(x);
    lv_obj_set_size(x, 56, 56);
    lv_obj_align(x, LV_ALIGN_TOP_RIGHT, -30, 10);     /* same place as on the notes / reminders screens */
    lv_obj_set_style_radius(x, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(x, lv_color_hex(PILL_BG), 0);
    lv_obj_set_style_bg_opa(x, LV_OPA_TRANSP, 0);    /* just the X; the 56 px touch area stays */
    lv_obj_set_ext_click_area(x, 12);
    lv_obj_add_event_cb(x, close_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *xl = lv_label_create(x);
    lv_obj_set_style_text_font(xl, &buddy_font_28, 0);
    lv_obj_set_style_text_color(xl, lv_color_white(), 0);
    lv_label_set_text(xl, ICON_CLOSE);
    lv_obj_center(xl);

    s_list = lv_obj_create(s_scr);
    lv_obj_remove_style_all(s_list);
    lv_obj_set_size(s_list, LIST_W, LIST_H);
    lv_obj_align(s_list, LV_ALIGN_TOP_MID, 0, LIST_Y);
    lv_obj_set_scroll_dir(s_list, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(s_list, LV_SCROLLBAR_MODE_OFF);
    lv_obj_add_flag(s_list, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_set_flex_flow(s_list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_list, 8, 0);
    lv_obj_set_style_pad_bottom(s_list, 6, 0);

    /* Mic: ring (listening) and spinner (thinking) sit behind / around it. */
    s_ring = lv_obj_create(s_scr);
    lv_obj_remove_style_all(s_ring);
    lv_obj_add_style(s_ring, ui_style_accent_border(), 0);
    lv_obj_set_size(s_ring, RING_BASE, RING_BASE);
    lv_obj_set_style_radius(s_ring, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(s_ring, 4, 0);
    lv_obj_remove_flag(s_ring, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_align(s_ring, LV_ALIGN_BOTTOM_MID, 0, -(MIC_BOTTOM - (RING_BASE - MIC_SIZE) / 2));

    s_spinner = lv_spinner_create(s_scr);
    lv_obj_set_size(s_spinner, SPINNER_SIZE, SPINNER_SIZE);
    lv_obj_set_style_arc_width(s_spinner, 5, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(s_spinner, LV_OPA_20, LV_PART_MAIN);
    lv_obj_set_style_arc_width(s_spinner, 5, LV_PART_INDICATOR);
    lv_obj_add_style(s_spinner, ui_style_accent_border(), LV_PART_INDICATOR);
    lv_obj_remove_flag(s_spinner, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_align(s_spinner, LV_ALIGN_BOTTOM_MID, 0, -(MIC_BOTTOM - (SPINNER_SIZE - MIC_SIZE) / 2));

    s_mic = lv_button_create(s_scr);
    lv_obj_remove_style_all(s_mic);
    lv_obj_add_style(s_mic, ui_style_accent_bg(), 0);
    lv_obj_set_size(s_mic, MIC_SIZE, MIC_SIZE);
    lv_obj_set_style_radius(s_mic, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_transform_scale(s_mic, 236, LV_STATE_PRESSED);
    lv_obj_set_style_transform_pivot_x(s_mic, MIC_SIZE / 2, 0);
    lv_obj_set_style_transform_pivot_y(s_mic, MIC_SIZE / 2, 0);
    lv_obj_align(s_mic, LV_ALIGN_BOTTOM_MID, 0, -MIC_BOTTOM);
    lv_obj_add_event_cb(s_mic, mic_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(s_mic, mic_cb, LV_EVENT_CLICKED, NULL);
    s_mic_lbl = lv_label_create(s_mic);
    lv_obj_set_style_text_font(s_mic_lbl, &buddy_font_28, 0);
    lv_obj_set_style_text_color(s_mic_lbl, lv_color_white(), 0);
    lv_obj_center(s_mic_lbl);

    apply_state_visuals();
}

/* Caller holds the lock. */
void ui_chat_set_state(ui_conv_t st)
{
    if (!s_scr || st == s_state) {
        return;
    }
    if (st == UI_CONV_LISTENING) {
        hide_typing();
        s_cur_user = NULL;
        s_cur_reply = NULL;
        s_cur_display = false;
        show_typing();
    } else if (st == UI_CONV_IDLE) {
        hide_typing();      /* nothing was heard */
        s_cur_user = NULL;
        s_cur_reply = NULL;
        s_cur_display = false;
    }
    s_state = st;
    apply_state_visuals();
    if (st != UI_CONV_IDLE) {
        ui_load_screen(s_scr, true);
    }
    scroll_to_end();
}

/* Caller holds the lock. 40 ms tick while listening: the ring follows the mic level. */
void ui_chat_anim(int level, uint32_t phase)
{
    if (!s_scr || s_state != UI_CONV_LISTENING || lv_screen_active() != s_scr) {
        return;
    }
    int pulse = (lv_trigo_sin((int32_t)(phase * 9) % 360) + 32767) * 6 / 65534;
    int size = RING_BASE + pulse + level * 26 / 100;
    lv_obj_set_size(s_ring, size, size);
    lv_obj_align(s_ring, LV_ALIGN_BOTTOM_MID, 0, -(MIC_BOTTOM - (size - MIC_SIZE) / 2));
    lv_obj_set_style_border_opa(s_ring, (lv_opa_t)(120 + level * 135 / 100), 0);
}

void ui_chat_refresh_theme(void)
{
    if (!s_list) {
        return;
    }
    uint32_t n = lv_obj_get_child_count(s_list);
    for (uint32_t i = 0; i < n; i++) {
        lv_obj_t *row = lv_obj_get_child(s_list, i);
        if (lv_obj_get_style_flex_main_place(row, 0) == LV_FLEX_ALIGN_END) {
            lv_obj_set_style_bg_color(lv_obj_get_child(row, 0), g_ui_theme.accent, 0);
        }
    }
}

void ui_chat_user(const char *text)
{
    if (!text || !text[0]) {
        return;
    }
    LOCK();
    if (s_scr) {
        hide_typing();
        if (!s_cur_user) {
            s_cur_user = add_bubble(true, text);
        } else {
            lv_label_set_text(s_cur_user, text);
            fit_width(s_cur_user);
        }
        scroll_to_end();
    }
    UNLOCK();
}

void ui_chat_reply(const char *delta)
{
    if (!delta || !delta[0]) {
        return;
    }
    LOCK();
    if (s_scr && !s_cur_display) {
        if (!s_cur_reply) {
            s_cur_reply = add_bubble(false, delta);
        } else {
            lv_label_ins_text(s_cur_reply, LV_LABEL_POS_LAST, delta);
            fit_width(s_cur_reply);
        }
        scroll_to_end();
    }
    UNLOCK();
}

/* Next UTF-8 code point of `s` (advances *p); malformed bytes decode as themselves. */
static uint32_t next_cp(const unsigned char **p)
{
    const unsigned char *s = *p;
    uint32_t c = *s++;
    int extra = c >= 0xF0 ? 3 : c >= 0xE0 ? 2 : c >= 0xC0 ? 1 : 0;
    if (extra) {
        c &= 0x3F >> extra;
    }
    while (extra-- > 0 && (*s & 0xC0) == 0x80) {
        c = (c << 6) | (*s++ & 0x3F);
    }
    *p = s;
    return c;
}

/* True if every character of `text` has a glyph in `font` (or its maths fallback). */
static bool font_covers(const lv_font_t *font, const char *text)
{
    const unsigned char *p = (const unsigned char *)text;
    while (*p) {
        uint32_t cp = next_cp(&p);
        lv_font_glyph_dsc_t dsc;
        if (cp != ' ' && !lv_font_get_glyph_dsc(font, &dsc, cp, 0)) {
            return false;
        }
    }
    return true;
}

/* Short values (21°C, 14:30, E = mc²) large; longer formulas at 28 px so they fit. */
static const lv_font_t *display_font(const char *text)
{
    int chars = 0;
    for (const unsigned char *p = (const unsigned char *)text; *p; chars++) {
        next_cp(&p);
    }
    return chars <= DISPLAY_BIG_MAX && font_covers(&buddy_font_big, text) ? &buddy_font_big : &buddy_font_28;
}

void ui_chat_display(const char *text)
{
    if (!text || !text[0]) {
        return;
    }
    LOCK();
    if (s_scr) {
        if (!s_cur_reply) {
            s_cur_reply = add_bubble(false, text);
        } else {
            lv_label_set_text(s_cur_reply, text);
        }
        lv_obj_set_style_text_font(s_cur_reply, display_font(text), 0);
        lv_obj_set_style_text_color(s_cur_reply, g_ui_theme.accent, 0);
        lv_obj_set_style_text_align(s_cur_reply, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_style_bg_opa(s_cur_reply, LV_OPA_TRANSP, 0);    /* just the value, no bubble */
        lv_obj_set_style_pad_ver(s_cur_reply, 24, 0);  /* room above and below the value */
        lv_obj_set_flex_align(lv_obj_get_parent(s_cur_reply), LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_START,
                              LV_FLEX_ALIGN_START);
        fit_width(s_cur_reply);
        s_cur_display = true;
        scroll_to_end();
    }
    UNLOCK();
}

bool ui_chat_is_active(void)
{
    return s_scr && lv_screen_active() == s_scr;
}
