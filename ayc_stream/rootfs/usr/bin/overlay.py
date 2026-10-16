#!/usr/bin/env python3
"""Render full-frame transparent overlays from a data-driven layout.

Writes (atomically) $AYC_DIR/overlay_live.png and overlay_offline.png every
OVERLAY_INTERVAL seconds. ffmpeg reads them with `-f image2 -loop 1`, which
re-opens the file for every frame, so changes appear without a restart.
Layout: $AYC_DIR/overlay.json (seeded from /opt/ayc/overlay.default.json),
reloaded every refresh. Image elements may reference files in /data or /share.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_caption as uc  # noqa: E402  (tide data helpers)

DIR = Path(os.environ.get("AYC_DIR", "/data"))
DEFAULT = Path(os.environ.get("AYC_DEFAULT_LAYOUT", "/opt/ayc/overlay.default.json"))
LAYOUT = Path(os.environ.get("AYC_LAYOUT", str(DIR / "overlay.json")))
OUT_H = int(os.environ.get("OUTPUT_HEIGHT", "720"))
OUT_W = (OUT_H * 16 // 9 + 1) // 2 * 2
INTERVAL = float(os.environ.get("OVERLAY_INTERVAL", "2"))
SEARCH = [LAYOUT.parent, DIR, Path("/share/ayc_overlays"), Path("/share")]
FONTS = {
    "bold": ["/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "regular": ["/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "mono": ["/usr/share/fonts/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
}
_font_cache: dict = {}
_last_err = ""


def log(msg: str) -> None:
    print(f"{datetime.now(uc.TZ):%Y-%m-%d %H:%M:%S %Z} [overlay] {msg}", flush=True)


def font(name: str, size: int):
    key = (name, size)
    if key not in _font_cache:
        cands = FONTS.get(name, [name])
        f = None
        for c in cands:
            try:
                f = ImageFont.truetype(c, size)
                break
            except Exception:
                pass
        _font_cache[key] = f or ImageFont.load_default()
    return _font_cache[key]


def colour(v, default=(255, 255, 255, 255)):
    if not v:
        return default
    if isinstance(v, (list, tuple)):
        return tuple(v) + ((255,) if len(v) == 3 else ())
    v = v.lstrip("#")
    if len(v) == 6:
        v += "FF"
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4, 6))


def find_file(name: str) -> Path | None:
    p = Path(name)
    if p.is_absolute():
        return p if p.exists() else None
    for d in SEARCH:
        if (d / p).exists():
            return d / p
    return None


def place(val, size, total, sc):
    """x/y: number (neg = from far edge), 'center', or 'center+N'."""
    if isinstance(val, str):
        off = 0.0
        if val.startswith("center"):
            rest = val[6:]
            off = float(rest) * sc if rest else 0.0
            return int((total - size) / 2 + off)
        val = float(val)
    val = float(val) * sc
    return int(total - size + val) if val < 0 else int(val)


# ---------------------------------------------------------------- tide chart
def tide_data():
    data = uc.load_cache()
    if not data:
        return None, []
    return data, uc.parse_extremes(data)


def render_chart(w: int, h: int, el: dict) -> Image.Image:
    now = datetime.now(uc.TZ)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=max(6, h // 22),
                        fill=colour(el.get("bg"), (8, 16, 28, 185)), outline=(120, 180, 220, 150), width=1)
    fs = el.get("font_scale", 1.0)
    f_title = font("bold", max(10, int(h * 0.072 * fs)))
    f_tag = font("bold", max(9, int(h * 0.064 * fs)))
    f_tick = font("regular", max(9, int(h * 0.058 * fs)))
    data, extremes = tide_data()
    if not data or not extremes:
        d.text((12, 12), "Tide chart unavailable", fill=(255, 255, 255, 220), font=f_title)
        return img
    t0, t1 = now - timedelta(hours=6), now + timedelta(hours=18)
    heights = [(t, v) for t, v in uc.parse_heights(data) if t0 <= t <= t1]
    curve = heights if len(heights) >= 4 else uc.cosine_heights(extremes, t0, t1, step_min=15)
    levels = [v for _, v in curve] + [e[1] for e in extremes if t0 <= e[0] <= t1]
    lo, hi = min(levels) - 0.25, max(levels) + 0.4
    th = f_tick.getbbox("0")[3]
    tt = f_title.getbbox("Ag")[3]
    px0, px1 = int(f_tick.getlength("0.0") + 14), w - 10
    py0, py1 = int(tt + 16 + th), h - int(th * 2 + 14)
    span = (t1 - t0).total_seconds()
    X = lambda t: px0 + (t - t0).total_seconds() / span * (px1 - px0)  # noqa: E731
    Y = lambda v: py1 - (v - lo) / (hi - lo) * (py1 - py0)  # noqa: E731
    station = (data.get("station") or {}).get("locality") or "Lyme Regis"
    d.text((10, 6), f"Tide · Axmouth ({station})", fill=(225, 238, 255, 240), font=f_title)
    nfn = "Not for navigation"
    d.text((w - 10 - f_tick.getlength(nfn), 8), nfn, fill=(215, 190, 120, 220), font=f_tick)
    step = 0.5 if hi - lo < 3.5 else 1.0
    v = (int(lo / step) + 1) * step
    while v <= hi:
        y = Y(v)
        d.line([(px0, y), (px1, y)], fill=(90, 120, 150, 90))
        lbl = f"{v:.1f}"
        d.text((px0 - 6 - f_tick.getlength(lbl), y - th / 2 - 1), lbl, fill=(190, 210, 230, 220), font=f_tick)
        v += step
    c = t0.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while c <= t1:
        if c.hour % 6 == 0:
            x = X(c)
            d.line([(x, py0), (x, py1)], fill=(90, 120, 150, 80))
            lbl = c.strftime("%H:%M") if c.hour else c.strftime("%a")
            d.text((x - f_tick.getlength(lbl) / 2, py1 + 4), lbl, fill=(190, 210, 230, 220), font=f_tick)
        c += timedelta(hours=1)
    pts = [(X(t), Y(v)) for t, v in curve]
    d.polygon([(pts[0][0], py1)] + pts + [(pts[-1][0], py1)], fill=(40, 160, 200, 60))
    d.line(pts, fill=(80, 220, 255, 245), width=max(2, h // 90))
    nhw = next((e for e in extremes if e[0] > now and e[2] == "HW"), None)
    nlw = next((e for e in extremes if e[0] > now and e[2] == "LW"), None)
    for e in extremes:
        if not t0 <= e[0] <= t1:
            continue
        x, y = X(e[0]), Y(e[1])
        r = max(3, h // 50)
        col = (255, 225, 100, 255) if e[2] == "HW" else (150, 210, 255, 255)
        d.ellipse([(x - r, y - r), (x + r, y + r)], fill=col, outline=(255, 255, 255, 210))
        if e is nhw or e is nlw:
            tag = f"{e[2]} {uc.fmt_time(e[0])} {e[1]:.1f}m"
            tw = f_tag.getlength(tag)
            tgh = f_tag.getbbox("Ag")[3]
            tx = min(max(x - tw / 2, 4), w - tw - 4)
            ty = y - tgh - 10 if e[2] == "HW" else y + 8
            if ty < py0:
                ty = y + 8
            if ty + tgh > py1:
                ty = y - tgh - 10
            d.rectangle([(tx - 3, ty - 2), (tx + tw + 3, ty + tgh + 2)], fill=(0, 0, 0, 150))
            d.text((tx, ty), tag, fill=(255, 255, 255, 250), font=f_tag)
    nx = X(now)
    d.line([(nx, py0), (nx, py1)], fill=(255, 120, 80, 230), width=2)
    return img


# ---------------------------------------------------------------- elements
def draw_text(canvas, d, el, sc):
    if "text_file" in el:
        f = find_file(el["text_file"])
        txt = f.read_text(encoding="utf-8").strip() if f else ""
    else:
        txt = str(el.get("text", ""))
    txt = datetime.now(uc.TZ).strftime(txt) if el.get("strftime") else txt
    if not txt:
        return
    size = max(6, int(el.get("size", 20) * sc))
    fnt = font(el.get("font", "bold"), size)
    maxw = el.get("max_width")
    while maxw and fnt.getlength(txt) > maxw * sc and size > 8:  # shrink to fit
        size -= 1
        fnt = font(el.get("font", "bold"), size)
    pad = int(el.get("pad", 0) * sc)
    l, t, r, b = d.textbbox((0, 0), txt, font=fnt)
    bw, bh = r - l + 2 * pad, b - t + 2 * pad
    x = place(el.get("x", 0), bw, canvas.width, sc)
    y = place(el.get("y", 0), bh, canvas.height, sc)
    if el.get("box"):
        d.rectangle([(x, y), (x + bw, y + bh)], fill=colour(el["box"]))
    d.text((x + pad - l, y + pad - t), txt, font=fnt, fill=colour(el.get("color")),
           stroke_width=int(el.get("stroke", 0) * sc), stroke_fill=colour(el.get("stroke_color"), (0, 0, 0, 255)))


def draw_box(canvas, el, sc, img):
    w, h = img.size
    x = place(el.get("x", 0), w, canvas.width, sc)
    y = place(el.get("y", 0), h, canvas.height, sc)
    canvas.alpha_composite(img, (max(0, min(x, canvas.width - w)), max(0, min(y, canvas.height - h))))


def render(layout: dict, mode: str) -> Image.Image:
    sc = OUT_H / float(layout.get("ref_height", 720))
    canvas = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    for el in layout.get("elements", []):
        show = el.get("show", "always")
        if el.get("enabled", True) is False or (show != "always" and show != mode):
            continue
        try:
            typ = el.get("type", "text")
            if typ == "text":
                draw_text(canvas, d, el, sc)
            elif typ == "chart":
                w, h = int(el.get("w", 400) * sc), int(el.get("h", 220) * sc)
                draw_box(canvas, el, sc, render_chart(min(w, OUT_W), min(h, OUT_H), el))
            elif typ == "image":
                f = find_file(el["path"])
                if not f:
                    continue
                img = Image.open(f).convert("RGBA")
                if "w" in el or "h" in el:
                    w = int(el.get("w", 0) * sc) or int(img.width * el["h"] * sc / img.height)
                    h = int(el.get("h", 0) * sc) or int(img.height * w / img.width)
                    img = img.resize((w, h), Image.LANCZOS)
                if "opacity" in el:
                    a = img.getchannel("A").point(lambda p: int(p * float(el["opacity"])))
                    img.putalpha(a)
                draw_box(canvas, el, sc, img)
        except Exception as e:  # one bad element must not kill the overlay
            log(f"element {el.get('id', '?')} error: {e}")
    return canvas


def atomic_png(img: Image.Image, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    img.save(tmp, "PNG", compress_level=1)
    os.replace(tmp, path)


def load_layout() -> dict:
    global _last_err
    if not LAYOUT.exists():
        shutil.copy(DEFAULT, LAYOUT)
        log(f"seeded {LAYOUT} from {DEFAULT}")
    try:
        layout = json.loads(LAYOUT.read_text(encoding="utf-8"))
        if _last_err:
            log("layout OK again")
        _last_err = ""
        load_layout.good = layout
        return layout
    except Exception as e:
        if str(e) != _last_err:
            log(f"overlay.json invalid ({e}); keeping last good layout")
            _last_err = str(e)
        return getattr(load_layout, "good", None) or json.loads(DEFAULT.read_text())


def main() -> int:
    DIR.mkdir(parents=True, exist_ok=True)
    once = "--once" in sys.argv
    log(f"rendering {OUT_W}x{OUT_H} overlays every {INTERVAL}s from {LAYOUT}")
    while True:
        t = time.time()
        try:
            layout = load_layout()
            atomic_png(render(layout, "live"), DIR / "overlay_live.png")
            atomic_png(render(layout, "offline"), DIR / "overlay_offline.png")
        except Exception as e:
            log(f"render error: {e}")
        if once:
            return 0
        time.sleep(max(0.2, INTERVAL - (time.time() - t)))


if __name__ == "__main__":
    raise SystemExit(main())
