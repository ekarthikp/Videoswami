"""Animated captions as ASS subtitles, plus the ASS helpers shared with templates.py.

A transcript (docs/SPEC.md "Transcript format") is split into short word groups and
written as an ASS file whose PlayRes equals the output size, so sizes in px at 1080
high scale with the picture. render.py burns the file with the `ass` filter.
"""
from __future__ import annotations

import functools
import json
import re
import struct
from pathlib import Path

from . import ff
from .ff import VSError
from .spec import _bool, _choice, _color, _keys, _num, _path, _str

STYLES = ("pop", "highlight", "karaoke", "box", "classic", "minimal")
POSITIONS = ("bottom", "center", "top")
CAPTION_KEYS = ["transcript", "style", "position", "max_words", "size", "color", "accent", "font", "uppercase"]
DEFAULTS = {"style": "pop", "position": "bottom", "max_words": 4, "size": 80, "color": "white", "accent": "#FFE600",
            "font": None, "uppercase": False}
GAP_BREAK = 0.6
HOLD_MAX = 0.3

# --------------------------------------------------------------------------- ASS helpers

_NAMED = {"white": "FFFFFF", "black": "000000", "yellow": "FFFF00", "red": "FF0000", "green": "00FF00",
          "blue": "0000FF", "cyan": "00FFFF", "magenta": "FF00FF", "orange": "FFA500", "gray": "808080",
          "grey": "808080", "pink": "FFC0CB", "purple": "800080"}


def rgb_alpha(value):
    """('RRGGBB', alpha 0..255 where 0 is opaque) from 'red', '#RRGGBB', '0xRRGGBB' with an optional @opacity."""
    text, _, opacity = str(value).partition("@")
    low = text.lower()
    if low in _NAMED:
        rgb = _NAMED[low]
    elif low.startswith("#") and len(low) >= 7:
        rgb = low[1:7].upper()
    elif low.startswith("0x") and len(low) >= 8:
        rgb = low[2:8].upper()
    else:
        raise VSError("unsupported colour %r; use #RRGGBB or one of %s" % (value, ", ".join(sorted(_NAMED))))
    alpha = 0
    if opacity:
        alpha = int(round((1.0 - max(0.0, min(1.0, float(opacity)))) * 255))
    return rgb, alpha


def bgr(value):
    """Inline override colour body: BBGGRR."""
    rgb, _ = rgb_alpha(value)
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def ass_colour(value, alpha=None):
    """Style colour &HAABBGGRR."""
    rgb, a = rgb_alpha(value)
    return "&H%02X%s%s%s" % (a if alpha is None else alpha, rgb[4:6], rgb[2:4], rgb[0:2])


def ass_time(seconds):
    cs = int(round(max(0.0, seconds) * 100))
    return "%d:%02d:%02d.%02d" % (cs // 360000, cs // 6000 % 60, cs // 100 % 60, cs % 100)


def ass_text(text):
    """Escape user text for an ASS Dialogue line."""
    text = str(text).replace("\\", "/").replace("{", "\\{").replace("}", "\\}")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")


def ass_style(name, font, size, primary, secondary="&H00FFFFFF", outline_colour="&H00000000", back="&H00000000", bold=True,
              border_style=1, outline=0.0, shadow=0.0, alignment=2, margin_l=0, margin_r=0, margin_v=0):
    return "Style: %s,%s,%d,%s,%s,%s,%s,%d,0,0,0,100,100,0,0,%d,%.2f,%.2f,%d,%d,%d,%d,1" % (
        name, font, max(1, int(round(size))), primary, secondary, outline_colour, back, -1 if bold else 0,
        border_style, outline, shadow, alignment, margin_l, margin_r, margin_v)


def dialogue(start, end, style, text, layer=0):
    return "Dialogue: %d,%s,%s,%s,,0,0,0,,%s" % (layer, ass_time(start), ass_time(end), style, text)


def ass_doc(width, height, styles, events):
    head = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: %d" % width, "PlayResY: %d" % height, "WrapStyle: 0",
            "ScaledBorderAndShadow: yes", "YCbCr Matrix: None", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
            "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"]
    mid = ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    return "\n".join(head + list(styles) + mid + list(events)) + "\n"


