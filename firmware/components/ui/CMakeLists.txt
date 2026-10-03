file(GLOB UI_FONTS "${CMAKE_CURRENT_SOURCE_DIR}/fonts/*.c")

idf_component_register(
    SRCS "ui.c" "ui_screens.c" "ui_i18n.c" "ui_i18n_tables.c" "ui_items.c" "ui_chat.c" "ui_lang.c" "ui_wave_img.c" ${UI_FONTS}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "."
    REQUIRES settings json
    PRIV_REQUIRES board
)
