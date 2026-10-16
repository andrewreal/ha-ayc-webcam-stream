# AYC Webcam Stream

Streams the Axe Yacht Club harbour webcam to YouTube Live at 720p / 15 fps (~1.5 Mbps)
with a live tide caption, the "Axe Yacht Club" label, a tide chart (top right) and an
automatic fallback loop with an "camera offline" notice when the camera drops.

## Install
1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories**, add `https://github.com/andrewreal/ha-ayc-webcam-stream`
   (or copy the `ayc_stream` folder into `/addons` on the Pi via the Samba/SSH add-on,
   then ⋮ → **Check for updates** and it appears under *Local add-ons*).
2. Open **AYC Webcam Stream** → **Install** (builds locally on the Pi; takes a few minutes).
3. **Configuration** tab: paste your YouTube stream key into `stream_key`. Save.
4. **Info** tab: turn on **Start on boot** and **Watchdog**, then **Start**.
5. Check the **Log** tab for `LIVE: HLS fresh, pushing 1280x720@15`. In YouTube Studio the
   stream goes live (or press Go live if auto-start is off).

## Options
| Option | Default | Notes |
|---|---|---|
| stream_key | – | YouTube stream key (required, kept secret) |
| output_height | 720 | 720 recommended on a Pi 4 |
| fps | 15 | camera only delivers ~1–2 fps |
| bitrate_k | 1500 | video bitrate in kbps |
| x264_preset | ultrafast | `veryfast` gives better quality if CPU allows |

## Notes
- Tides: UKHO EasyTide Lyme Regis (0028), OpenWaters fallback. Not for navigation.
- Fallback clip refreshes every 20 min into `/data/fallback.mp4`.
- Keep an eye on Pi temperature (Settings → System → Hardware); use a heatsink/fan case.
- Upload needs ~1.6 Mbps continuously (~0.5 TB/month).

## Editing overlays live (no restart)
All graphics (club name, tide caption, tide chart, offline notice, any logos) are drawn
into one transparent full-frame PNG by `overlay.py` every 2 s, from the layout file
**`overlay.json` in the add-on config folder** – in Home Assistant that is
`/addon_configs/<id>_ayc_stream/overlay.json` (open it with the File editor, Studio Code
Server or Samba add-on). It's seeded from the default on first start.
ffmpeg re-reads the PNG continuously, so saving the JSON changes the stream within ~2–3 s.
If the JSON is invalid the last good layout is kept and an error is logged.
Delete the file and restart to go back to the default.

Coordinates use a 1280×720 reference and scale to the output. Negative `x`/`y` count from
the right/bottom edge; `"center"` or `"center+40"` centres. `show`: `always`, `live`
(camera up) or `offline` (fallback only). `"enabled": false` hides an element.

Element types:
```json
{"id":"club","type":"text","text":"Axe Yacht Club","x":16,"y":16,"font":"bold","size":22,
 "color":"#FFFFFF","box":"#000000B0","pad":8}
{"id":"caption","type":"text","text_file":"caption.txt","x":16,"y":-16,"size":21,"max_width":1248}
{"id":"clock","type":"text","text":"%H:%M","strftime":true,"x":-16,"y":-60}
{"id":"chart","type":"chart","x":-16,"y":16,"w":400,"h":220,"font_scale":1.0}
{"id":"logo","type":"image","path":"logo.png","x":-16,"y":-60,"h":60,"opacity":0.9}
```
Fonts: `bold`, `regular`, `mono`, or a path to a .ttf. Colours: `#RRGGBB` or `#RRGGBBAA`.

**Custom PNGs:** drop the file next to `overlay.json` in the add-on config folder, or into
`/share/ayc_overlays/` (Samba: the `share` folder), and add an `image` element with its file name as `path`.
Image changes are picked up live too.

## Checking stream health (1.0.3+)
Every 30 s the log shows a progress line for `[ingest]` and `[push]`, e.g.
`frame= 970 fps= 15 ... speed=1.00x`. Both should sit at about `speed=1.0x`. A push speed
well below 1.0 means YouTube gets too little video. See CHANGELOG.md for version notes.

## Camera resolution and CPU (1.0.4+)
The AYC camera is an Axis camera with a 1920x1080 native image. Decoding 1080p MJPEG is the
most expensive step on a Pi. The camera (Axis VAPIX) can scale the stream itself. The
add-on appends the parameters to the camera URL:
`.../axis-cgi/mjpg/video.cgi?resolution=1280x720&fps=15`.
Set **camera_resolution** to `1280x720` (matches 720p output) or `640x360` for the lightest load.
Supported sizes include 1920x1080, 1280x720, 1024x640, 800x450, 640x360, 480x270 and 320x180.
Snapshots work the same way: `.../axis-cgi/jpg/image.cgi?resolution=640x360`.

**decode_lowres** (default 1) decodes the MJPEG at 1/2 (1), 1/4 (2) or 1/8 (3) size. It only
applies when the camera frame is at least twice the output width, e.g. 1080p camera with
`output_height` 540 or lower, or a 4K camera at 720p. The `[ingest] connecting ...` log line shows the
detected width and whether lowres is in use.

## Camera frame rate (1.0.5+)
The AYC camera sends about 6 frames/s (measured at 1280x720; `&fps=15` is ignored). The add-on
stamps frames with real time and repeats them to reach the `fps` option, so the stream stays
real time. Setting `fps` to 6-8 saves CPU and bandwidth with no visible loss.
