# Changelog

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
