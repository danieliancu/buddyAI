/*
 * ola - language picker ("Other" in quick settings).
 *
 * Full screen in the same style as the notes / reminders lists: title, X in the
 * top-right corner, a search box (on-screen keyboard while it has focus) and
 * the list of every supported language except English, which has its own
 * button in settings. The list comes from the server (`languages` message).
 * Picking one sets both `language` and `preferred_language`, then goes back to
 * settings.
 */
#include <string.h>
#include <ctype.h>
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "board.h"
#include "ui_priv.h"

static const char *TAG = "ui_lang";

#define LOCK()      board_display_lock(0)
#define UNLOCK()    board_display_unlock()

#define MAX_LANGS       80
#define CONTENT_W       340
#define SEARCH_Y        90
#define SEARCH_H        48
#define LIST_Y          (SEARCH_Y + SEARCH_H + 10)
#define KEYBOARD_H      210
#define ROW_BG          0x1a1f28
#define PILL_BG         0x252b36

typedef struct {
    char code[SETTINGS_LANG_MAX];
    char label[SETTINGS_LANG_LABEL_MAX];    /* renderable on the watch (native name when possible) */
    char name[32];                          /* English name, for search */
} lang_entry_t;

static lang_entry_t *s_langs;   /* PSRAM */
static int           s_count;

static lv_obj_t *s_scr;
static lv_obj_t *s_title;
static lv_obj_t *s_search;
static lv_obj_t *s_list;
static lv_obj_t *s_kb;

/* ------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* ------------------------------------------------------------------------- */

/* Case-insensitive ASCII substring (the English name covers searches without diacritics). */
static bool contains_ci(const char *hay, const char *needle)
{
    size_t n = strlen(needle);
    if (!n) {
        return true;
    }
    for (; *hay; hay++) {
        size_t i = 0;
        while (i < n && hay[i] && tolower((unsigned char)hay[i]) == tolower((unsigned char)needle[i])) {
            i++;
        }
        if (i == n) {
            return true;
        }
    }
    return false;
}

static void show_keyboard(bool show)
{
    lv_obj_set_flag(s_kb, LV_OBJ_FLAG_HIDDEN, !show);
    lv_obj_set_height(s_list, BOARD_LCD_V_RES - LIST_Y - (show ? KEYBOARD_H : 20));
    if (!show) {
        lv_obj_remove_state(s_search, LV_STATE_FOCUSED);
    }
}

/* ------------------------------------------------------------------------- */
/* List                                                                       */
/* ------------------------------------------------------------------------- */

static void pick_cb(lv_event_t *e)
{
    int idx = (int)(intptr_t)lv_event_get_user_data(e);
    if (idx < 0 || idx >= s_count || !g_ui_cb.on_settings_change) {
        return;
    }
    cJSON *c = cJSON_CreateObject();
    cJSON_AddStringToObject(c, "language", s_langs[idx].code);
    cJSON_AddStringToObject(c, "preferred_language", s_langs[idx].code);
    g_ui_cb.on_settings_change(c);
    cJSON_Delete(c);
    show_keyboard(false);
    ui_settings_open();
}

static void rebuild(void)
{
    lv_obj_clean(s_list);
    const char *q = lv_textarea_get_text(s_search);
    int shown = 0;
    for (int i = 0; i < s_count; i++) {
        const lang_entry_t *l = &s_langs[i];
        if (strcmp(l->code, "en") == 0 || !(contains_ci(l->label, q) || contains_ci(l->name, q))) {
            continue;
        }
        lv_obj_t *b = lv_button_create(s_list);
        lv_obj_remove_style_all(b);
        lv_obj_set_size(b, CONTENT_W, 52);
        lv_obj_set_style_radius(b, 16, 0);
        lv_obj_set_style_bg_color(b, lv_color_hex(ROW_BG), 0);
        lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(b, lv_color_hex(PILL_BG), LV_STATE_PRESSED);
        lv_obj_set_style_pad_hor(b, 16, 0);
        if (strcmp(l->code, g_ui_settings.language) == 0) {
            lv_obj_set_style_border_color(b, g_ui_theme.accent, 0);
            lv_obj_set_style_border_width(b, 2, 0);
        }
        lv_obj_add_event_cb(b, pick_cb, LV_EVENT_CLICKED, (void *)(intptr_t)i);
        lv_obj_t *t = lv_label_create(b);
        lv_label_set_text(t, l->label);
        lv_obj_align(t, LV_ALIGN_LEFT_MID, 0, 0);
        if (strcmp(l->label, l->name) != 0) {
            lv_obj_t *n = lv_label_create(b);
            lv_obj_set_style_text_opa(n, LV_OPA_50, 0);
            lv_label_set_text(n, l->name);
            lv_obj_align(n, LV_ALIGN_RIGHT_MID, 0, 0);
        }
        shown++;
    }
    if (shown == 0) {
        lv_obj_t *l = lv_label_create(s_list);
        lv_obj_set_style_text_opa(l, LV_OPA_70, 0);
        lv_obj_set_style_pad_top(l, 20, 0);
        lv_label_set_text(l, s_count ? ui_str(STR_NO_MATCH) : ui_str(STR_LOADING));
    }
}

/* ------------------------------------------------------------------------- */
/* Screen                                                                     */
/* ------------------------------------------------------------------------- */

static void close_cb(lv_event_t *e)
{
    show_keyboard(false);
    ui_settings_open();
}

static void search_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_FOCUSED || code == LV_EVENT_CLICKED) {
        lv_keyboard_set_textarea(s_kb, s_search);
        show_keyboard(true);
    } else if (code == LV_EVENT_VALUE_CHANGED) {
        rebuild();
    }
}

