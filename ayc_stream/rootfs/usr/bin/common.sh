#!/bin/bash
# shellcheck disable=SC2034
# Shared settings for AYC stream scripts. Sourced, not executed.
export TZ=Europe/London
D="${AYC_DIR:-/data}"
HLS_DIR="${HLS_DIR:-/tmp/ayc_hls}"
HLS="$HLS_DIR/live.m3u8"
F=/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf
[ -f "$F" ] || F=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
OUT_H="${OUTPUT_HEIGHT:-720}"
OUT_W=$(( (OUT_H * 16 / 9 + 1) / 2 * 2 ))
FPS="${FPS:-15}"
BITRATE_K="${BITRATE_K:-1500}"
PRESET="${X264_PRESET:-ultrafast}"
CHART="$D/tide_chart.png"
FALLBACK="$D/fallback.mp4"
# Scale factor vs the 1080p original
s(){ echo $(( ($1 * OUT_H + 540) / 1080 )); }
log(){ echo "$(date '+%F %T %Z') [$LOG_TAG] $*"; }
hls_age(){
  [ -s "$HLS" ] || { echo 9999; return; }
  echo $(( $(date +%s) - $(stat -c %Y "$HLS") ))
}
redact(){ sed -u -E 's#live2/[^ ]+#live2/REDACTED#g'; }
