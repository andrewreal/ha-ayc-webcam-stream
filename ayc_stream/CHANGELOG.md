# Changelog

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
