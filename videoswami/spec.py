"""Loading, normalising and validating edit specs (see docs/SPEC.md).

load_spec() accepts a path to a JSON file or an already parsed dict and returns a
fully normalised spec: defaults filled in, paths absolute, times as seconds,
every source probed, and every duration quantised to whole output frames so that
the computed duration equals what render produces. Every problem raises VSError
naming the offending field, e.g. ``timeline[2].sped: unknown key``.
"""
from __future__ import annotations

import copy
import difflib
import json
import os
import re
from fractions import Fraction
from pathlib import Path

from . import ff
from .ff import VSError

TOP_KEYS = ["output", "background", "timeline", "overlays", "subtitles", "audio", "captions"]
OUTPUT_KEYS = ["width", "height", "fps", "vcodec", "crf", "preset", "acodec", "audio_bitrate", "loudnorm",
               "faststart", "pix_fmt", "profile"]
# Export profiles. Explicit output keys override these; max_width/max_height only cap the default size (never scale up).
PROFILES = {
    "reels": {"width": 1080, "height": 1920, "fps": 30, "loudnorm": True},
    "shorts": {"width": 1080, "height": 1920, "fps": 30, "loudnorm": True},
    "tiktok": {"width": 1080, "height": 1920, "fps": 30, "loudnorm": True},
    "youtube": {"width": 1920, "height": 1080},
    "youtube-4k": {"width": 3840, "height": 2160},
    "square": {"width": 1080, "height": 1080},
    "portrait": {"width": 1080, "height": 1350},
    "whatsapp": {"max_width": 1280, "max_height": 720, "crf": 28, "audio_bitrate": "128k"},
    "x": {"width": 1280, "height": 720},
}
EASES = ("linear", "in", "out", "inout")
ANIM_KINDS = ("fade", "slide-up", "slide-down", "slide-left", "slide-right", "pop")
VISUAL_KEYS = ["fit", "crop", "rotate", "hflip", "vflip", "color", "grayscale", "blur", "sharpen", "denoise",
               "vignette", "lut", "zoom", "keyframes", "fade_in", "fade_out", "transition"]
VIDEO_ITEM_KEYS = ["src", "in", "out", "speed", "volume", "mute", "reverse"] + VISUAL_KEYS
IMAGE_ITEM_KEYS = ["image", "duration"] + VISUAL_KEYS
COLOR_ITEM_KEYS = ["color", "duration", "keyframes", "fade_in", "fade_out", "transition"]
COLOR_ADJUST_KEYS = ["brightness", "contrast", "saturation", "gamma", "hue", "temperature"]
OVERLAY_KEYS = {
    "text": ["type", "text", "start", "end", "x", "y", "margin", "size", "color", "font", "bold", "box", "box_color",
             "outline", "outline_color", "shadow", "fade", "align", "animate"],
    "image": ["type", "src", "start", "end", "x", "y", "margin", "scale", "opacity", "fade", "animate"],
    "video": ["type", "src", "in", "start", "end", "x", "y", "margin", "scale", "volume", "round", "animate"],
    "title": ["type", "template", "text", "sub", "start", "end", "accent", "color"],
}
SUBTITLE_KEYS = ["file", "burn", "size", "color"]
AUDIO_KEYS = ["src", "start", "in", "out", "volume", "fade_in", "fade_out", "loop", "duck"]
FITS = ("contain", "cover", "stretch", "blur")
VCODECS = ("h264", "h265", "vp9", "prores")
ACODECS = ("aac", "opus")
CONTAINERS = (".mp4", ".mov", ".mkv", ".webm")
H_POS = ("left", "center", "right")
V_POS = ("top", "center", "bottom")
COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?|0x[0-9a-fA-F]{6}([0-9a-fA-F]{2})?|[A-Za-z]+)(@[0-9.]+)?$")
TIME_RE = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")
FPS_MIN, FPS_MAX = 1, 240


# --------------------------------------------------------------------------- field helpers

