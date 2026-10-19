#!/usr/bin/env python3
"""Suggest (or optionally apply) an updated shingle-bar outline near daylight low tide.

Reads the current outline from /config/shingle_bar.json, samples a few frames from the
live HLS (or a still via --image), segments shingle vs water in a ROI around the outline,
and writes a candidate + debug JPEG under /config/shingle_candidates/.

Modes (SHINGLE_AUTO / --mode):
  off      - exit immediately
  suggest  - write candidates only (default)
  apply    - also atomically replace shingle_bar.json when checks pass

Designed to be light on a Pi 4: Pillow only, small ROI, few frames, runs rarely.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_caption as uc  # noqa: E402

DIR = Path(os.environ.get("AYC_DIR", "/data"))
CFG = Path(os.environ.get("AYC_LAYOUT", str(DIR / "overlay.json"))).parent
OUTLINE = Path(os.environ.get("AYC_SHINGLE", str(CFG / "shingle_bar.json")))
CAND_DIR = Path(os.environ.get("AYC_SHINGLE_CANDIDATES", str(CFG / "shingle_candidates")))
HLS = Path(os.environ.get("HLS_DIR", "/tmp/ayc_hls")) / "live.m3u8"
OUT_H = int(os.environ.get("OUTPUT_HEIGHT", "720"))
OUT_W = (OUT_H * 16 // 9 + 1) // 2 * 2
MODE = os.environ.get("SHINGLE_AUTO", "suggest").strip().lower() or "suggest"

DEFAULT_SHINGLE_JSON = """{"updated":"2026-10-19","label":"Shingle bar (updated 19 Oct)","ref_width":1280,"ref_height":720,"style":{"color":"#FF2828FF","outline":"#000000DC","width":4,"outline_width":8,"dash":[40,4],"label_color":"#FFFFFFFF","label_box":"#B41414DC"},"polylines":[{"id":"left","points":[[140.4,591.7],[156.1,573.9],[214.5,544.2],[233.3,539.0],[275.0,519.1],[381.5,486.3],[431.6,484.2],[432.6,500.9],[397.7,517.0],[390.3,539.5],[371.0,561.4]]},{"id":"right","points":[[523.9,561.9],[522.3,501.4],[525.0,493.0],[536.4,485.7],[601.1,478.4],[629.8,469.0],[709.1,462.8],[755.6,452.9]]}]}"""

# Timing gates
LW_WINDOW_H = 1.5          # ± hours around low water
MIN_GAP_BETWEEN_RUNS_S = 90 * 60  # don't re-run more than once per ~1.5 h
SUN_ALT_MIN_DEG = 5.0      # rough daylight gate (Axmouth ~50.7N)
GLARE_WHITE_FRAC_MAX = 0.18
MIN_EDGE_CONTRAST = 18.0    # mean |Δ| across proposed edge
MAX_MEAN_SHIFT_PX = 55.0    # reject if outline jumps too far
MAX_HAUSDORFF_PX = 90.0
N_FRAMES = 4
ROI_PAD = 48


def log(msg: str) -> None:
    print(f"{datetime.now(uc.TZ):%Y-%m-%d %H:%M:%S %Z} [shingle] {msg}", flush=True)


def load_outline(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"outline read error: {e}")
        return None


def all_points(outline: dict) -> list[tuple[float, float]]:
    pts = []
    for poly in outline.get("polylines") or []:
        for p in poly.get("points") or []:
            pts.append((float(p[0]), float(p[1])))
    return pts


def outline_bbox(outline: dict, pad: int = ROI_PAD):
    pts = all_points(outline)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (
        max(0, int(min(xs) - pad)),
        max(0, int(min(ys) - pad)),
        min(OUT_W, int(max(xs) + pad)),
        min(OUT_H, int(max(ys) + pad)),
    )


def nearest_lw(now: datetime):
    data = uc.load_cache()
    if not data:
        return None
    extremes = uc.parse_extremes(data)
    lws = [e for e in extremes if e[2] == "LW"]
    if not lws:
        return None
    return min(lws, key=lambda e: abs((e[0] - now).total_seconds()))


def rough_sun_up(now: datetime):
    """Cheap daylight check for ~Axmouth without astral deps.

    Approximate solar altitude via day-of-year declination + hour angle.
    Good enough to skip night / deep twilight. Returns (is_up, altitude_deg).
    """
    from datetime import timezone
    lat = 50.705 * math.pi / 180
    lon = -3.055
    start = datetime(now.year, 1, 1, tzinfo=uc.TZ)
    n = (now - start).days + 1
    decl = 23.44 * math.pi / 180 * math.sin(2 * math.pi * (284 + n) / 365)
    utc = now.astimezone(timezone.utc)
    lst_h = (utc.hour + utc.minute / 60 + utc.second / 3600) + lon / 15
    ha = (lst_h - 12) * 15 * math.pi / 180
    sin_alt = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(ha)
    alt = math.asin(max(-1, min(1, sin_alt))) * 180 / math.pi
    return alt >= SUN_ALT_MIN_DEG, alt


def grab_hls_frames(n: int = N_FRAMES) -> list[Image.Image]:
    if not HLS.exists():
        log(f"no HLS at {HLS}")
        return []
    out_dir = Path("/tmp/ayc_shingle_frames")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink(missing_ok=True)
    # One ffmpeg pass: take n frames spaced ~0.8s
    pattern = str(out_dir / "frame_%02d.jpg")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(HLS),
        "-frames:v", str(n),
        "-vf", f"fps=1.2,scale={OUT_W}:{OUT_H}",
        "-q:v", "4",
        pattern,
    ]
    try:
        subprocess.run(cmd, check=True, timeout=30)
    except Exception as e:
        log(f"ffmpeg grab failed: {e}")
        return []
    frames = []
    for p in sorted(out_dir.glob("frame_*.jpg")):
        try:
            frames.append(Image.open(p).convert("RGB").resize((OUT_W, OUT_H), Image.BILINEAR))
        except Exception:
            pass
    return frames


def glare_fraction(img: Image.Image, box) -> float:
    crop = img.crop(box)
    # near-white pixels
    px = crop.load()
    w, h = crop.size
    n = w * h
    if n == 0:
        return 1.0
    white = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y][:3]
            if r > 230 and g > 230 and b > 220:
                white += 1
    sampled = ((h + 1) // 2) * ((w + 1) // 2)
    return white / max(1, sampled)


def water_score(r, g, b) -> float:
    """Higher = more water-like. Shingle is dark grey/brown; water is brighter / bluer / foam-white."""
    bright = (r + g + b) / 3.0
    blue_bias = b - 0.5 * (r + g)
    # foam
    foam = 1.0 if bright > 200 and abs(r - g) < 25 and abs(g - b) < 30 else 0.0
    # dark shingle penalty
    dark = 1.0 if bright < 70 and abs(r - g) < 20 and abs(g - b) < 20 else 0.0
    return bright * 0.35 + blue_bias * 0.8 + foam * 80 - dark * 60


def sample_along_normal(img, x, y, dx, dy, half=18):
    """Mean water_score on the +normal side minus -normal side."""
    px = img.load()
    w, h = img.size
    pos = []
    neg = []
    nlen = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / nlen, dx / nlen  # left-hand normal
    for s in range(2, half + 1, 2):
        for sign, bucket in ((1, pos), (-1, neg)):
            xi = int(round(x + sign * nx * s))
            yi = int(round(y + sign * ny * s))
            if 0 <= xi < w and 0 <= yi < h:
                r, g, b = px[xi, yi][:3]
                bucket.append(water_score(r, g, b))
    if not pos or not neg:
        return 0.0, 0.0
    return sum(pos) / len(pos), sum(neg) / len(neg)


def refine_polyline(img: Image.Image, points: list, search=22) -> tuple[list, float]:
    """Nudge each vertex along the local normal toward the water/shingle boundary."""
    if len(points) < 2:
        return points, 0.0
    new = []
    contrasts = []
    for i, (x, y) in enumerate(points):
        if i == 0:
            dx, dy = points[1][0] - x, points[1][1] - y
        elif i == len(points) - 1:
            dx, dy = x - points[i - 1][0], y - points[i - 1][1]
        else:
            dx = points[i + 1][0] - points[i - 1][0]
            dy = points[i + 1][1] - points[i - 1][1]
        nlen = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / nlen, dx / nlen
        best_t = 0
        best_score = -1e9
        # Find t where water_score crosses: sample both sides at each offset
        for t in range(-search, search + 1, 2):
            xi = x + nx * t
            yi = y + ny * t
            pos_m, neg_m = sample_along_normal(img, xi, yi, dx, dy, half=14)
            # We want a large difference; polarity: prefer water on the "sea" side.
            # Use abs contrast; keep sign that matches current orientation at t=0 later.
            contrast = abs(pos_m - neg_m)
            # Prefer staying near original unless contrast clearly better
            score = contrast - 0.15 * abs(t)
            if score > best_score:
                best_score = score
                best_t = t
                best_contrast = contrast
        # Polarity lock: at t=0, remember which side was wetter; keep that
        pos0, neg0 = sample_along_normal(img, x, y, dx, dy, half=14)
        # If flipping would put water on the opposite side vs original, prefer smaller step
        pos_b, neg_b = sample_along_normal(img, x + nx * best_t, y + ny * best_t, dx, dy, half=14)
        if (pos0 - neg0) * (pos_b - neg_b) < 0 and abs(best_t) > 4:
            # polarity flipped — damp
            best_t = int(best_t * 0.3)
            best_contrast = abs(pos0 - neg0)
        new.append([round(x + nx * best_t, 1), round(y + ny * best_t, 1)])
        contrasts.append(best_contrast)
    mean_c = sum(contrasts) / max(1, len(contrasts))
    return new, mean_c


def mean_shift(a: list, b: list) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 1e9
    return sum(math.hypot(a[i][0] - b[i][0], a[i][1] - b[i][1]) for i in range(n)) / n


def hausdorff(a: list, b: list) -> float:
    def directed(p, q):
        return max(min(math.hypot(x0 - x1, y0 - y1) for x1, y1 in q) for x0, y0 in p)
    if not a or not b:
        return 1e9
    return max(directed(a, b), directed(b, a))


def median_polylines(runs: list[list[list]]) -> list[list]:
    """runs: list of polylines (each polyline = list of [x,y]); median per vertex."""
    if not runs:
        return []
    n = len(runs[0])
    out = []
    for i in range(n):
        xs = sorted(r[i][0] for r in runs)
        ys = sorted(r[i][1] for r in runs)
        mid = len(xs) // 2
        out.append([round(xs[mid], 1), round(ys[mid], 1)])
    return out


def format_label(when: datetime) -> str:
    return f"Shingle bar (updated {when.strftime('%-d %b')})"


def write_debug(img: Image.Image, old: dict, new_polys: list, box, path: Path, note: str):
    vis = img.convert("RGBA")
    overlay = Image.new("RGBA", vis.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rectangle(box, outline=(0, 200, 255, 180), width=2)
    for poly in old.get("polylines") or []:
        pts = [tuple(p) for p in poly.get("points") or []]
        if len(pts) >= 2:
            d.line(pts, fill=(255, 80, 80, 200), width=3)
    for pts in new_polys:
        if len(pts) >= 2:
            d.line([tuple(p) for p in pts], fill=(80, 255, 120, 220), width=3)
    d.rectangle([8, 8, 8 + 8 * len(note), 28], fill=(0, 0, 0, 160))
    try:
        fnt = ImageFont.truetype("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        try:
            fnt = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        except Exception:
            fnt = ImageFont.load_default()
    d.text((12, 10), note, fill=(255, 255, 255, 240), font=fnt)
    Image.alpha_composite(vis, overlay).convert("RGB").save(path, quality=85)


def last_run_too_recent(cand_dir: Path) -> bool:
    stamp = cand_dir / "last_run.json"
    if not stamp.exists():
        return False
    try:
        t = datetime.fromisoformat(json.loads(stamp.read_text())["at"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=uc.TZ)
        return (datetime.now(uc.TZ) - t).total_seconds() < MIN_GAP_BETWEEN_RUNS_S
    except Exception:
        return False


def mark_run(cand_dir: Path, info: dict):
    cand_dir.mkdir(parents=True, exist_ok=True)
    info = dict(info)
    info["at"] = datetime.now(uc.TZ).isoformat(timespec="seconds")
    (cand_dir / "last_run.json").write_text(json.dumps(info, indent=2))


def process_frames(frames: list[Image.Image], outline: dict, mode: str, force: bool = False) -> int:
    box = outline_bbox(outline)
    if not box:
        log("outline empty")
        return 2
    # Median glare across frames
    glares = [glare_fraction(im, box) for im in frames]
    glare = sorted(glares)[len(glares) // 2]
    if glare > GLARE_WHITE_FRAC_MAX and not force:
        log(f"reject: glare {glare:.2f} > {GLARE_WHITE_FRAC_MAX}")
        write_debug(frames[0], outline, [], box, CAND_DIR / f"reject_glare_{datetime.now(uc.TZ):%Y%m%d_%H%M%S}.jpg",
                    f"REJECT glare={glare:.2f}")
        mark_run(CAND_DIR, {"result": "reject_glare", "glare": glare})
        return 1

    new_polys = []
    contrasts = []
    for pi, poly in enumerate(outline.get("polylines") or []):
        pts = [[float(p[0]), float(p[1])] for p in poly.get("points") or []]
        if len(pts) < 2:
            new_polys.append(pts)
            continue
        per_frame = []
        per_c = []
        for im in frames:
            refined, c = refine_polyline(im, pts)
            per_frame.append(refined)
            per_c.append(c)
        med = median_polylines(per_frame)
        new_polys.append(med)
        contrasts.append(sorted(per_c)[len(per_c) // 2])
        log(f"poly[{poly.get('id', pi)}] contrast={contrasts[-1]:.1f} shift={mean_shift(pts, med):.1f}px")

    mean_c = sum(contrasts) / max(1, len(contrasts))
    if mean_c < MIN_EDGE_CONTRAST and not force:
        log(f"reject: low contrast {mean_c:.1f}")
        write_debug(frames[0], outline, new_polys, box,
                    CAND_DIR / f"reject_contrast_{datetime.now(uc.TZ):%Y%m%d_%H%M%S}.jpg",
                    f"REJECT contrast={mean_c:.1f}")
        mark_run(CAND_DIR, {"result": "reject_contrast", "contrast": mean_c, "glare": glare})
        return 1

    shifts = []
    haus = []
    for poly, newp in zip(outline.get("polylines") or [], new_polys):
        oldp = [[float(p[0]), float(p[1])] for p in poly.get("points") or []]
        shifts.append(mean_shift(oldp, newp))
        haus.append(hausdorff(oldp, newp))
    mean_s = sum(shifts) / max(1, len(shifts))
    max_h = max(haus) if haus else 0
    if (mean_s > MAX_MEAN_SHIFT_PX or max_h > MAX_HAUSDORFF_PX) and not force:
        log(f"reject: shape change mean_shift={mean_s:.1f} haus={max_h:.1f}")
        write_debug(frames[0], outline, new_polys, box,
                    CAND_DIR / f"reject_shape_{datetime.now(uc.TZ):%Y%m%d_%H%M%S}.jpg",
                    f"REJECT shift={mean_s:.0f} haus={max_h:.0f}")
        mark_run(CAND_DIR, {"result": "reject_shape", "mean_shift": mean_s, "hausdorff": max_h})
        return 1

    now = datetime.now(uc.TZ)
    candidate = json.loads(json.dumps(outline))  # deep copy
    candidate["updated"] = now.strftime("%Y-%m-%d")
    candidate["label"] = format_label(now)
    candidate["proposed_at"] = now.isoformat(timespec="seconds")
    candidate["metrics"] = {
        "glare": round(glare, 3),
        "contrast": round(mean_c, 1),
        "mean_shift_px": round(mean_s, 1),
        "hausdorff_px": round(max_h, 1),
        "mode": mode,
    }
    for poly, newp in zip(candidate["polylines"], new_polys):
        poly["points"] = newp

    CAND_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    cand_path = CAND_DIR / f"candidate_{stamp}.json"
    dbg_path = CAND_DIR / f"candidate_{stamp}.jpg"
    cand_path.write_text(json.dumps(candidate, indent=2) + "\n")
    write_debug(frames[0], outline, new_polys, box, dbg_path,
                f"OK glare={glare:.2f} c={mean_c:.0f} Δ={mean_s:.0f}px")
    # Stable pointer to latest suggestion
    latest = CAND_DIR / "latest.json"
    latest.write_text(json.dumps(candidate, indent=2) + "\n")
    log(f"wrote {cand_path.name} + debug JPEG (suggest only)" if mode != "apply"
        else f"wrote {cand_path.name}; applying to {OUTLINE}")

    if mode == "apply":
        tmp = OUTLINE.with_suffix(".json.tmp")
        # Strip proposal-only fields for live file
        live = {k: v for k, v in candidate.items() if k not in ("proposed_at", "metrics")}
        tmp.write_text(json.dumps(live, indent=2) + "\n")
        os.replace(tmp, OUTLINE)
        log(f"applied new outline → {OUTLINE}")

    mark_run(CAND_DIR, {"result": "ok", "candidate": cand_path.name, "glare": glare,
                        "contrast": mean_c, "mean_shift": mean_s})
    return 0


def gates_ok(force: bool = False) -> bool:
    if force:
        return True
    now = datetime.now(uc.TZ)
    up, alt = rough_sun_up(now)
    if not up:
        log(f"skip: sun alt {alt:.1f}° < {SUN_ALT_MIN_DEG}")
        return False
    lw = nearest_lw(now)
    if not lw:
        log("skip: no LW in tide cache")
        return False
    hours = abs((lw[0] - now).total_seconds()) / 3600
    if hours > LW_WINDOW_H:
        log(f"skip: nearest LW {lw[0]:%H:%M} is {hours:.1f}h away (need ≤{LW_WINDOW_H})")
        return False
    if last_run_too_recent(CAND_DIR):
        log("skip: already ran recently")
        return False
    log(f"gates ok: LW {lw[0]:%H:%M} in {hours:.2f}h, sun alt {alt:.1f}°")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Shingle-bar outline auto-update (suggest/apply/off)")
    ap.add_argument("--mode", choices=("off", "suggest", "apply"), default=None)
    ap.add_argument("--image", help="Still image to analyse instead of HLS")
    ap.add_argument("--outline", help="Path to current shingle_bar.json")
    ap.add_argument("--out", help="Candidates directory")
    ap.add_argument("--force", action="store_true", help="Skip tide/daylight/glare/shape gates")
    args = ap.parse_args(argv)

    mode = (args.mode or MODE).lower()
    if mode == "off":
        log("mode=off")
        return 0
    if args.outline:
        global OUTLINE
        OUTLINE = Path(args.outline)
    if args.out:
        global CAND_DIR
        CAND_DIR = Path(args.out)

    outline = load_outline(OUTLINE)
    if not outline:
        # Seed embedded default so detector and overlay can proceed (1.0.8+)
        try:
            OUTLINE.parent.mkdir(parents=True, exist_ok=True)
            if not OUTLINE.exists() or OUTLINE.stat().st_size == 0:
                OUTLINE.write_text(DEFAULT_SHINGLE_JSON + "\n", encoding="utf-8")
                log(f"seeded {OUTLINE} from embedded default")
            outline = load_outline(OUTLINE)
        except Exception as e:
            log(f"could not seed outline at {OUTLINE}: {e}")
        if not outline:
            log(f"no outline at {OUTLINE}")
            return 2

    if not args.image and not gates_ok(force=args.force):
        return 0

    if args.image:
        im = Image.open(args.image).convert("RGB")
        if im.size != (OUT_W, OUT_H):
            im = im.resize((OUT_W, OUT_H), Image.BILINEAR)
        frames = [im]
        log(f"using still {args.image} ({im.size})")
    else:
        frames = grab_hls_frames()
        if not frames:
            return 2
        log(f"grabbed {len(frames)} HLS frames")

    return process_frames(frames, outline, mode, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
