"""Visual reference: measure the look of a video or image and match other footage to it.

Commands: ``reference`` (style profile + contact sheet) and ``match`` (colour, loudness and frame
transfer). Python API: ``profile(path)`` and ``color_filters(src_color, ref_color, strength)``.

Measurement reads small raw frames from ffmpeg pipes (like smart.py). numpy is used when importable;
VIDEOSWAMI_NO_NUMPY=1 forces the pure-Python path.
"""
import argparse
import json
import math
import shutil
import tempfile
from math import gcd
from pathlib import Path

from . import analyze, smart
from .ff import VSError, probe

ASPECTS = ((9, 16), (4, 5), (1, 1), (4, 3), (16, 9), (21, 9))
ASPECT_TOLERANCE = 0.03   # relative difference in ratio still counted as a named aspect
MAX_LONG_SIDE = 1920
MEASURE_FRAMES = 12
PARTS = ("color", "loudness", "frame")
CAPTION_WIDTH = 160
EDGE_STEP = 60            # a horizontal brightness step this big counts as high-contrast detail
TEMP_GAIN = 2.0           # temperature units per unit of (R-B)/(R+B)
MAX_SAT = 8.0
MAX_TEMP = 1.6


# --------------------------------------------------------------------------- helpers

def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _n(v):
    text = "%.4f" % v
    return text.rstrip("0").rstrip(".") if "." in text else text


def _need_media(path, what="reference"):
    p = Path(path).expanduser()
    if not p.is_file():
        raise VSError("%s not found: %s" % (what, path))
    return p


def _is_image(info):
    """A still picture: a single video frame with no sound and (at most) a placeholder duration."""
    return bool(info.get("has_video")) and not info.get("has_audio") and (
        info.get("nb_frames") == 1 or float(info.get("duration") or 0) < 0.05)


