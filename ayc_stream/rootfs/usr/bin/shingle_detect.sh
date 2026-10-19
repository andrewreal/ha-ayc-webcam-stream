#!/bin/bash
# Periodically try to update the shingle-bar outline near daylight low tide.
LOG_TAG=shingle
# shellcheck source=/dev/null
. /usr/bin/common.sh
MODE="${SHINGLE_AUTO:-suggest}"
if [ "$MODE" = "off" ] || [ "$MODE" = "null" ] || [ -z "$MODE" ]; then
  log "SHINGLE_AUTO=$MODE — detector idle"
  while true; do sleep 3600; done
fi
# Layout / shingle live in /config when present
[ -d /config ] && export AYC_LAYOUT="${AYC_LAYOUT:-/config/overlay.json}"
export AYC_SHINGLE="${AYC_SHINGLE:-${AYC_LAYOUT%/*}/shingle_bar.json}"
export AYC_SHINGLE_CANDIDATES="${AYC_SHINGLE_CANDIDATES:-${AYC_SHINGLE%/*}/shingle_candidates}"
export SHINGLE_AUTO="$MODE"
log "detector mode=$MODE outline=$AYC_SHINGLE"
# Check every 10 minutes; the script itself enforces tide/daylight/spacing gates
while true; do
  python3 -u /usr/bin/shingle_detect.py || true
  sleep 600
done
