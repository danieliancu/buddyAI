idf_component_register(
    SRCS "src/board_core.c" "src/board_display.c" "src/board_audio.c" "src/board_pm.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_driver_i2c esp_driver_gpio esp_lcd
    PRIV_REQUIRES esp_driver_spi esp_driver_i2s esp_timer esp_pm
)
