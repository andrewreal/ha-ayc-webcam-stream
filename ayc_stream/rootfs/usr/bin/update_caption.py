#!/usr/bin/env python3
"""Fetch Axmouth-area tide extremes and write caption.txt + tide_chart.png.

Primary: UKHO Admiralty EasyTide Lyme Regis (station 0028) — nearest free
official station to Axmouth harbour (~8.7 km). UKHO has no Axmouth station;
eOceanic and similar sites publish Lyme Regis times for Axmouth.

Fallback: OpenWaters / Slackwater nearest harmonics (West Bay, ~20 km).

Cache refreshed at most once per ~20 hours. EasyTide endpoints are the same
unauthenticated JSON URLs the public EasyTide web app uses. Height samples
(tidalHeightOccurrenceList, every 30 min) are cached for the on-screen chart.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

DIR = Path(os.environ.get("AYC_DIR", "/data"))
CACHE = DIR / "tides_cache.json"
CAPTION = DIR / "caption.txt"
TMP = DIR / "caption.txt.tmp"
CHART = DIR / "tide_chart.png"
CHART_TMP = DIR / "tide_chart.png.tmp"
LOG = DIR / "caption.log"

# Axmouth harbour entrance (camera location label)
LAT, LON = 50.705, -3.055
TZ = ZoneInfo("Europe/London")

# EasyTide (UKHO) — Lyme Regis is the closest listed station to Axmouth
EASYTIDE_STATION_ID = "0028"
EASYTIDE_STATION_NAME = "Lyme Regis"
EASYTIDE_URL = (
    "https://easytide.admiralty.co.uk/Home/GetPredictionData"
    f"?stationId={EASYTIDE_STATION_ID}"
)
EASYTIDE_SOURCE = (
    "https://easytide.admiralty.co.uk/Home/GetPredictionData"
    f"?stationId={EASYTIDE_STATION_ID} (UKHO EasyTide / {EASYTIDE_STATION_NAME})"
)

OPENWATERS_URL = (
    f"https://api.openwaters.io/tides/extremes?latitude={LAT}&longitude={LON}"
)
OPENWATERS_SOURCE = (
    "https://api.openwaters.io/tides/extremes (OpenWaters / Slackwater)"
)

CACHE_MAX_AGE_H = 20  # refresh at least daily
LOOP_S = 60  # keeps the on-screen clock current
UA = "AYC-harbour-cam/1.0 (+harbour webcam tide caption; daily cache)"

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Chart geometry (content 420x220; extra transparent bottom pad lifts the
# drawn panel when ffmpeg overlays at bottom-right without a filter change)
CHART_W, CHART_CONTENT_H = 560, 300
CHART_BOTTOM_PAD = 0  # unused; chart sits top-right
CHART_H = CHART_CONTENT_H + CHART_BOTTOM_PAD
CHART_PAD_L, CHART_PAD_R = 42, 14
CHART_PAD_T, CHART_PAD_B = 28, 36


def log(msg: str) -> None:
    line = f"{datetime.now(TZ).isoformat(timespec='seconds')} {msg}\n"
    # Add-on: log to stdout (Supervisor log)
    sys.stdout.write("[caption] " + line)
    sys.stdout.flush()


def http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _parse_easytide_dt(dt_raw: str) -> datetime:
    if dt_raw.endswith("Z") or "+" in dt_raw[10:]:
        iso = dt_raw.replace("Z", "+00:00")
    else:
        iso = dt_raw + "+00:00"
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def fetch_easytide() -> dict:
    """Return normalised cache payload from EasyTide Lyme Regis."""
    raw = http_json(EASYTIDE_URL)
    events = raw.get("tidalEventList") or []
    if len(events) < 4:
        raise RuntimeError(f"EasyTide returned too few events ({len(events)})")

    extremes = []
    for e in events:
        # eventType: 0 = High Water, 1 = Low Water (EasyTide convention)
        et = e.get("eventType")
        high = et == 0
        t = _parse_easytide_dt(e["dateTime"])
        height = float(e["height"])
        extremes.append(
            {
                "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "level": height,
                "high": high,
                "low": not high,
                "label": "High" if high else "Low",
            }
        )

    heights = []
    for o in raw.get("tidalHeightOccurrenceList") or []:
        t = _parse_easytide_dt(o["dateTime"])
        heights.append(
            {
                "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "level": float(o["height"]),
            }
        )

    return {
        "fetched_at": datetime.now(TZ).isoformat(),
        "station": {
            "id": EASYTIDE_STATION_ID,
            "name": EASYTIDE_STATION_NAME,
            "locality": EASYTIDE_STATION_NAME,
            "latitude": 50.7167,
            "longitude": -2.9333,
        },
        "distance_km": 8.7,
        "datum": "Chart Datum (UKHO)",
        "units": "m",
        "extremes": extremes,
        "heights": heights,
        "source": EASYTIDE_SOURCE,
    }


def fetch_openwaters() -> dict:
    """Fallback: OpenWaters nearest station (typically West Bay)."""
    payload = http_json(OPENWATERS_URL)
    return {
        "fetched_at": datetime.now(TZ).isoformat(),
        "station": {
            "id": payload["station"]["id"],
            "name": payload["station"].get("name"),
            "locality": payload["station"].get("locality"),
            "latitude": payload["station"].get("latitude"),
            "longitude": payload["station"].get("longitude"),
        },
        "distance_km": payload.get("distance"),
        "datum": payload.get("datum"),
        "units": payload.get("units"),
        "extremes": payload["extremes"],
        "heights": [],
        "source": OPENWATERS_SOURCE,
    }


def load_cache() -> dict | None:
    if not CACHE.exists():
        return None
    try:
        data = json.loads(CACHE.read_text())
        fetched = datetime.fromisoformat(data["fetched_at"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=TZ)
        if datetime.now(TZ) - fetched > timedelta(hours=CACHE_MAX_AGE_H):
            return None
        now = datetime.now(timezone.utc)
        future = [
            e
            for e in data["extremes"]
            if datetime.fromisoformat(e["time"].replace("Z", "+00:00")) > now
        ]
        if len(future) < 2:
            return None
        return data
    except Exception as e:
        log(f"cache read error: {e}")
        return None


def save_cache(data: dict) -> None:
    CACHE.write_text(json.dumps(data, indent=2))


def parse_extremes(data: dict) -> list[tuple[datetime, float, str]]:
    out = []
    for e in data["extremes"]:
        t = datetime.fromisoformat(e["time"].replace("Z", "+00:00")).astimezone(TZ)
        kind = "HW" if e.get("high") or e.get("label", "").lower().startswith("high") else "LW"
        out.append((t, float(e["level"]), kind))
    out.sort(key=lambda x: x[0])
    return out


def parse_heights(data: dict) -> list[tuple[datetime, float]]:
    out = []
    for h in data.get("heights") or []:
        t = datetime.fromisoformat(h["time"].replace("Z", "+00:00")).astimezone(TZ)
        out.append((t, float(h["level"])))
    out.sort(key=lambda x: x[0])
    return out


def cosine_heights(
    extremes: list[tuple[datetime, float, str]], t0: datetime, t1: datetime, step_min: int = 15
) -> list[tuple[datetime, float]]:
    """Build a smooth curve between HW/LW extremes (OpenWaters fallback)."""
    if len(extremes) < 2:
        return []
    samples: list[tuple[datetime, float]] = []
    t = t0
    step = timedelta(minutes=step_min)
    while t <= t1:
        # find bracketing extremes
        prev = None
        nxt = None
        for e in extremes:
            if e[0] <= t:
                prev = e
            elif e[0] > t and nxt is None:
                nxt = e
                break
        if prev is None:
            h = extremes[0][1]
        elif nxt is None:
            h = prev[1]
        else:
            span = (nxt[0] - prev[0]).total_seconds()
            if span <= 0:
                h = prev[1]
            else:
                frac = (t - prev[0]).total_seconds() / span
                # classic cosine tide interpolation
                h = (prev[1] + nxt[1]) / 2 + (prev[1] - nxt[1]) / 2 * math.cos(math.pi * frac)
        samples.append((t, h))
        t += step
    return samples


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def fmt_clock(now: datetime) -> str:
    # e.g. Thu 15 Oct 16:30 BST
    tzname = now.tzname() or "BST"
    return now.strftime(f"%a %-d %b %H:%M {tzname}")


def build_caption(extremes: list[tuple[datetime, float, str]], now: datetime) -> str:
    nxt = None
    for e in extremes:
        if e[0] > now:
            nxt = e
            break

    if nxt is None:
        state = "Tide data unavailable"
        next_bits = ""
    else:
        state = "Rising" if nxt[2] == "HW" else "Falling"
        next_hw = next((e for e in extremes if e[0] > now and e[2] == "HW"), None)
        next_lw = next((e for e in extremes if e[0] > now and e[2] == "LW"), None)
        parts = []
        if next_hw:
            parts.append(f"HW {fmt_time(next_hw[0])} {next_hw[1]:.1f}m")
        if next_lw:
            parts.append(f"LW {fmt_time(next_lw[0])} {next_lw[1]:.1f}m")
        next_bits = "  ".join(parts)

    clock = fmt_clock(now)
    caption = (
        f"Axmouth  {clock}  |  {state}"
        + (f"  |  {next_bits}" if next_bits else "")
        + "  |  Not for navigation"
    )
    return caption


def write_caption(text: str) -> None:
    TMP.write_text(text + "\n", encoding="utf-8")
    os.replace(TMP, CAPTION)


def _font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()



def write_chart_image(img: Image.Image) -> None:
    """Write tide_chart.png, preferring in-place overwrite of the existing inode.

    ffmpeg -loop 1 keeps an open fd; os.replace() would leave it on a deleted
    inode so live overlays would never see updates. In-place truncate+write
    keeps the same inode. Falls back to atomic replace if needed.
    """
    out_w = int(os.environ.get("CHART_OUT_W", "0") or 0)
    out_h = int(os.environ.get("CHART_OUT_H", "0") or 0)
    if out_w > 0 and out_h > 0 and img.size != (out_w, out_h):
        # Scale proportionally for 720p output (560x300 @1080p -> 373x200 @720p)
        img = img.resize((out_w, out_h), Image.LANCZOS)
    img.save(CHART_TMP, "PNG")
    data = CHART_TMP.read_bytes()
    if CHART.exists():
        try:
            with CHART.open("r+b") as f:
                f.seek(0)
                f.write(data)
                f.truncate(len(data))
                f.flush()
                os.fsync(f.fileno())
            CHART_TMP.unlink(missing_ok=True)
            return
        except Exception as e:
            log(f"chart in-place write failed, falling back to replace: {e}")
    os.replace(CHART_TMP, CHART)

def render_tide_chart(
    data: dict,
    extremes: list[tuple[datetime, float, str]],
    now: datetime,
) -> None:
    """Draw a ~24h semi-transparent tide panel and atomically write tide_chart.png."""
    # Window: 6h before now → 18h after (24h total), Europe/London
    t0 = now - timedelta(hours=6)
    t1 = now + timedelta(hours=18)

    heights = parse_heights(data)
    if heights:
        curve = [(t, h) for t, h in heights if t0 <= t <= t1]
        # densify a little with linear interp if sparse gaps
        if len(curve) < 4:
            curve = cosine_heights(extremes, t0, t1, step_min=15)
    else:
        curve = cosine_heights(extremes, t0, t1, step_min=15)

    if len(curve) < 2:
        # empty placeholder so ffmpeg still has a file
        img = Image.new("RGBA", (CHART_W, CHART_CONTENT_H), (0, 0, 0, 160))
        draw = ImageDraw.Draw(img)
        draw.text((12, 12), "Tide chart unavailable", fill=(255, 255, 255, 220), font=_font(FONT_REG, 14))
        if CHART_BOTTOM_PAD > 0:
            padded = Image.new("RGBA", (CHART_W, CHART_H), (0, 0, 0, 0))
            padded.paste(img, (0, 0))
            img = padded
        write_chart_image(img)
        return

    # Height range with padding
    levels = [h for _, h in curve]
    for e in extremes:
        if t0 <= e[0] <= t1:
            levels.append(e[1])
    h_min = min(levels) - 0.25
    h_max = max(levels) + 0.35
    if h_max - h_min < 1.5:
        mid = (h_min + h_max) / 2
        h_min, h_max = mid - 0.75, mid + 0.75

    plot_x0 = CHART_PAD_L
    plot_y0 = CHART_PAD_T
    plot_x1 = CHART_W - CHART_PAD_R
    plot_y1 = CHART_CONTENT_H - CHART_PAD_B
    plot_w = plot_x1 - plot_x0
    plot_h = plot_y1 - plot_y0
    span_s = (t1 - t0).total_seconds() or 1.0

    def x_of(t: datetime) -> float:
        return plot_x0 + ((t - t0).total_seconds() / span_s) * plot_w

    def y_of(h: float) -> float:
        return plot_y1 - ((h - h_min) / (h_max - h_min)) * plot_h

    img = Image.new("RGBA", (CHART_W, CHART_CONTENT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Panel background
    draw.rounded_rectangle(
        [(0, 0), (CHART_W - 1, CHART_CONTENT_H - 1)],
        radius=10,
        fill=(8, 16, 28, 175),
        outline=(120, 180, 220, 140),
        width=1,
    )

    font_sm = _font(FONT_REG, 11)
    font_md = _font(FONT_BOLD, 12)
    font_tiny = _font(FONT_REG, 10)

    # Title
    station = (data.get("station") or {}).get("locality") or "Lyme Regis"
    draw.text((10, 6), f"Tide · Axmouth ({station})", fill=(220, 235, 255, 230), font=font_md)
    draw.text(
        (CHART_W - 118, 7),
        "Not for navigation",
        fill=(200, 180, 120, 200),
        font=font_tiny,
    )

    # Grid / height ticks
    nice_steps = [0.5, 1.0, 0.25]
    step = 0.5
    for s in nice_steps:
        n = int((h_max - h_min) / s) + 1
        if 3 <= n <= 7:
            step = s
            break
    first = math.ceil(h_min / step) * step
    h_tick = first
    while h_tick <= h_max + 1e-6:
        y = y_of(h_tick)
        draw.line([(plot_x0, y), (plot_x1, y)], fill=(80, 110, 140, 90), width=1)
        label = f"{h_tick:.1f}"
        bbox = draw.textbbox((0, 0), label, font=font_tiny)
        tw = bbox[2] - bbox[0]
        draw.text((plot_x0 - tw - 6, y - 6), label, fill=(180, 200, 220, 210), font=font_tiny)
        h_tick += step

    # Metres unit
    draw.text((4, plot_y0 - 2), "m", fill=(160, 180, 200, 180), font=font_tiny)

    # Time ticks every 6 hours, plus ends
    tick_hours = []
    # align to clock hours
    cursor = t0.replace(minute=0, second=0, microsecond=0)
    if cursor < t0:
        cursor += timedelta(hours=1)
    while cursor <= t1:
        if cursor.hour % 6 == 0 or cursor == t0.replace(minute=0, second=0, microsecond=0):
            tick_hours.append(cursor)
        cursor += timedelta(hours=1)
    # ensure a few labels
    if len(tick_hours) < 3:
        tick_hours = [t0 + timedelta(hours=h) for h in (0, 6, 12, 18, 24) if t0 + timedelta(hours=h) <= t1]

    for tt in tick_hours:
        x = x_of(tt)
        if x < plot_x0 - 1 or x > plot_x1 + 1:
            continue
        draw.line([(x, plot_y0), (x, plot_y1)], fill=(80, 110, 140, 70), width=1)
        lbl = tt.strftime("%H:%M")
        bbox = draw.textbbox((0, 0), lbl, font=font_tiny)
        tw = bbox[2] - bbox[0]
        draw.text((x - tw / 2, plot_y1 + 4), lbl, fill=(180, 200, 220, 210), font=font_tiny)

    # Day label under axis
    day_lbl = now.strftime("%a %-d %b")
    draw.text((plot_x0, CHART_CONTENT_H - 16), day_lbl, fill=(160, 180, 200, 180), font=font_tiny)

    # Curve fill under line
    pts = [(x_of(t), y_of(h)) for t, h in curve]
    if len(pts) >= 2:
        fill_pts = [(pts[0][0], plot_y1)] + pts + [(pts[-1][0], plot_y1)]
        draw.polygon(fill_pts, fill=(40, 160, 200, 55))
        draw.line(pts, fill=(80, 220, 255, 240), width=2)

    # Mark HW/LW in window
    next_hw = next((e for e in extremes if e[0] > now and e[2] == "HW"), None)
    next_lw = next((e for e in extremes if e[0] > now and e[2] == "LW"), None)
    for e in extremes:
        if not (t0 <= e[0] <= t1):
            continue
        x, y = x_of(e[0]), y_of(e[1])
        is_next = e is next_hw or e is next_lw
        r = 4 if is_next else 3
        colour = (255, 220, 90, 255) if e[2] == "HW" else (140, 200, 255, 255)
        if is_next:
            colour = (255, 240, 120, 255) if e[2] == "HW" else (160, 220, 255, 255)
        draw.ellipse([(x - r, y - r), (x + r, y + r)], fill=colour, outline=(255, 255, 255, 200))
        if is_next:
            tag = f"{e[2]} {fmt_time(e[0])} {e[1]:.1f}m"
            bbox = draw.textbbox((0, 0), tag, font=font_sm)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            tx = min(max(x - tw / 2, 4), CHART_W - tw - 4)
            ty = y - th - 10 if e[2] == "HW" else y + 8
            if ty < 22:
                ty = y + 8
            if ty + th > plot_y1 - 2:
                ty = y - th - 10
            draw.rectangle([(tx - 2, ty - 1), (tx + tw + 2, ty + th + 1)], fill=(0, 0, 0, 140))
            draw.text((tx, ty), tag, fill=(255, 255, 255, 245), font=font_sm)

    # "Now" vertical line
    nx = x_of(now)
    if plot_x0 <= nx <= plot_x1:
        draw.line([(nx, plot_y0), (nx, plot_y1)], fill=(255, 120, 80, 220), width=2)
        # triangle at top
        draw.polygon(
            [(nx, plot_y0 - 2), (nx - 5, plot_y0 - 10), (nx + 5, plot_y0 - 10)],
            fill=(255, 120, 80, 230),
        )
        now_lbl = "now"
        bbox = draw.textbbox((0, 0), now_lbl, font=font_tiny)
        tw = bbox[2] - bbox[0]
        draw.text((nx - tw / 2, plot_y0 - 22), now_lbl, fill=(255, 160, 120, 230), font=font_tiny)

    # Transparent bottom pad so bottom-right overlay sits the panel higher
    if CHART_BOTTOM_PAD > 0:
        padded = Image.new("RGBA", (CHART_W, CHART_H), (0, 0, 0, 0))
        padded.paste(img, (0, 0))
        img = padded

    write_chart_image(img)


def ensure_data() -> tuple[dict, list[tuple[datetime, float, str]]]:
    data = load_cache()
    # Refresh if cache lacks height samples (older cache from before chart support)
    if data is not None and not data.get("heights"):
        log("cache missing heights; refreshing for tide chart")
        data = None

    if data is not None:
        return data, parse_extremes(data)

    # Prefer EasyTide Lyme Regis; fall back to OpenWaters West Bay
    errors: list[str] = []
    for name, fetcher in (
        ("EasyTide Lyme Regis", fetch_easytide),
        ("OpenWaters", fetch_openwaters),
    ):
        try:
            log(f"fetching tides from {name}")
            payload = fetcher()
            save_cache(payload)
            data = load_cache()
            if data is None:
                raise RuntimeError("cache empty after fetch")
            log(
                f"cached {len(data['extremes'])} extremes "
                f"heights={len(data.get('heights') or [])} "
                f"station={data['station'].get('locality') or data['station'].get('name')} "
                f"dist_km={data.get('distance_km')} "
                f"source={data.get('source')}"
            )
            return data, parse_extremes(data)
        except Exception as e:
            errors.append(f"{name}: {e}")
            log(f"fetch failed ({name}): {e}")

    raise RuntimeError("; ".join(errors) or "all tide sources failed")


def once() -> str:
    data, extremes = ensure_data()
    now = datetime.now(TZ)
    text = build_caption(extremes, now)
    write_caption(text)
    try:
        if os.environ.get("AYC_LEGACY_CHART") == "1":  # overlay.py draws the chart now
            render_tide_chart(data, extremes, now)
    except Exception as e:
        log(f"chart render error: {e}")
    return text


def main() -> int:
    loop = "--once" not in sys.argv
    while True:
        try:
            text = once()
            log(f"caption: {text}")
        except Exception as e:
            log(f"ERROR: {e}")
            if not CAPTION.exists():
                now = datetime.now(TZ)
                write_caption(
                    f"Axmouth  {fmt_clock(now)}  |  Tide data unavailable  |  Not for navigation"
                )
        if not loop:
            return 0
        time.sleep(LOOP_S)


if __name__ == "__main__":
    raise SystemExit(main())
