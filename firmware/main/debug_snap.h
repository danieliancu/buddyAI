/*
 * ola - development only: GET http://<watch-ip>:8080/snap returns the current screen (BMP).
 * Does nothing in release builds.
 */
#pragma once

void debug_snap_start(void);     /* once Wi-Fi is up; safe to call again */
