#!/bin/bash
# shellcheck disable=SC2034
# Sole Axis reader -> local HLS window (relay + clip recorder).
LOG_TAG=ingest
# shellcheck source=/dev/null
. /usr/bin/common.sh
CAM="${CAMERA_URL:-https://webcam.aycwebcam.co.uk/axis-cgi/mjpg/video.cgi}"
if [ -n "$CAM_RES" ]; then  # Axis VAPIX: ask the camera for a smaller stream
  case "$CAM" in *\?*) CAM="$CAM&resolution=$CAM_RES&fps=$FPS";; *) CAM="$CAM?resolution=$CAM_RES&fps=$FPS";; esac
fi
mkdir -p "$HLS_DIR"
while true; do
  rm -f "$HLS_DIR"/*.ts "$HLS_DIR"/*.m3u8
  # Half/quarter-res MJPEG decode (-lowres) only when the source is >= 2x the output width
  SRC_W=$(timeout 20 ffprobe -v error -tls_verify 0 -f mjpeg -select_streams v:0 -show_entries stream=width \
          -of csv=p=0 "$CAM" 2>/dev/null | head -1)
  LR=0
  if [ "${LOWRES:-0}" -gt 0 ] && [ "${SRC_W:-0}" -ge $((OUT_W * 2)) ]; then LR=$LOWRES; fi
  log "connecting to camera (${SRC_W:-?}px wide, lowres=$LR) -> HLS ${OUT_W}x${OUT_H}@${FPS}"
  # Timestamps are re-stamped from the real-time clock inside the filter graph (settb/setpts
  # RTCTIME), so the fps filter duplicates frames up to $FPS and HLS runs at real time even
  # when the camera sends ~6 fps, whatever the demuxer/ffmpeg version does with wallclock.
  # max(.., PREV_OUTPTS+1) keeps pts strictly increasing if the system clock steps back.
  # -tls_verify 0: camera certificate has expired
  ffmpeg -nostdin -hide_banner -loglevel warning "${STATS[@]}" -tls_verify 0 -rw_timeout 15000000 \
    -threads 4 -lowres "$LR" -f mjpeg -use_wallclock_as_timestamps 1 -i "$CAM" \
    -an -filter_threads 2 -vf "settb=1/1000,setpts=max((RTCTIME-RTCSTART)/1000\,if(isnan(PREV_OUTPTS)\,0\,PREV_OUTPTS+1)),fps=$FPS,scale=$OUT_W:$OUT_H:flags=fast_bilinear,format=yuv420p" -fps_mode cfr -r "$FPS" \
    -c:v libx264 -preset ultrafast -tune zerolatency -threads 2 -b:v $((BITRATE_K * 2))k \
    -g $((FPS * 2)) -keyint_min $((FPS * 2)) -sc_threshold 0 \
    -f hls -hls_time 2 -hls_list_size 8 -hls_flags delete_segments+append_list+independent_segments+program_date_time+temp_file \
    -hls_segment_filename "$HLS_DIR/seg%03d.ts" "$HLS" 2>&1 | redact
  log "exited; retry in 3s"
  sleep 3
done
