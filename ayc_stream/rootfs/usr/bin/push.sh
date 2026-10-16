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
OV_FPS="${OVERLAY_FPS:-1}"   # how often ffmpeg re-reads the overlay PNG
ENC=(-c:v libx264 -preset "$PRESET" -b:v "${BITRATE_K}k" -maxrate "${BITRATE_K}k" -bufsize "$((BITRATE_K * 2))k"
     -fps_mode cfr -r "$FPS" -g $((FPS * 2)) -keyint_min $((FPS * 2)) -sc_threshold 0 -tune zerolatency -threads 3 -pix_fmt yuv420p
     -c:a aac -b:a 128k -ar 44100 -ac 2 -f "$OUT_FMT")
SILENCE=(-thread_queue_size 512 -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100)
# -readrate 1 paces only the overlay input so it neither floods the queue nor lags edits.
ov_in(){ echo "-readrate 1 -thread_queue_size 512 -f image2 -pattern_type none -loop 1 -framerate $OV_FPS -i $D/overlay_$1.png"; }
# Live: the ingest HLS is already CFR at $FPS, so frames are re-timed by count (N) and
# passed through 1:1 (cfr then never needs to duplicate). An HLS timestamp jump/discontinuity can no longer make a cfr/fps
# stage repeat one stale frame forever (1.0.5 freeze).
FILTER_LIVE="[0:v]setpts=N/($FPS*TB),scale=$OUT_W:$OUT_H,format=yuv420p[base];[2:v]format=rgba,setpts=PTS-STARTPTS[ov];[base][ov]overlay=0:0:format=yuv420:eof_action=repeat:repeatlast=1:shortest=1,format=yuv420p[v]"
FILTER="[0:v]fps=$FPS,scale=$OUT_W:$OUT_H,setpts=PTS-STARTPTS[base];[2:v]format=rgba,setpts=PTS-STARTPTS[ov];[base][ov]overlay=0:0:format=yuv420:eof_action=repeat:repeatlast=1,format=yuv420p[v]"

ensure_assets(){
  [ -s "$D/caption.txt" ] || echo "Axmouth  |  Tide data loading  |  Not for navigation" > "$D/caption.txt"
  [ -s "$FALLBACK" ] || cp /opt/ayc/fallback.mp4 "$FALLBACK"
  for m in live offline; do
    [ -s "$D/overlay_$m.png" ] || python3 -c "from PIL import Image; Image.new('RGBA',($OUT_W,$OUT_H),(0,0,0,0)).save('$D/overlay_$m.png')"
  done
}
alive(){ [ "$(hls_age)" -lt "$HLS_MAX_AGE" ]; }
PROG="${HLS_DIR}/push_progress.log"
FREEZE_WINDOW="${FREEZE_WINDOW:-60}"
# Last "frame dup" pair from the push's progress lines (dup= is absent until the first dup)
progress(){ tail -n 20 "$PROG" 2>/dev/null | grep 'frame=' | tail -1 |
  sed -E 's/.*frame= *([0-9]+).*/\1 &/; s/^([0-9]+) .*dup= *([0-9]+).*/\1 \2/; s/^([0-9]+) [^0-9].*/\1 0/'; }

while true; do
  ensure_assets
  if alive; then
    log "LIVE: HLS fresh, pushing ${OUT_W}x${OUT_H}@${FPS} ${BITRATE_K}k"
    # shellcheck disable=SC2046
    # -shortest: when the HLS video ends (ingest restarted -> #EXT-X-ENDLIST) the push exits
    # and is restarted here, instead of cfr repeating the last frame forever (1.0.5 freeze).
    : > "$PROG"
    ffmpeg -nostdin -y -hide_banner -loglevel warning "${STATS[@]}" \
      -fflags +discardcorrupt+genpts -thread_queue_size 512 -live_start_index -3 -i "$HLS" "${SILENCE[@]}" $(ov_in live) \
      -filter_complex "$FILTER_LIVE" -map "[v]" -map 1:a -shortest "${ENC[@]}" "$URL" > >(redact | tee -a "$PROG") 2>&1 &
    PID=$!
    stale=0
    # Frozen-input watchdog: every 30 s compare progress with the sample from FREEZE_WINDOW ago
    t0=$(date +%s); hist=()
    while kill -0 "$PID" 2>/dev/null; do
      now=$(date +%s)
      if [ $((now - t0)) -ge 30 ]; then
        t0=$now
        read -r f1 d1 <<<"$(progress)"; hist+=("${f1:-0} ${d1:-0}")
        n=$(( FREEZE_WINDOW / 30 ))
        if [ "${#hist[@]}" -gt "$n" ]; then
          read -r f0 d0 <<<"${hist[$(( ${#hist[@]} - 1 - n ))]}"
          df=$(( ${f1:-0} - f0 )); dd=$(( ${d1:-0} - d0 ))
          if [ "$df" -le 0 ] || [ $((dd * 2)) -gt "$df" ]; then
            log "frozen input, restarting (frames +$df, dup +$dd in ${FREEZE_WINDOW}s)"
            kill "$PID" 2>/dev/null; sleep 1; kill -9 "$PID" 2>/dev/null
            break
          fi
        fi
      fi
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
    ffmpeg -nostdin -y -hide_banner -loglevel warning "${STATS[@]}" -stream_loop -1 -re -i "$FALLBACK" \
      "${SILENCE[@]}" $(ov_in offline) \
      -filter_complex "$FILTER" -map "[v]" -map 1:a -t 45 "${ENC[@]}" "$URL" 2>&1 | redact
  fi
  sleep 2
done
