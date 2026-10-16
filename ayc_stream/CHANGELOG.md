# Changelog

## 1.0.6
- Fix the frozen YouTube picture (push repeating one frame forever, `dup=` climbing,
  "More than 1000 frames duplicated"). Reproduced locally: when the ingest ffmpeg restarts
  it ends the HLS playlist (`#EXT-X-ENDLIST`). The push's camera video then hit EOF, but the
  overlay filter kept emitting frames driven by the endless overlay/silence inputs, and cfr
  duplicated the last camera frame. The HLS-freshness watchdog never fired because the new
  playlist was fresh.
  - Push overlay uses `shortest=1` and the output `-shortest`, so the push exits when the camera
    video ends and the loop restarts it about 2 s later.
  - Push re-times live frames by count (`setpts=N/(FPS*TB)`; the ingest HLS is already CFR) and
    reads HLS with `-fflags +discardcorrupt+genpts`, so timestamp jumps/discontinuities can't
    cause duplication.
  - New frozen-input watchdog: every 30 s it compares push progress with 60 s earlier. If the
    frame count stalls or more than half the new frames are duplicates, it logs
    `[push] frozen input, restarting` and restarts the push.
- Ingest: real-time timestamps are forced strictly increasing (`max(..., PREV_OUTPTS+1)`), and
  HLS uses `independent_segments+program_date_time+temp_file`.

## 1.0.5
- Fix ingest running at ~0.4x real time ("No data" / "not receiving enough video"). The AYC
  camera delivers about 6 fps whatever `&fps=` asks for, and the wallclock input timestamps
  were not taking effect on the add-on's ffmpeg (Alpine 3.20, ffmpeg 6.1). Each frame
  was then treated as 1/15 s, so 6 real fps became 0.4x. Frames are now re-stamped from the real-time
  clock in the filter graph (`settb=1/1000,setpts=(RTCTIME-RTCSTART)/1000`) before `fps=`, so
  frames are duplicated up to the configured fps and HLS runs at 1.0x regardless of camera rate.
- Removed `-fflags +genpts` from the camera input.

## 1.0.4
- Ingest made cheaper: MJPEG decode with `-threads 4`, scaling with `flags=fast_bilinear`,
  `-filter_threads 2`, x264 `-threads 2`, fps filter before scale (drops frames before scaling).
- New option `decode_lowres` (0-3, default 1): half-res MJPEG decode, applied only when the
  camera frame is at least 2x the output width (probed on each connect and logged).
- New option `camera_resolution` (e.g. `1280x720`): asks the Axis camera for a smaller
  stream (`?resolution=..&fps=..`). Cheapest fix for a slow Pi; the camera is 1920x1080 natively.
- Fallback clip recorder now stream-copies the HLS (no decode or re-encode), so it no longer
  fails as "short" when the Pi is busy.
- ffmpeg progress lines are prefixed with `[ingest]` / `[push]` / `[clip]`.

## 1.0.3
- Ingest: camera frames are timestamped by wall clock and output at a constant 15 fps
  (`-fps_mode cfr`), so HLS segments are real time even when the camera sends fewer or
  irregular frames. Keyframe every 2 s to match the 2 s segments.
- Push: removed `-re` on the HLS input (live HLS is already paced; double pacing starved
  YouTube, causing "No data" / "not receiving enough video"). Output forced to CFR at the
  configured fps, keyframe every 2 s, AAC stereo 128k 44.1 kHz silent track.
- Overlay PNG input: read at 1 fps with `-readrate 1` and `thread_queue_size 512` (fixes
  "Thread message queue blocking"); edits still appear within about 1 s.
- Logs: ffmpeg progress (`fps=`, `speed=`) every 30 s for ingest and push, with the stream
  key masked. Healthy is `speed≈1.0x` and `fps≈15`.

## 1.0.2
- Pace HLS input, cheaper yuv overlay, zerolatency.

## 1.0.1
- Install GNU sed (BusyBox lacks `sed -u`).