static void kb_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_READY || code == LV_EVENT_CANCEL) {
        show_keyboard(false);
    }
}

void ui_lang_init(void)
{
    s_langs = heap_caps_calloc(MAX_LANGS, sizeof(lang_entry_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);

    s_scr = lv_obj_create(NULL);
    lv_obj_add_style(s_scr, ui_style_screen(), 0);
    lv_obj_remove_flag(s_scr, LV_OBJ_FLAG_SCROLLABLE);

    s_title = lv_label_create(s_scr);
    lv_obj_set_style_text_font(s_title, &buddy_font_28, 0);
    lv_obj_align(s_title, LV_ALIGN_TOP_LEFT, 36, 42);
    lv_label_set_text(s_title, ui_str(STR_LANGUAGE));

    lv_obj_t *x = ui_add_close_x(s_scr, close_cb);

    s_search = lv_textarea_create(s_scr);
    lv_obj_set_size(s_search, CONTENT_W, SEARCH_H);
    lv_obj_align(s_search, LV_ALIGN_TOP_MID, 0, SEARCH_Y);
    lv_textarea_set_one_line(s_search, true);
    lv_textarea_set_placeholder_text(s_search, ui_str(STR_SEARCH));
    lv_obj_set_style_bg_color(s_search, lv_color_white(), 0);          /* white box, black text */
    lv_obj_set_style_bg_opa(s_search, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_search, 0, 0);
    lv_obj_set_style_radius(s_search, 24, 0);
    lv_obj_set_style_pad_hor(s_search, 18, 0);
    lv_obj_set_style_text_color(s_search, lv_color_black(), 0);
    lv_obj_set_style_text_color(s_search, lv_color_hex(0x8a8f99), LV_PART_TEXTAREA_PLACEHOLDER);
    lv_obj_set_style_bg_color(s_search, lv_color_black(), LV_PART_CURSOR);
    lv_obj_set_style_border_color(s_search, lv_color_black(), LV_PART_CURSOR);
    lv_obj_add_event_cb(s_search, search_cb, LV_EVENT_ALL, NULL);

    s_list = lv_obj_create(s_scr);
    lv_obj_remove_style_all(s_list);
    lv_obj_set_width(s_list, CONTENT_W);
    lv_obj_align(s_list, LV_ALIGN_TOP_MID, 0, LIST_Y);
    lv_obj_set_scroll_dir(s_list, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(s_list, LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_flex_flow(s_list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_list, 8, 0);
    lv_obj_set_style_pad_bottom(s_list, 24, 0);

    s_kb = lv_keyboard_create(s_scr);
    lv_obj_set_size(s_kb, BOARD_LCD_H_RES, KEYBOARD_H);
    lv_obj_align(s_kb, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_text_font(s_kb, &buddy_font_20, LV_PART_ITEMS);
    lv_obj_add_event_cb(s_kb, kb_cb, LV_EVENT_ALL, NULL);
    show_keyboard(false);
    lv_obj_move_foreground(x);      /* nothing built after it may cover its touch area */
}

void ui_lang_open(void)
{
    lv_label_set_text(s_title, ui_str(STR_LANGUAGE));
    lv_textarea_set_placeholder_text(s_search, ui_str(STR_SEARCH));
    lv_textarea_set_text(s_search, "");
    show_keyboard(false);
    rebuild();
    lv_obj_scroll_to_y(s_list, 0, LV_ANIM_OFF);
    ui_note_activity();
    ui_load_screen(s_scr, true);
}

/* Label of a language code from the server's list (NULL if unknown). Caller holds the lock. */
const char *ui_lang_label(const char *code)
{
    for (int i = 0; code && i < s_count; i++) {
        if (strcmp(s_langs[i].code, code) == 0) {
            return s_langs[i].label;
        }
    }
    return NULL;
}

static void copy_str(char *dst, size_t len, const char *src)
{
    size_t n = src ? strlen(src) : 0;
    if (n >= len) {
        n = len - 1;
        while (n > 0 && ((unsigned char)src[n] & 0xC0) == 0x80) {
            n--;
        }
    }
    if (n) {
        memcpy(dst, src, n);
    }
    dst[n] = '\0';
}

void ui_languages_set(const char *json)
{
    cJSON *j = cJSON_Parse(json);
    const cJSON *items = j ? cJSON_GetObjectItemCaseSensitive(j, "items") : NULL;
    if (!cJSON_IsArray(items) || !s_langs) {
        ESP_LOGW(TAG, "bad languages message (%s)", j ? "no items" : "invalid JSON");
        cJSON_Delete(j);
        return;
    }
    LOCK();
    int n = 0;
    const cJSON *it;
    cJSON_ArrayForEach(it, items) {
        if (n >= MAX_LANGS) {
            break;
        }
        const cJSON *code = cJSON_GetObjectItemCaseSensitive(it, "code");
        const cJSON *label = cJSON_GetObjectItemCaseSensitive(it, "label");
        const cJSON *name = cJSON_GetObjectItemCaseSensitive(it, "name");
        if (!cJSON_IsString(code) || !cJSON_IsString(label)) {
            continue;
        }
        copy_str(s_langs[n].code, sizeof(s_langs[n].code), code->valuestring);
        copy_str(s_langs[n].label, sizeof(s_langs[n].label), label->valuestring);
        copy_str(s_langs[n].name, sizeof(s_langs[n].name), cJSON_IsString(name) ? name->valuestring : label->valuestring);
        n++;
    }
    s_count = n;
    ESP_LOGI(TAG, "%d languages", n);
    if (lv_screen_active() == s_scr) {
        rebuild();
    }
    ui_settings_refresh();
    UNLOCK();
    cJSON_Delete(j);
}
