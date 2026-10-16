#!/bin/bash
# shellcheck disable=SC2034
# Every 20 min record a fresh 3-min fallback clip from the HLS ingest.
LOG_TAG=clip
# shellcheck source=/dev/null
. /usr/bin/common.sh
TMP="$D/clip_tmp.mp4"
sleep 30
while true; do
  if [ "$(hls_age)" -lt 15 ]; then
    log "recording 180s clip from HLS ingest"
    if nice -n 15 timeout 220 ffmpeg -nostdin -loglevel error -y -i "$HLS" -t 180 -an \
         -vf "fps=$FPS,scale=$OUT_W:$OUT_H,format=yuv420p" -c:v libx264 -preset veryfast -crf 28 \
         -movflags +faststart "$TMP" \
       && [ "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$TMP" 2>/dev/null | cut -d. -f1)" -ge 60 ]; then
      mv -f "$TMP" "$FALLBACK"
      log "fallback.mp4 refreshed ($(du -h "$FALLBACK" | awk '{print $1}'))"
    else
      log "clip failed/short; keeping previous"
      rm -f "$TMP"
    fi
  else
    log "ingest quiet; skip clip"
  fi
  sleep 1200
done
