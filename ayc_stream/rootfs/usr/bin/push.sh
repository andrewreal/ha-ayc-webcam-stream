#!/bin/bash
# shellcheck disable=SC2034
# YouTube push with watchdog: live from HLS, fallback loop when camera is down.
# All graphics come from one full-frame RGBA PNG (overlay_live.png /
# overlay_offline.png) rendered by overlay.py. image2 -loop 1 re-opens the file
# for every overlay frame, so layout/caption changes appear live.
# OUTPUT_URL / OUTPUT_FORMAT override the target (local testing).
LOG_TAG=push
# shellcheck source=/dev/null
. /usr/bin/common.sh
URL="${OUTPUT_URL:-rtmps://a.rtmps.youtube.com:443/live2/${STREAM_KEY}}"
OUT_FMT="${OUTPUT_FORMAT:-flv}"
HLS_MAX_AGE=20     # HLS older than this = camera/ingest down
STALE_GRACE=12     # seconds stale before killing the live push
OV_FPS="${OVERLAY_FPS:-2}"   # how often ffmpeg re-reads the overlay PNG
ENC=(-c:v libx264 -preset "$PRESET" -b:v "${BITRATE_K}k" -maxrate "${BITRATE_K}k" -bufsize "$((BITRATE_K * 2))k"
     -g $((FPS * 2)) -keyint_min $((FPS * 2)) -sc_threshold 0 -tune zerolatency -threads 3 -pix_fmt yuv420p
     -c:a aac -b:a 64k -ar 44100 -f "$OUT_FMT")
SILENCE=(-thread_queue_size 64 -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100)
ov_in(){ echo "-thread_queue_size 64 -f image2 -pattern_type none -loop 1 -framerate $OV_FPS -i $D/overlay_$1.png"; }
FILTER="[0:v]fps=$FPS,scale=$OUT_W:$OUT_H,setpts=PTS-STARTPTS[base];[2:v]format=rgba,setpts=PTS-STARTPTS[ov];[base][ov]overlay=0:0:format=yuv420:eof_action=repeat:repeatlast=1,format=yuv420p[v]"

ensure_assets(){
  [ -s "$D/caption.txt" ] || echo "Axmouth  |  Tide data loading  |  Not for navigation" > "$D/caption.txt"
  [ -s "$FALLBACK" ] || cp /opt/ayc/fallback.mp4 "$FALLBACK"
  for m in live offline; do
    [ -s "$D/overlay_$m.png" ] || python3 -c "from PIL import Image; Image.new('RGBA',($OUT_W,$OUT_H),(0,0,0,0)).save('$D/overlay_$m.png')"
  done
}
alive(){ [ "$(hls_age)" -lt "$HLS_MAX_AGE" ]; }

while true; do
  ensure_assets
  if alive; then
    log "LIVE: HLS fresh, pushing ${OUT_W}x${OUT_H}@${FPS} ${BITRATE_K}k"
    # shellcheck disable=SC2046
    ffmpeg -nostdin -hide_banner -loglevel warning \
      -re -thread_queue_size 512 -live_start_index -3 -i "$HLS" "${SILENCE[@]}" $(ov_in live) \
      -filter_complex "$FILTER" -map "[v]" -map 1:a "${ENC[@]}" "$URL" > >(redact) 2>&1 &
    PID=$!
    stale=0
    while kill -0 "$PID" 2>/dev/null; do
      if alive; then stale=0; else
        stale=$((stale + 2))
        if [ "$stale" -ge "$STALE_GRACE" ]; then
          log "HLS stale ${stale}s (age $(hls_age)s) - killing push, switching to fallback"
          kill "$PID" 2>/dev/null; sleep 1; kill -9 "$PID" 2>/dev/null
          break
        fi
      fi
      sleep 2
    done
    wait "$PID" 2>/dev/null
    log "live ffmpeg exited; re-checking ingest"
  else
    log "FALLBACK: camera down, looping fallback.mp4 (re-check every 45s)"
    # shellcheck disable=SC2046
    ffmpeg -nostdin -hide_banner -loglevel warning -stream_loop -1 -re -i "$FALLBACK" \
      "${SILENCE[@]}" $(ov_in offline) \
      -filter_complex "$FILTER" -map "[v]" -map 1:a -t 45 "${ENC[@]}" "$URL" 2>&1 | redact
  fi
  sleep 2
done
