#!/bin/bash
# Keep every worker running; restart any that dies. Exit cleanly on SIGTERM.
WORKERS=(caption.sh overlay.sh ingest.sh record_clip.sh push.sh shingle_detect.sh)
declare -A PIDS
start(){ setsid "/usr/bin/$1" & PIDS[$1]=$!; echo "$(date '+%F %T %Z') [supervisor] started $1 (pid ${PIDS[$1]})"; }
stop_all(){
  echo "$(date '+%F %T %Z') [supervisor] stopping"
  for w in "${WORKERS[@]}"; do kill -- -"${PIDS[$w]}" 2>/dev/null; done
  sleep 2; pkill -9 ffmpeg 2>/dev/null; exit 0
}
trap stop_all TERM INT
for w in "${WORKERS[@]}"; do start "$w"; [ "$w" = ingest.sh ] && sleep 6; done
while true; do
  for w in "${WORKERS[@]}"; do
    kill -0 "${PIDS[$w]}" 2>/dev/null || { echo "$(date '+%F %T %Z') [supervisor] $w died; restarting"; start "$w"; }
  done
  sleep 5 & wait $!
done