def _font_family(path):
    """Family name (name id 1) of a TrueType/OpenType file; None when it cannot be read."""
    try:
        data = Path(path).read_bytes()
        base = 0
        if data[:4] == b"ttcf":
            base = struct.unpack(">I", data[12:16])[0]
        count = struct.unpack(">H", data[base + 4:base + 6])[0]
        for i in range(count):
            rec = base + 12 + 16 * i
            if data[rec:rec + 4] == b"name":
                off = struct.unpack(">I", data[rec + 8:rec + 12])[0]
                _, n, str_off = struct.unpack(">HHH", data[off:off + 6])
                best = None
                for j in range(n):
                    plat, enc, lang, nid, length, soff = struct.unpack(">HHHHHH", data[off + 6 + 12 * j:off + 18 + 12 * j])
                    if nid != 1:
                        continue
                    raw = data[off + str_off + soff:off + str_off + soff + length]
                    if plat == 3:
                        cand = (0 if lang == 0x409 else 1, raw.decode("utf-16-be", "replace"))
                    elif plat == 1:
                        cand = (2, raw.decode("mac_roman", "replace"))
                    else:
                        continue
                    if best is None or cand[0] < best[0]:
                        best = cand
                return best[1] if best else None
    except (OSError, struct.error, ValueError):
        return None
    return None


@functools.lru_cache(maxsize=32)
def resolve_font(font=None):
    """(family name for ASS, fonts directory or None). Falls back to a generic name when no font file is found."""
    path = ff.find_font(font, False)
    if not path:
        return "Arial", None
    family = _font_family(path) or Path(path).stem
    return family.replace(",", " "), str(Path(path).parent)


# --------------------------------------------------------------------------- transcript

def load_transcript_file(path):
    """Read a transcript JSON file into the shared shape; VSError on a bad file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise VSError("transcript %s cannot be read: %s" % (path, exc))
    return check_transcript(data, str(path))


def check_transcript(data, label="transcript"):
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise VSError("%s: expected an object with a 'segments' list" % label)
    for si, seg in enumerate(data["segments"]):
        if not isinstance(seg, dict) or not isinstance(seg.get("start"), (int, float)) or not isinstance(seg.get("end"), (int, float)):
            raise VSError("%s: segments[%d] needs numeric start and end" % (label, si))
        for wi, w in enumerate(seg.get("words") or []):
            if not isinstance(w, dict) or not isinstance(w.get("w", w.get("word")), str) \
                    or not isinstance(w.get("start"), (int, float)) or not isinstance(w.get("end"), (int, float)):
                raise VSError("%s: segments[%d].words[%d] needs w, start and end" % (label, si, wi))
    return data


def flat_words(transcript):
    """Flat [{w,start,end}] in time order; segments without word timings get words spread evenly."""
    out = []
    for seg in transcript["segments"]:
        words = seg.get("words") or []
        if words:
            for w in words:
                text = str(w.get("w", w.get("word"))).strip()
                if text:
                    out.append({"w": text, "start": float(w["start"]), "end": float(w["end"])})
            continue
        parts = str(seg.get("text", "")).split()
        if parts:
            step = (float(seg["end"]) - float(seg["start"])) / len(parts)
            for i, part in enumerate(parts):
                out.append({"w": part, "start": float(seg["start"]) + i * step, "end": float(seg["start"]) + (i + 1) * step})
    out.sort(key=lambda w: (w["start"], w["end"]))
    return out


_SENTENCE_END = re.compile(r"[.!?…][\"')\]]*$")


def group_words(words, max_words):
    """Short lines: break at max_words, at gaps over 0.6 s and after sentence ends. Adds 'show_end' (hold up to 0.3 s)."""
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > GAP_BREAK):
            groups.append(cur)
            cur = []
        cur.append(w)
        if _SENTENCE_END.search(w["w"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    out = []
    for i, ws in enumerate(groups):
        start, end = ws[0]["start"], max(ws[-1]["end"], ws[0]["start"] + 0.05)
        show_end = end + HOLD_MAX
        if i + 1 < len(groups):
            show_end = min(show_end, groups[i + 1][0]["start"])
        out.append({"start": start, "end": end, "show_end": max(show_end, end), "words": ws})
    return out


# --------------------------------------------------------------------------- spec / options

def normalise(raw, base):
    """Validate the spec's top-level `captions` object (or command options) and load its transcript."""
    _keys(raw, CAPTION_KEYS, "captions")
    if raw.get("transcript") is None:
        raise VSError("captions.transcript: required (path to a transcript JSON file)")
    path = _path(raw["transcript"], base, "captions.transcript")
    try:
        transcript = load_transcript_file(path)
    except VSError as exc:
        raise VSError("captions.transcript: %s" % exc)
    cap = options(raw, "captions")
    cap["transcript"] = transcript
    cap["transcript_path"] = path
    return cap


