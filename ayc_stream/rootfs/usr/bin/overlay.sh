#!/bin/bash
# shellcheck disable=SC2034
LOG_TAG=overlay
# shellcheck source=/dev/null
. /usr/bin/common.sh
export OUTPUT_HEIGHT="$OUT_H"
while true; do
  python3 -u /usr/bin/overlay.py
  log "overlay.py exited; restart in 5s"; sleep 5
done
