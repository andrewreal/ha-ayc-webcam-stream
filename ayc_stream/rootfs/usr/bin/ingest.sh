#!/bin/bash
# shellcheck disable=SC2034
# Sole Axis reader -> local HLS window (relay + clip recorder).
LOG_TAG=ingest
# shellcheck source=/dev/null
. /usr/bin/common.sh
CAM="${CAMERA_URL:-https://webcam.aycwebcam.co.uk/axis-cgi/mjpg/video.cgi}"
mkdir -p "$HLS_DIR"
while true; do
  rm -f "$HLS_DIR"/*.ts "$HLS_DIR"/*.m3u8
  log "connecting to camera -> HLS ${OUT_W}x${OUT_H}@${FPS}"
  # -tls_verify 0: camera certificate has expired
  ffmpeg -nostdin -hide_banner -loglevel warning -tls_verify 0 -rw_timeout 15000000 \
    -f mjpeg -use_wallclock_as_timestamps 1 -i "$CAM" \
    -an -vf "fps=$FPS,scale=$OUT_W:$OUT_H,format=yuv420p" \
    -c:v libx264 -preset ultrafast -tune zerolatency -b:v $((BITRATE_K * 2))k \
    -g "$FPS" -keyint_min "$FPS" -sc_threshold 0 \
    -f hls -hls_time 2 -hls_list_size 8 -hls_flags delete_segments+append_list \
    -hls_segment_filename "$HLS_DIR/seg%03d.ts" "$HLS" 2>&1
  log "exited; retry in 3s"
  sleep 3
done