def options(raw, where="captions"):
    cap = {}
    cap["style"] = _choice(raw.get("style", DEFAULTS["style"]), STYLES, where + ".style")
    cap["position"] = _choice(raw.get("position", DEFAULTS["position"]), POSITIONS, where + ".position")
    cap["max_words"] = _num(raw.get("max_words", DEFAULTS["max_words"]), where + ".max_words", 1, 20, integer=True)
    cap["size"] = _num(raw.get("size", DEFAULTS["size"]), where + ".size", 1, 2000)
    cap["color"] = _color(raw.get("color", DEFAULTS["color"]), where + ".color")
    cap["accent"] = _color(raw.get("accent", DEFAULTS["accent"]), where + ".accent")
    font = raw.get("font")
    cap["font"] = _str(font, where + ".font") if font is not None else None
    cap["uppercase"] = _bool(raw.get("uppercase", False), where + ".uppercase")
    if cap["font"]:
        ff.find_font(cap["font"], False)
    return cap


# --------------------------------------------------------------------------- ASS generation

def build_ass(cap, transcript, width, height):
    """Return (ass text, fonts dir or None, group count) for normalised caption options and a transcript."""
    groups = group_words(flat_words(transcript), cap["max_words"])
    family, fontsdir = resolve_font(cap["font"])
    u = height / 1080.0
    style, position = cap["style"], cap["position"]
    size = cap["size"] * u
    if style == "minimal":
        size *= 0.6
    align = {"bottom": 2, "center": 5, "top": 8}[position]
    margin_v = int(round((0.05 if style == "minimal" else 0.07) * height))
    margin_lr = int(round(0.06 * width))
    primary = ass_colour(cap["color"])
    accent = ass_colour(cap["accent"])
    outline_colour = "&H00000000"
    outline = max(1.0, size * 0.07)
    border_style, shadow, bold = 1, max(0.0, size * 0.02), style != "minimal"
    if style == "box":
        outline, shadow = size * 0.28, 0.0
        outline_colour = "&H59000000"
    if style == "karaoke":
        primary, secondary = accent, ass_colour(cap["color"])
    else:
        secondary = ass_colour(cap["color"])
    styles = [ass_style("Cap", family, size, primary, secondary, outline_colour, "&H80000000", bold, border_style, outline, shadow,
                        align, margin_lr, margin_lr, margin_v)]
    events = []
    low = (lambda s: s.upper()) if cap["uppercase"] else (lambda s: s)
    for g in groups:
        words = [low(w["w"]) for w in g["words"]]
        if style in ("classic", "minimal", "box"):
            events.append(dialogue(g["start"], g["show_end"], "Cap", ass_text(" ".join(words))))
        elif style == "highlight":
            for i, w in enumerate(g["words"]):
                start = g["start"] if i == 0 else w["start"]
                end = g["words"][i + 1]["start"] if i + 1 < len(words) else g["show_end"]
                parts = []
                for j, text in enumerate(words):
                    if j == i:
                        parts.append("{\\1c&H%s&}%s{\\1c&H%s&}" % (bgr(cap["accent"]), ass_text(text), bgr(cap["color"])))
                    else:
                        parts.append(ass_text(text))
                events.append(dialogue(start, max(end, start + 0.02), "Cap", " ".join(parts)))
        elif style == "karaoke":
            parts = []
            for i, w in enumerate(g["words"]):
                nxt = g["words"][i + 1]["start"] if i + 1 < len(words) else w["end"]
                cs = max(1, int(round((nxt - w["start"]) * 100)))
                parts.append("{\\kf%d}%s%s" % (cs, ass_text(words[i]), " " if i + 1 < len(words) else ""))
            events.append(dialogue(g["start"], g["show_end"], "Cap", "".join(parts)))
        else:  # pop
            for i, w in enumerate(g["words"]):
                if i + 1 < len(words):
                    end = max(w["end"], min(g["words"][i + 1]["start"], w["end"] + 0.15))
                else:
                    end = g["show_end"]
                tag = "{\\fscx70\\fscy70\\t(0,90,\\fscx112\\fscy112)\\t(90,170,\\fscx100\\fscy100)}"
                events.append(dialogue(w["start"], max(end, w["start"] + 0.05), "Cap", tag + ass_text(words[i])))
    return ass_doc(width, height, styles, events), fontsdir, len(groups)


