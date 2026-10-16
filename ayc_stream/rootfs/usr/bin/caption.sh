#!/bin/bash
# shellcheck disable=SC2034
LOG_TAG=caption
# shellcheck source=/dev/null
. /usr/bin/common.sh
while true; do
  python3 -u /usr/bin/update_caption.py
  log "update_caption.py exited; restart in 10s"; sleep 10
done
