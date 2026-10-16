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
# Masks the stream key and splits ffmpeg's \r-terminated progress lines into log lines
# (sed alone would hold them until the next \n).
redact(){ python3 -u -c '
import re,sys
buf=b""
while True:
    c=sys.stdin.buffer.read1(4096)
    if not c: break
    buf+=c
    *lines,buf=re.split(rb"[\r\n]+",buf)
    for l in lines:
        if l.strip(): sys.stdout.buffer.write(re.sub(rb"live2/[^ ]+",b"live2/REDACTED",l)+b"\n"); sys.stdout.flush()
if buf.strip(): sys.stdout.buffer.write(re.sub(rb"live2/[^ ]+",b"live2/REDACTED",buf)+b"\n")
'; }
STATS=(-stats -stats_period "${STATS_PERIOD:-30}")