def _aspect_name(w, h):
    ratio = w / float(h)
    best = min(ASPECTS, key=lambda a: abs(math.log(ratio / (a[0] / float(a[1])))))
    if abs(ratio / (best[0] / float(best[1])) - 1.0) <= ASPECT_TOLERANCE:
        return "%d:%d" % best
    d = gcd(int(w), int(h)) or 1
    return "%d:%d" % (w // d, h // d)


# --------------------------------------------------------------------------- measuring colour

def _frames(path, info, vf="", start=0.0, length=None, count=MEASURE_FRAMES, width=smart.FRAME_W):
    """Raw rgb24 frames sampled evenly across `path` (or one frame of a still), with `vf` applied after scaling."""
    h = max(2, int(round(width * info["height"] / float(info["width"]))))
    still = _is_image(info)
    parts = []
    args = []
    if still:
        args += ["-i", smart._abs(path), "-frames:v", "1"]
    else:
        length = float(length if length is not None else (info.get("duration") or 0) - start)
        length = max(length, 0.04)
        if start > 0:
            args += ["-ss", "%.3f" % start]
        args += ["-t", "%.3f" % length, "-i", smart._abs(path)]
        parts.append("fps=%s" % _n(_clamp(count / length, 0.05, 50.0)))
    parts.append("scale=%d:%d" % (width, h))
    if vf:
        parts.append(vf)
    data = smart._pipe(args + ["-an", "-vf", ",".join(parts), "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    size = width * h * 3
    out = [data[i:i + size] for i in range(0, len(data) - size + 1, size)]
    if not out:
        raise VSError("could not sample any frames from %s" % path)
    return out


def _stats(frames):
    """Colour statistics of rgb24 frames; luma on 0..255."""
    np = smart._np()
    if np is not None:
        px = np.concatenate([np.frombuffer(f, dtype=np.uint8).reshape(-1, 3) for f in frames]).astype(np.float64)
        luma = px @ np.array([0.299, 0.587, 0.114])
        p5, p95 = np.percentile(luma, [5, 95])
        mx, mn = px.max(axis=1), px.min(axis=1)
        sat = float(np.where(mx > 0, (mx - mn) / np.where(mx > 0, mx, 1), 0).mean())
        mean = px.mean(axis=0)
        return _pack(float(luma.mean()), float(p5), float(p95), sat, [float(m) for m in mean])
    hist = [0] * 256
    sums = [0, 0, 0]
    luma_sum = 0.0
    sat_sum = 0.0
    n = 0
    for f in frames:
        r, g, b = f[0::3], f[1::3], f[2::3]
        sums[0] += sum(r)
        sums[1] += sum(g)
        sums[2] += sum(b)
        for x, y, z in zip(r, g, b):
            lum = 0.299 * x + 0.587 * y + 0.114 * z
            luma_sum += lum
            hist[int(lum)] += 1
            hi, lo = max(x, y, z), min(x, y, z)
            if hi:
                sat_sum += (hi - lo) / float(hi)
        n += len(r)

    def pct(p):
        goal, acc = p * n, 0
        for v, c in enumerate(hist):
            acc += c
            if acc >= goal:
                return float(v)
        return 255.0
    return _pack(luma_sum / n, pct(0.05), pct(0.95), sat_sum / n, [s / float(n) for s in sums])


def _pack(mean, p5, p95, sat, rgb):
    r, b = rgb[0], rgb[2]
    return {"luma_mean": round(mean, 2), "luma_p5": round(p5, 2), "luma_p95": round(p95, 2),
            "contrast": round(p95 - p5, 2), "saturation": round(sat, 4),
            "temperature": round((r - b) / (r + b), 4) if r + b else 0.0,
            "mean_rgb": [round(c, 2) for c in rgb]}


def measure(path, vf="", count=MEASURE_FRAMES, info=None):
    """Colour profile of a video or image, optionally after the ffmpeg filter chain `vf`."""
    info = info or probe(str(path))
    if not info.get("has_video") or not info.get("width"):
        raise VSError("%s has no picture to measure" % path)
    return _stats(_frames(path, info, vf, count=count))


def _blend_stats(items):
    """Weighted mean of colour profiles [(weight, profile)]."""
    total = float(sum(w for w, _ in items)) or 1.0
    keys = ("luma_mean", "luma_p5", "luma_p95", "saturation", "temperature")
    out = {k: sum(w * p[k] for w, p in items) / total for k in keys}
    rgb = [sum(w * p["mean_rgb"][i] for w, p in items) / total for i in range(3)]
    res = _pack(out["luma_mean"], out["luma_p5"], out["luma_p95"], out["saturation"], rgb)
    res["temperature"] = round(out["temperature"], 4)
    return res


# --------------------------------------------------------------------------- colour solving

IDENTITY = {"brightness": 0.0, "contrast": 1.0, "gamma": 1.0, "saturation": 1.0, "temperature": 0.0}


def _target(src, ref, strength):
    s = _clamp(float(strength), 0.0, 1.0)
    return {k: src[k] + s * (ref[k] - src[k]) for k in ("luma_mean", "luma_p5", "luma_p95", "contrast", "saturation", "temperature")}


def _solve(src, tgt):
    """First estimate of the filter parameters that move `src` statistics onto `tgt`."""
    c = _clamp((tgt["luma_p95"] - tgt["luma_p5"]) / max(src["luma_p95"] - src["luma_p5"], 1.0), 0.4, 2.5)
    b = _clamp(tgt["luma_mean"] / 255.0 - ((src["luma_mean"] / 255.0 - 0.5) * c + 0.5), -0.6, 0.6)
    sat = _clamp(tgt["saturation"] / max(src["saturation"], 0.02), 0.2, MAX_SAT)
    t = _clamp((tgt["temperature"] - src["temperature"]) * TEMP_GAIN, -MAX_TEMP, MAX_TEMP)
    return {"brightness": b, "contrast": c, "gamma": 1.0, "saturation": sat, "temperature": t}


def _refine(params, after, tgt):
    """One correction step from what the filters really produced."""
    p = dict(params)
    p["brightness"] = _clamp(p["brightness"] + (tgt["luma_mean"] - after["luma_mean"]) / 255.0, -0.8, 0.8)
    spread = max(after["luma_p95"] - after["luma_p5"], 1.0)
    p["contrast"] = _clamp(p["contrast"] * math.sqrt(_clamp((tgt["luma_p95"] - tgt["luma_p5"]) / spread, 0.5, 2.0)), 0.4, 3.0)
    p["saturation"] = _clamp(p["saturation"] * _clamp(tgt["saturation"] / max(after["saturation"], 0.02), 0.4, 2.5), 0.2, MAX_SAT)
    p["temperature"] = _clamp(p["temperature"] + (tgt["temperature"] - after["temperature"]) * TEMP_GAIN, -MAX_TEMP, MAX_TEMP)
    return p


def _chain(p):
    """ffmpeg filter chain for the parameters: eq (brightness, contrast, gamma), hue (saturation), red/blue gains (temperature)."""
    parts = []
    if (p["brightness"], p["contrast"], p["gamma"]) != (0.0, 1.0, 1.0):
        parts.append("eq=brightness=%s:contrast=%s:gamma=%s" % (_n(p["brightness"]), _n(p["contrast"]), _n(p["gamma"])))
    if abs(p["saturation"] - 1.0) > 1e-4:
        parts.append("hue=s=%s" % _n(p["saturation"]))
    if abs(p["temperature"]) > 1e-4:
        k = p["temperature"] * 0.5  # warm = more red, less blue
        parts.append("colorchannelmixer=rr=%s:bb=%s" % (_n(max(0.05, 1 + k)), _n(max(0.05, 1 - k))))
    return ",".join(parts)


def _error(after, tgt):
    return (abs(after["luma_mean"] - tgt["luma_mean"]) / 255.0 * 2 + abs(after["saturation"] - tgt["saturation"])
            + abs(after["temperature"] - tgt["temperature"]) + abs(after["contrast"] - tgt["contrast"]) / 255.0)


def color_filters(src_color, ref_color, strength=1.0):
    """ffmpeg filter chain that moves `src_color` statistics toward `ref_color` (profile['color'] dicts).

    This is the single-shot estimate; `match` and the spec `look` refine it once against a real measurement.
    """
    return _chain(_solve(src_color, _target(src_color, ref_color, strength)))


def _fit_and_refine(src, ref, strength, measure_after):
    """Estimate, measure the result with `measure_after(chain)`, correct once; keep whichever is closer.

    Returns (chain, params, after_profile).
    """
    tgt = _target(src, ref, strength)
    first = _solve(src, tgt)
    chain1 = _chain(first)
    after1 = measure_after(chain1)
    second = _refine(first, after1, tgt)
    chain2 = _chain(second)
    after2 = measure_after(chain2)
    if _error(after2, tgt) <= _error(after1, tgt):
        return chain2, second, after2
    return chain1, first, after1


def _applied(p):
    return {k: round(p[k], 4) for k in IDENTITY}


# --------------------------------------------------------------------------- profile

def _pacing(scenes, dur):
    bounds = [0.0] + [t for t in scenes if 0 < t < dur] + [dur]
    shots = sorted(b - a for a, b in zip(bounds, bounds[1:]))
    n = len(shots)
    median = shots[n // 2] if n % 2 else (shots[n // 2 - 1] + shots[n // 2]) / 2.0
    avg = dur / n
    return {"cuts": n - 1, "avg_shot": round(avg, 3), "median_shot": round(median, 3),
            "shots_per_minute": round(60.0 / avg, 2) if avg > 0 else 0.0}


def _captions(path, info):
    """Best effort: a horizontal band with persistent high-contrast small detail suggests burned-in captions."""
    try:
        frames = _frames(path, info, "format=gray", count=8, width=CAPTION_WIDTH)
    except VSError:
        return {"likely": False, "position": None}
    w = CAPTION_WIDTH
    h = max(2, int(round(w * info["height"] / float(info["width"]))))
    bands = 9
    rows_per = max(1, h // bands)
    scores = []
    for f in frames:
        gray = f[0::3]
        row_edges = []
        for y in range(h):
            row = gray[y * w:(y + 1) * w]
            row_edges.append(sum(1 for a, b in zip(row, row[1:]) if abs(a - b) >= EDGE_STEP) / float(w - 1))
        scores.append([sum(row_edges[i * rows_per:(i + 1) * rows_per]) / float(rows_per) for i in range(bands)])
    persistent = [sorted(s[i] for s in scores)[len(scores) // 2] for i in range(bands)]
    best = max(range(bands), key=lambda i: persistent[i])
    rest = [v for i, v in enumerate(persistent) if abs(i - best) > 1]
    rest_mean = sum(rest) / len(rest) if rest else 0.0
    if persistent[best] >= 0.04 and persistent[best] >= 3.0 * rest_mean:
        position = "top" if best < bands // 3 else "bottom" if best >= bands - bands // 3 else "center"
        return {"likely": True, "position": position}
    return {"likely": False, "position": None}


def profile(path, scenes=None, loudness=None, frames=MEASURE_FRAMES):
    """Style profile of a video or image (see docs/SPEC.md). `scenes` and `loudness` may be supplied if already measured."""
    src = _need_media(path)
    info = probe(str(src))
    if not info.get("has_video") or not info.get("width"):
        raise VSError("%s has no picture, so it has no look to measure" % path)
    still = _is_image(info)
    out = {"source": src.name, "kind": "image" if still else "video",
           "frame": {"width": info["width"], "height": info["height"], "aspect": _aspect_name(info["width"], info["height"]),
                     "fps": None if still else round(info["fps"], 3)},
           "color": measure(src, count=frames, info=info)}
    if still:
        out["pacing"] = None
        out["audio"] = {"integrated": None, "true_peak": None, "lra": None}
        out["captions"] = _captions(src, info)
        return out
    dur = float(info.get("duration") or 0)
    if scenes is None:
        scenes = analyze.find_scenes(src, 0.3, info)
    out["pacing"] = _pacing(scenes, dur)
    out["audio"] = loudness if loudness is not None else analyze.measure_loudness(src, info)
    out["captions"] = _captions(src, info)
    return out


def load_reference(ref):
    """A profile from a style.json or by measuring a video/image file."""
    p = _need_media(ref)
    if p.suffix.lower() == ".json":
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise VSError("%s is not valid JSON: %s" % (ref, exc))
        data = data.get("profile", data) if isinstance(data, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("color"), dict):
            raise VSError("%s is not a VideoSwami style file (no profile.color)" % ref)
        return data
    return profile(p)


# --------------------------------------------------------------------------- commands

def cmd_reference(a):
    src = _need_media(a.file, "reference")
    n = int(a.frames)
    if n < 1:
        raise VSError("--frames must be at least 1")
    out_dir = Path(a.out).expanduser() if a.out else src.parent / ("%s.style" % src.stem)
    sheet = out_dir / "contact.jpg"
    info = probe(str(src))
    if not info.get("has_video") or not info.get("width"):
        raise VSError("%s has no picture, so it has no look to measure" % a.file)
    still = _is_image(info)
    dry = bool(getattr(a, "dry_run", False))
    scenes = loudness = None
    if still:
        cmd = analyze.extract_frame(str(src), 0.0, sheet, width=1280)
        if dry:
            return {"ok": True, "dry_run": True, "style": str(out_dir / "style.json"), "contact_sheet": str(sheet), "commands": [cmd]}
        out_dir.mkdir(parents=True, exist_ok=True)
        analyze._run(cmd)
    else:
        ins = analyze.cmd_inspect(argparse.Namespace(file=str(src), frames=n, out=str(out_dir), dry_run=dry,
                                                     quiet=bool(getattr(a, "quiet", False)), overwrite=True))
        if dry:
            return {"ok": True, "dry_run": True, "style": str(out_dir / "style.json"), "contact_sheet": str(sheet),
                    "commands": ins.get("commands", [])}
        scenes, loudness = ins["scenes"], ins["loudness"]
    prof = profile(src, scenes=scenes, loudness=loudness, frames=max(n, MEASURE_FRAMES))
    style = out_dir / "style.json"
    style.write_text(json.dumps(prof, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "style": str(style), "contact_sheet": str(sheet), "profile": prof}


def _parse_only(text):
    if not text:
        return set(PARTS)
    wanted = {x.strip() for x in text.split(",") if x.strip()}
    bad = wanted - set(PARTS)
    if bad or not wanted:
        raise VSError("--only takes a comma list of %s (got %r)" % (", ".join(PARTS), text))
    return wanted


def _target_size(ref_frame):
    w, h = ref_frame["width"], ref_frame["height"]
    scale = min(1.0, MAX_LONG_SIDE / float(max(w, h)))
    return smart._even(w * scale), smart._even(h * scale)


def _pacing_hint(ref, src_cuts, src_dur):
    pa = ref.get("pacing")
    if not pa:
        return "the reference is a still image, so it has no pacing to compare"
    return "reference averages %.1f s per shot (%d cuts/min); this clip has %d cut%s" % (
        pa["avg_shot"], round(pa["shots_per_minute"]), src_cuts, "" if src_cuts == 1 else "s")


def cmd_match(a):
    from .render import _Graph, _fit_filters
    src = smart._need_file(a.file)
    strength = float(a.strength)
    if not 0.0 <= strength <= 1.0:
        raise VSError("--strength must be between 0 and 1 (got %s)" % a.strength)
    only = _parse_only(a.only)
    ref = load_reference(a.reference)
    out = smart._out_path(a, [src])
    info = probe(str(src))
    smart._need_video(info, src)
    before = measure(src, info=info)
    applied = {"color": None, "loudness": None, "frame": None}
    after = None
    g = _Graph()
    label = "0:v:0"
    if "color" in only:
        chain, params, after = _fit_and_refine(before, ref["color"], strength, lambda vf: measure(src, vf=vf, info=info))
        applied["color"] = _applied(params)
        if chain:
            label = g.add([label], chain)
    if "frame" in only:
        w, h = _target_size(ref["frame"])
        fps = ref["frame"].get("fps")
        label = _fit_filters(g, label, {"fit": a.fit}, w, h, "black")
        applied["frame"] = {"width": w, "height": h, "fps": fps or round(info["fps"], 3)}
        if fps:
            label = g.add([label], "fps=%s" % _n(fps))
    label = g.add([label], ["format=yuv420p", "setsar=1"])
    args = ["-i", smart._abs(src), "-filter_complex", g.text(), "-map", "[%s]" % label]
    codec = smart._codec_args(out)
    if info["has_audio"]:
        target = (ref.get("audio") or {}).get("integrated")
        if "loudness" in only and target is not None:
            target = _clamp(float(target), -70.0, -5.0)
            args += ["-map", "0:a:0", "-af", "loudnorm=I=%s:TP=-1.5:LRA=11,aresample=48000" % _n(target)]
            applied["loudness"] = {"target": round(target, 2)}
        else:
            args += ["-map", "0:a:0"]
    args += codec
    cuts = len(analyze.find_scenes(src, 0.3, info))
    extra = {"applied": applied, "before": {"color": before}, "after": {"color": after or before},
             "reference": {"color": ref["color"]}, "pacing_hint": _pacing_hint(ref, cuts, info["duration"])}
    return smart._encode(a, out, args, float(info["duration"] or 0), extra)


# --------------------------------------------------------------------------- edit spec `look`

def look_chain(spec):
    """Colour chain for spec['look'] (grades the finished timeline), measured on the timeline's own sources.

    Estimates from the sources, measures the graded sources, corrects once. Returns "" when nothing can be measured.
    """
    look = spec.get("look")
    if not look:
        return ""
    ref = load_reference(look["reference"])
    segments = []
    for item in spec["timeline"]:
        if item["kind"] not in ("video", "image"):
            continue
        segments.append((item["dur"], item))

    def sample(vf):
        found = []
        for weight, item in segments:
            info = item["probe"]
            if item["kind"] == "video":
                frames = _frames(item["src"], info, vf, start=item["in"], length=item["src_dur"], count=6)
            else:
                frames = _frames(item["src"], dict(info, nb_frames=1), vf)
            found.append((weight, _stats(frames)))
        return _blend_stats(found)

    if not segments:
        return ""
    chain, _params, _after = _fit_and_refine(sample(""), ref["color"], look["strength"], sample)
    return chain


# --------------------------------------------------------------------------- registration

def register(subparsers):
    p = subparsers.add_parser("reference", help="measure the look of a reference video or image (style.json + contact sheet)")
    p.add_argument("file", metavar="REF")
    p.add_argument("--out", help="directory for style.json and contact.jpg (default <REF stem>.style beside REF)")
    p.add_argument("--frames", type=int, default=12, help="frames on the contact sheet (default 12)")
    p.set_defaults(func=cmd_reference)

    p = subparsers.add_parser("match", help="match a clip's colour, loudness and frame to a reference")
    p.add_argument("file", metavar="FILE")
    p.add_argument("--reference", required=True, help="reference video, image or a style.json from `reference`")
    p.add_argument("--only", help="comma list of color,loudness,frame (default all)")
    p.add_argument("--strength", type=float, default=1.0, help="colour match strength 0..1 (default 1)")
    p.add_argument("--fit", default="cover", choices=["cover", "blur", "contain"], help="how the picture fills the new frame (default cover)")
    p.add_argument("-o", "--output", help="output file (required)")
    p.set_defaults(func=cmd_match)
