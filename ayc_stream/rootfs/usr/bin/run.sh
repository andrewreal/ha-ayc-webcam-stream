#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# AYC Webcam Stream add-on entrypoint: reads options, supervises all workers.
export TZ=Europe/London AYC_DIR=/data HLS_DIR=/tmp/ayc_hls
STREAM_KEY="$(bashio::config 'stream_key')"
if [ -z "$STREAM_KEY" ] || [ "$STREAM_KEY" = "null" ]; then
  bashio::exit.nok "stream_key is not set - add your YouTube stream key in the Configuration tab"
fi
export STREAM_KEY
OUTPUT_HEIGHT="$(bashio::config 'output_height' 720)"; export OUTPUT_HEIGHT
FPS="$(bashio::config 'fps' 15)"; export FPS
BITRATE_K="$(bashio::config 'bitrate_k' 1500)"; export BITRATE_K
X264_PRESET="$(bashio::config 'x264_preset' ultrafast)"; export X264_PRESET
DECODE_LOWRES="$(bashio::config 'decode_lowres' 1)"; export DECODE_LOWRES
CAMERA_RESOLUTION="$(bashio::config 'camera_resolution' '')"; [ "$CAMERA_RESOLUTION" = "null" ] && CAMERA_RESOLUTION=""; export CAMERA_RESOLUTION
SHINGLE_AUTO="$(bashio::config 'shingle_auto' 'suggest')"; [ "$SHINGLE_AUTO" = "null" ] && SHINGLE_AUTO="suggest"; export SHINGLE_AUTO
mkdir -p /data "$HLS_DIR"
# Layout lives in the add-on config folder (HA: /addon_configs/<id>_ayc_stream/) so it can be edited with File editor/Samba
if [ -d /config ]; then export AYC_LAYOUT=/config/overlay.json; fi
[ -s /data/fallback.mp4 ] || cp /opt/ayc/fallback.mp4 /data/fallback.mp4
# Seed shingle outline into addon config (/config) before workers start.
# overlay.py also seeds (with embedded fallback); this covers the common path early.
if [ -d /config ] && [ ! -s /config/shingle_bar.json ]; then
  if [ -s /opt/ayc/shingle_bar.json ]; then
    cp /opt/ayc/shingle_bar.json /config/shingle_bar.json \
      && bashio::log.info "seeded /config/shingle_bar.json from /opt/ayc" \
      || bashio::log.warning "could not seed shingle_bar.json from /opt/ayc"
  else
    bashio::log.warning "/opt/ayc/shingle_bar.json missing from image — overlay.py will write embedded default"
  fi
fi
bashio::log.info "Starting AYC stream ${OUTPUT_HEIGHT}p@${FPS} ${BITRATE_K}k (preset ${X264_PRESET})"
exec /usr/bin/supervise.sh