# --------------------------------------------------------------------------- command

def cmd_captions(a):
    from .quick import _need_file, _out_path, _spec_result, _abs, _dry
    src = _need_file(a.file)
    out = _out_path(a, [src])
    raw = {"style": a.style, "position": a.position, "max_words": a.max_words, "size": a.size, "color": a.color,
           "accent": a.accent, "uppercase": bool(a.uppercase)}
    if a.font:
        raw["font"] = a.font
    cap = options(raw, "options")
    if a.transcript:
        transcript = load_transcript_file(a.transcript)
        transcript_path = _abs(a.transcript)
    else:
        from . import speech  # lazy: only needed when no transcript was given
        transcript, transcript_path = speech.ensure_transcript(str(src))
    info = ff.probe(str(src))
    if not info["has_video"] and not a.ass_only:
        raise VSError("%s has no video stream to burn captions into" % a.file)
    width, height = info["width"] or 1920, info["height"] or 1080
    text, _, groups = build_ass(cap, transcript, width // 2 * 2, height // 2 * 2)
    if a.ass_only:
        if not _dry(a):
            Path(out).write_text(text, encoding="utf-8")
        return {"ok": True, "output": str(out), "ass": str(out), "style": cap["style"], "groups": groups}
    ass_path = out.with_suffix(".ass")
    if not _dry(a):
        ass_path.write_text(text, encoding="utf-8")
    spec = {"timeline": [{"src": _abs(src)}], "captions": dict(raw, transcript=_abs(transcript_path))}
    res = _spec_result(spec, out, a)
    res.update({"style": cap["style"], "groups": groups, "ass": str(ass_path)})
    return res


def register(sub):
    p = sub.add_parser("captions", help="burn animated word-by-word captions into a video")
    p.add_argument("file", metavar="FILE")
    p.add_argument("-o", "--output", help="output file (required)")
    p.add_argument("--transcript", help="transcript JSON (default: transcribe with the speech module)")
    p.add_argument("--style", default=DEFAULTS["style"], choices=STYLES)
    p.add_argument("--position", default=DEFAULTS["position"], choices=POSITIONS)
    p.add_argument("--max-words", type=int, default=DEFAULTS["max_words"], dest="max_words")
    p.add_argument("--size", type=float, default=DEFAULTS["size"], help="font size in px at 1080 high")
    p.add_argument("--color", default=DEFAULTS["color"])
    p.add_argument("--accent", default=DEFAULTS["accent"])
    p.add_argument("--font")
    p.add_argument("--uppercase", action="store_true")
    p.add_argument("--ass-only", action="store_true", dest="ass_only", help="write just the .ass file to OUT")
    p.set_defaults(func=cmd_captions)
