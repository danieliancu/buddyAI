#!/bin/sh
# Host unit tests for prov_util (setup password, Wi-Fi credential checks, QR payload).
# Needs a C compiler and ESP-IDF's Unity sources (IDF_PATH). Without a local compiler, run it in Docker:
#   docker run --rm -v "<repo>/firmware:/fw" -v "<IDF_PATH>/components/unity/unity/src:/unity" alpine:3 \
#     sh -c "apk add --no-cache gcc musl-dev >/dev/null && UNITY_SRC=/unity sh /fw/components/prov_util/test/host/run.sh"
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
UNITY_SRC="${UNITY_SRC:-$IDF_PATH/components/unity/unity/src}"
OUT="${TMPDIR:-/tmp}/prov_util_host_tests"
${CC:-cc} -std=c11 -Wall -Wextra -Werror -D_DEFAULT_SOURCE \
  -I"$UNITY_SRC" -I"$HERE/../../include" \
  "$HERE/host_runner.c" "$HERE/../../prov_util.c" "$UNITY_SRC/unity.c" -o "$OUT"
"$OUT"