def _keys(obj, allowed, where):
    if not isinstance(obj, dict):
        raise VSError("%s: expected an object, got %s" % (where, type(obj).__name__))
    for key in obj:
        if key not in allowed:
            near = difflib.get_close_matches(key, allowed, n=1)
            hint = " (did you mean '%s'?)" % near[0] if near else ""
            raise VSError("%s.%s: unknown key%s" % (where, key, hint))


def _num(value, where, lo=None, hi=None, integer=False, lo_open=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VSError("%s: expected a number, got %r" % (where, value))
    if integer and int(value) != value:
        raise VSError("%s: expected a whole number, got %r" % (where, value))
    if lo is not None and (value <= lo if lo_open else value < lo):
        raise VSError("%s: must be %s %s, got %r" % (where, ">" if lo_open else ">=", lo, value))
    if hi is not None and value > hi:
        raise VSError("%s: must be <= %s, got %r" % (where, hi, value))
    return int(value) if integer else float(value)


def _bool(value, where):
    if not isinstance(value, bool):
        raise VSError("%s: expected true or false, got %r" % (where, value))
    return value


def _str(value, where):
    if not isinstance(value, str) or not value:
        raise VSError("%s: expected a non-empty string, got %r" % (where, value))
    return value


def _choice(value, options, where):
    if value not in options:
        raise VSError("%s: must be one of %s, got %r" % (where, ", ".join(map(str, options)), value))
    return value


def _time(value, where):
    """Seconds from a number or "SS.mmm", "MM:SS.mmm", "HH:MM:SS.mmm"."""
    if isinstance(value, bool):
        raise VSError("%s: expected a time in seconds or HH:MM:SS.mmm, got %r" % (where, value))
    if isinstance(value, (int, float)):
        if value < 0:
            raise VSError("%s: time cannot be negative, got %r" % (where, value))
        return float(value)
    if isinstance(value, str):
        m = TIME_RE.match(value.strip())
        if m:
            parts = [p for p in value.strip().split(":")]
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + float(part)
            return seconds
    raise VSError("%s: expected a time in seconds or HH:MM:SS.mmm, got %r" % (where, value))


def _color(value, where):
    if not isinstance(value, str) or not COLOR_RE.match(value):
        raise VSError("%s: expected a colour like 'red', '#ff8800' or 'black@0.5', got %r" % (where, value))
    return value


def _path(value, base, where):
    text = _str(value, where)
    p = Path(text).expanduser()
    if not p.is_absolute():
        p = base / p
    if not p.is_file():
        raise VSError("%s: file not found: %s (looked for %s)" % (where, text, p))
    return str(p)


def _position(value, names, where):
    if isinstance(value, str):
        return _choice(value, names, where)
    return _num(value, where)


class _Prober:
    """Probe each file once per spec."""

    def __init__(self):
        self.cache = {}

    def __call__(self, path, where):
        if path not in self.cache:
            try:
                self.cache[path] = ff.probe(path)
            except VSError as exc:
                raise VSError("%s: %s" % (where, exc))
        return self.cache[path]


# --------------------------------------------------------------------------- timeline items

def _keyframes(raw, where):
    """Normalise camera keyframes; missing zoom/x/y hold the previous keyframe's value (1, 0.5, 0.5 at the start)."""
    if raw is None:
        return None
    where += ".keyframes"
    if not isinstance(raw, list) or not raw:
        raise VSError("%s: expected a non-empty list of keyframes" % where)
    out, zoom, x, y, last_t = [], 1.0, 0.5, 0.5, -1.0
    for i, kf in enumerate(raw):
        w = "%s[%d]" % (where, i)
        _keys(kf, ["t", "zoom", "x", "y", "ease"], w)
        if "t" not in kf:
            raise VSError("%s.t: required (seconds from the start of the item)" % w)
        t = _num(kf["t"], w + ".t", 0)
        if t <= last_t:
            raise VSError("%s.t: keyframe times must increase, got %s after %s" % (w, t, last_t))
        zoom = _num(kf.get("zoom", zoom), w + ".zoom", 1, 20)
        x = _num(kf.get("x", x), w + ".x", 0, 1)
        y = _num(kf.get("y", y), w + ".y", 0, 1)
        out.append({"t": t, "zoom": zoom, "x": x, "y": y, "ease": _choice(kf.get("ease", "linear"), EASES, w + ".ease")})
        last_t = t
    return out


def _animate(raw, where):
    """Normalise an overlay's animate object; None when absent or empty."""
    if raw is None:
        return None
    where += ".animate"
    _keys(raw, ["in", "out", "duration"], where)
    anim = {"in": None, "out": None, "duration": _num(raw.get("duration", 0.4), where + ".duration", 0, lo_open=True)}
    for key in ("in", "out"):
        if raw.get(key) is not None:
            anim[key] = _choice(raw[key], ANIM_KINDS, "%s.%s" % (where, key))
    return anim if (anim["in"] or anim["out"]) else None


def _visual(item, out, where, probe_for_kind):
    """Fields shared by video and image items."""
    base = out["_base"]
    out["fit"] = _choice(item.get("fit", "contain"), FITS, where + ".fit")
    crop = item.get("crop")
    if crop is not None:
        _keys(crop, ["x", "y", "w", "h"], where + ".crop")
        c = {"x": _num(crop.get("x", 0), where + ".crop.x", 0, 1), "y": _num(crop.get("y", 0), where + ".crop.y", 0, 1),
             "w": _num(crop.get("w", 1), where + ".crop.w", 0, 1, lo_open=True),
             "h": _num(crop.get("h", 1), where + ".crop.h", 0, 1, lo_open=True)}
        if c["x"] + c["w"] > 1.0001 or c["y"] + c["h"] > 1.0001:
            raise VSError("%s.crop: x+w and y+h must not exceed 1 (fractions of the source)" % where)
        out["crop"] = c
    else:
        out["crop"] = None
    out["rotate"] = _choice(item.get("rotate", 0), (0, 90, 180, 270), where + ".rotate")
    out["hflip"] = _bool(item.get("hflip", False), where + ".hflip")
    out["vflip"] = _bool(item.get("vflip", False), where + ".vflip")
    adj = item.get("color")
    if probe_for_kind and adj is not None:
        _keys(adj, COLOR_ADJUST_KEYS, where + ".color")
        out["color"] = {
            "brightness": _num(adj.get("brightness", 0), where + ".color.brightness", -1, 1),
            "contrast": _num(adj.get("contrast", 1), where + ".color.contrast", -1000, 1000),
            "saturation": _num(adj.get("saturation", 1), where + ".color.saturation", 0, 3),
            "gamma": _num(adj.get("gamma", 1), where + ".color.gamma", 0.1, 10),
            "hue": _num(adj.get("hue", 0), where + ".color.hue", -360, 360),
            "temperature": _num(adj.get("temperature", 0), where + ".color.temperature", -1, 1),
        }
    else:
        out["color"] = None
    out["grayscale"] = _bool(item.get("grayscale", False), where + ".grayscale")
    out["blur"] = _num(item.get("blur", 0), where + ".blur", 0, 100)
    out["sharpen"] = _num(item.get("sharpen", 0), where + ".sharpen", 0, 5)
    out["denoise"] = _bool(item.get("denoise", False), where + ".denoise")
    out["vignette"] = _bool(item.get("vignette", False), where + ".vignette")
    out["lut"] = _path(item["lut"], base, where + ".lut") if item.get("lut") is not None else None
    out["keyframes"] = _keyframes(item.get("keyframes"), where)
    zoom = item.get("zoom")
    if zoom is not None and out["keyframes"]:
        raise VSError("%s.keyframes: cannot be combined with zoom; use one or the other" % where)
    if zoom is not None:
        _keys(zoom, ["from", "to"], where + ".zoom")
        out["zoom"] = (_num(zoom.get("from", 1.0), where + ".zoom.from", 1, 10),
                       _num(zoom.get("to", 1.2), where + ".zoom.to", 1, 10))
    else:
        out["zoom"] = None


def _parse_item(item, index, base, prober):
    where = "timeline[%d]" % index
    if not isinstance(item, dict):
        raise VSError("%s: expected an object" % where)
    if "src" in item and "image" in item:
        raise VSError("%s: use either 'src' (video) or 'image' (still), not both" % where)
    out = {"_base": base, "index": index}
    if "src" in item:
        _keys(item, VIDEO_ITEM_KEYS, where)
        out["kind"] = "video"
        out["src"] = _path(item["src"], base, where + ".src")
        info = prober(out["src"], where + ".src")
        if not info["has_video"]:
            raise VSError("%s.src: %s has no video stream (use the 'audio' list for sound-only files)" % (where, item["src"]))
        out["probe"] = info
        out["has_audio"] = info["has_audio"]
        vdur = info["video_duration"] or info["duration"]
        start = _time(item.get("in", 0), where + ".in")
        end = item.get("out")
        end = _time(end, where + ".out") if end is not None else vdur
        if start >= vdur:
            raise VSError("%s.in: %s is past the end of %s (%.3f s long)" % (where, item.get("in"), item["src"], vdur))
        if end > vdur + 0.001:
            ff.warn("%s.out: %.3f is past the end of %s (%.3f s); using the end of the file" % (where, end, item["src"], vdur))
            end = vdur
        if end <= start:
            raise VSError("%s: 'out' (%.3f) must be greater than 'in' (%.3f)" % (where, end, start))
        out["in"], out["out"], out["src_dur"] = start, end, end - start
        out["explicit_out"] = item.get("out") is not None
        out["speed"] = _num(item.get("speed", 1.0), where + ".speed", 0.25, 4)
        out["volume"] = _num(item.get("volume", 1.0), where + ".volume", 0, 100)
        out["mute"] = _bool(item.get("mute", False), where + ".mute")
        out["reverse"] = _bool(item.get("reverse", False), where + ".reverse")
        _visual(item, out, where, True)
    elif "image" in item:
        _keys(item, IMAGE_ITEM_KEYS, where)
        out["kind"] = "image"
        out["src"] = _path(item["image"], base, where + ".image")
        out["probe"] = prober(out["src"], where + ".image")
        if not out["probe"]["has_video"]:
            raise VSError("%s.image: %s is not an image" % (where, item["image"]))
        out["has_audio"] = False
        if "duration" not in item:
            raise VSError("%s.duration: required for an image item" % where)
        out["src_dur"] = _time(item["duration"], where + ".duration")
        if out["src_dur"] <= 0:
            raise VSError("%s.duration: must be greater than 0" % where)
        out["speed"], out["volume"], out["mute"], out["reverse"] = 1.0, 1.0, True, False
        _visual(item, out, where, True)
    elif isinstance(item.get("color"), str):
        _keys(item, COLOR_ITEM_KEYS, where)
        out["kind"] = "color"
        out["color_value"] = _color(item["color"], where + ".color")
        out["has_audio"] = False
        if "duration" not in item:
            raise VSError("%s.duration: required for a colour item" % where)
        out["src_dur"] = _time(item["duration"], where + ".duration")
        if out["src_dur"] <= 0:
            raise VSError("%s.duration: must be greater than 0" % where)
        out["speed"], out["volume"], out["mute"], out["reverse"] = 1.0, 1.0, True, False
        out["keyframes"] = _keyframes(item.get("keyframes"), where)  # a solid colour has nothing to move, so it is only checked
    else:
        raise VSError("%s: needs 'src' (video clip), 'image' + 'duration' (still) or 'color' + 'duration' (solid colour)" % where)
    out["fade_in"] = _num(item.get("fade_in", 0), where + ".fade_in", 0)
    out["fade_out"] = _num(item.get("fade_out", 0), where + ".fade_out", 0)
    tr = item.get("transition")
    if tr is not None:
        _keys(tr, ["type", "duration"], where + ".transition")
        kind = _str(tr.get("type", "fade"), where + ".transition.type")
        known = ff.xfade_names()
        if known and kind not in known:
            near = difflib.get_close_matches(kind, sorted(known), n=3)
            raise VSError("%s.transition.type: unknown transition %r%s" % (where, kind, " (did you mean %s?)" % ", ".join(near) if near else ""))
        out["transition"] = {"type": kind, "duration": _num(tr.get("duration", 0.5), where + ".transition.duration", 0, lo_open=True)}
    else:
        out["transition"] = None
    return out


# --------------------------------------------------------------------------- other sections

def _overlay(item, index, base, prober, total):
    where = "overlays[%d]" % index
    if not isinstance(item, dict):
        raise VSError("%s: expected an object" % where)
    kind = _choice(item.get("type"), tuple(OVERLAY_KEYS), where + ".type")
    _keys(item, OVERLAY_KEYS[kind], where)
    if kind == "title":
        from . import templates
        o = templates.normalise(item, where)
    else:
        o = {"type": kind}
    o["start"] = _time(item.get("start", 0), where + ".start")
    end = item.get("end")
    o["end"] = _time(end, where + ".end") if end is not None else None
    if o["end"] is not None and o["end"] <= o["start"]:
        raise VSError("%s.end: must be greater than start (%.3f)" % (where, o["start"]))
    if o["start"] >= total:
        raise VSError("%s.start: %.3f is after the end of the video (%.3f s)" % (where, o["start"], total))
    if kind == "title":
        return o
    o["animate"] = _animate(item.get("animate"), where)
    o["x"] = _position(item.get("x", "center" if kind == "text" else "right"), H_POS, where + ".x")
    o["y"] = _position(item.get("y", "bottom" if kind == "text" else "top"), V_POS, where + ".y")
    o["margin"] = _num(item.get("margin", 60 if kind == "text" else 40), where + ".margin", 0)
    if kind == "text":
        o["text"] = _str(item.get("text"), where + ".text")
        o["size"] = _num(item.get("size", 64), where + ".size", 1, 2000)
        o["color"] = _color(item.get("color", "white"), where + ".color")
        o["font"] = _str(item["font"], where + ".font") if item.get("font") is not None else None
        o["bold"] = _bool(item.get("bold", False), where + ".bold")
        o["box"] = _bool(item.get("box", False), where + ".box")
        o["box_color"] = _color(item.get("box_color", "black@0.5"), where + ".box_color")
        # A boxed caption reads fine without an outline or shadow, so those default off with a box.
        o["outline"] = _num(item.get("outline", 0 if o["box"] else 2), where + ".outline", 0)
        o["outline_color"] = _color(item.get("outline_color", "black"), where + ".outline_color")
        o["shadow"] = _bool(item.get("shadow", not o["box"]), where + ".shadow")
        o["fade"] = _num(item.get("fade", 0), where + ".fade", 0)
        o["align"] = _choice(item.get("align", "center"), ("left", "center", "right"), where + ".align")
        if o["font"]:
            ff.find_font(o["font"], o["bold"])  # fail now, naming the field, if the font is unknown
    else:
        o["src"] = _path(item.get("src"), base, where + ".src")
        info = prober(o["src"], where + ".src")
        if not info["has_video"]:
            raise VSError("%s.src: %s has no picture" % (where, item.get("src")))
        o["probe"] = info
        o["scale"] = _num(item.get("scale", 0.15 if kind == "image" else 0.3), where + ".scale", 0, 1, lo_open=True)
        if kind == "image":
            o["opacity"] = _num(item.get("opacity", 1.0), where + ".opacity", 0, 1)
            o["fade"] = _num(item.get("fade", 0), where + ".fade", 0)
        else:
            o["in"] = _time(item.get("in", 0), where + ".in")
            o["volume"] = _num(item.get("volume", 0), where + ".volume", 0, 100)
            o["round"] = _bool(item.get("round", False), where + ".round")
    return o


def _subtitles(sub, base):
    _keys(sub, SUBTITLE_KEYS, "subtitles")
    return {"file": _path(sub.get("file"), base, "subtitles.file"),
            "burn": _bool(sub.get("burn", True), "subtitles.burn"),
            "size": _num(sub.get("size", 28), "subtitles.size", 1, 500),
            "color": _color(sub.get("color", "white"), "subtitles.color")}


def _audio_track(item, index, base, prober):
    where = "audio[%d]" % index
    _keys(item, AUDIO_KEYS, where)
    src = _path(item.get("src"), base, where + ".src")
    info = prober(src, where + ".src")
    if not info["has_audio"]:
        raise VSError("%s.src: %s has no audio stream" % (where, item.get("src")))
    start = _time(item.get("in", 0), where + ".in")
    end = item.get("out")
    end = _time(end, where + ".out") if end is not None else info["duration"]
    end = min(end, info["duration"]) if info["duration"] else end
    if end <= start:
        raise VSError("%s: 'out' (%.3f) must be greater than 'in' (%.3f)" % (where, end, start))
    return {"src": src, "probe": info, "start": _time(item.get("start", 0), where + ".start"), "in": start, "out": end,
            "volume": _num(item.get("volume", 1.0), where + ".volume", 0, 100),
            "fade_in": _num(item.get("fade_in", 0), where + ".fade_in", 0),
            "fade_out": _num(item.get("fade_out", 0), where + ".fade_out", 0),
            "loop": _bool(item.get("loop", False), where + ".loop"),
            "duck": _bool(item.get("duck", False), where + ".duck")}


def _even(n):
    return max(2, int(n) // 2 * 2)


def _output(raw, items):
    _keys(raw, OUTPUT_KEYS, "output")
    raw = dict(raw)
    profile = raw.pop("profile", None)
    opts = {}
    if profile is not None:
        opts = dict(PROFILES[_choice(profile, tuple(PROFILES), "output.profile")])
    opts.update(raw)
    raw = opts
    first_video = next((i for i in items if i["kind"] == "video"), None) or next((i for i in items if i["kind"] == "image"), None)
    dw = dh = None
    fps = Fraction(30)
    if first_video:
        dw, dh = first_video["probe"]["width"], first_video["probe"]["height"]
        if first_video["kind"] == "video" and first_video["probe"]["fps"]:
            fps = Fraction(first_video["probe"]["fps"]).limit_denominator(1001)
    out = {}
    dw, dh = dw or 1920, dh or 1080
    if "max_width" in raw and "max_height" in raw:  # profile cap: shrink the source size to fit, never enlarge
        shrink = min(1.0, raw["max_width"] / dw, raw["max_height"] / dh)
        dw, dh = dw * shrink, dh * shrink
    out["width"] = _even(_num(raw["width"], "output.width", 2, integer=True)) if "width" in raw else _even(dw)
    out["height"] = _even(_num(raw["height"], "output.height", 2, integer=True)) if "height" in raw else _even(dh)
    if "fps" in raw:
        fps = Fraction(_num(raw["fps"], "output.fps", FPS_MIN, FPS_MAX)).limit_denominator(1001)
    out["fps"] = float(fps)
    out["fps_frac"] = (fps.numerator, fps.denominator)
    out["vcodec"] = _choice(raw.get("vcodec", "h264"), VCODECS, "output.vcodec") if "vcodec" in raw else None
    out["crf"] = _num(raw.get("crf", 18), "output.crf", 0, 63)
    out["preset"] = _choice(raw.get("preset", "medium"), ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium",
                                                          "slow", "slower", "veryslow"), "output.preset")
    out["acodec"] = _choice(raw.get("acodec", "aac"), ACODECS, "output.acodec") if "acodec" in raw else None
    ab = raw.get("audio_bitrate", "192k")
    if not isinstance(ab, str) or not re.match(r"^\d+[kKmM]?$", ab):
        raise VSError("output.audio_bitrate: expected something like '192k', got %r" % (ab,))
    out["audio_bitrate"] = ab
    out["loudnorm"] = _bool(raw.get("loudnorm", False), "output.loudnorm")
    out["faststart"] = _bool(raw.get("faststart", True), "output.faststart")
    pix = raw.get("pix_fmt")
    if pix is not None and (not isinstance(pix, str) or not re.match(r"^[a-z0-9]+$", pix)):
        raise VSError("output.pix_fmt: expected a pixel format name such as 'yuv420p', got %r" % (pix,))
    out["pix_fmt"] = pix
    return out


# --------------------------------------------------------------------------- entry point

def load_spec(source):
    """Load and normalise a spec from a JSON file path or a dict."""
    if isinstance(source, dict) and source.get("_normalised"):
        return source
    base = Path.cwd()
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        if not path.is_file():
            raise VSError("spec file not found: %s" % source)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise VSError("%s is not valid JSON: %s" % (path, exc))
        base = path.resolve().parent
    elif isinstance(source, dict):
        data = copy.deepcopy(source)
    else:
        raise VSError("spec must be a path or an object, got %s" % type(source).__name__)
    return _normalise(data, base)


def _normalise(data, base):
    _keys(data, TOP_KEYS, "spec")
    timeline = data.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        raise VSError("timeline: must be a non-empty list of clips, images or colour items")
    prober = _Prober()
    items = [_parse_item(item, i, base, prober) for i, item in enumerate(timeline)]
    output = _output(data.get("output", {}), items)
    background = _color(data.get("background", "black"), "background")
    fps = Fraction(*output["fps_frac"])

    def frames_of(seconds):
        return max(1, int(round(seconds * fps)))

    total_frames = 0
    for i, item in enumerate(items):
        where = "timeline[%d]" % i
        item["dur_frames"] = frames_of(item["src_dur"] / item["speed"])
        item["dur"] = float(item["dur_frames"] / fps)
        for key in ("fade_in", "fade_out"):
            if item[key] > item["dur"] + 1e-6:
                raise VSError("%s.%s: %.3f s is longer than the item itself (%.3f s)" % (where, key, item[key], item["dur"]))
        tr = item["transition"]
        if tr and i == 0:
            ff.warn("%s.transition: ignored, there is no earlier clip to transition from" % where)
            item["transition"] = tr = None
        if tr:
            tr["frames"] = frames_of(tr["duration"])
            tr["duration"] = float(tr["frames"] / fps)
            if tr["frames"] > item["dur_frames"] or tr["frames"] > total_frames:
                raise VSError("%s.transition.duration: %.3f s is longer than the clips it joins (this clip %.3f s, everything before it %.3f s)"
                              % (where, tr["duration"], item["dur"], float(total_frames / fps)))
            total_frames += item["dur_frames"] - tr["frames"]
        else:
            total_frames += item["dur_frames"]
        item.pop("_base", None)
    total = float(total_frames / fps)

    overlays = data.get("overlays", [])
    if not isinstance(overlays, list):
        raise VSError("overlays: expected a list")
    audio = data.get("audio", [])
    if not isinstance(audio, list):
        raise VSError("audio: expected a list")
    spec = {
        "_normalised": True,
        "_base": str(base),
        "output": output,
        "background": background,
        "timeline": items,
        "overlays": [_overlay(o, i, base, prober, total) for i, o in enumerate(overlays)],
        "subtitles": _subtitles(data["subtitles"], base) if data.get("subtitles") is not None else None,
        "audio": [_audio_track(a, i, base, prober) for i, a in enumerate(audio)],
        "captions": None,
        "frames": total_frames,
        "duration": total,
    }
    if data.get("captions") is not None:
        from . import captions
        if not isinstance(data["captions"], dict):
            raise VSError("captions: expected an object")
        spec["captions"] = captions.normalise(data["captions"], base)
    if spec["subtitles"] is None and "subtitles" in data and data["subtitles"] is not None:
        raise VSError("subtitles: expected an object")
    return spec
