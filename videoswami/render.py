"""Turn a normalised edit spec into one ffmpeg invocation and run it.

The whole edit is a single -filter_complex graph: every timeline item is
normalised to the output size, frame rate, pixel format and stereo 48 kHz sound,
items are joined with concat (cuts) or xfade/acrossfade (transitions), overlays
are drawn on top, then extra audio tracks are mixed in.
"""
from __future__ import annotations

import os
import re
import tempfile
from fractions import Fraction
from pathlib import Path

from . import captions, ff, templates
from .ff import VSError, esc, esc_path
from .spec import CONTAINERS, load_spec

AUDIO_RATE = 48000
LONG_GRAPH = 6000  # characters; longer graphs go through -filter_complex_script


def _n(value):
    """Compact decimal for filter arguments."""
    text = "%.6f" % value
    return text.rstrip("0").rstrip(".") if "." in text else text


class _Work:
    """Scratch files (drawtext text, long graphs). Writes nothing in dry-run mode."""

    def __init__(self, dry):
        self.dry = dry
        self.dir = Path(tempfile.gettempdir()) / "videoswami-dry" if dry else Path(tempfile.mkdtemp(prefix="videoswami-"))
        self.count = 0

    def write(self, name, text):
        self.count += 1
        path = self.dir / ("%d-%s" % (self.count, name))
        if not self.dry:
            path.write_text(text, encoding="utf-8")
        return path

    def cleanup(self):
        if not self.dry:
            import shutil
            shutil.rmtree(self.dir, ignore_errors=True)


class _Graph:
    """Accumulates filter statements and hands out unique labels."""

    def __init__(self):
        self.statements = []
        self.counter = 0

    def label(self, prefix="t"):
        self.counter += 1
        return "%s%d" % (prefix, self.counter)

    def add(self, inputs, filters, prefix="t", outputs=1):
        """Append `[in..]f1,f2[out..]` and return the new output label(s)."""
        if isinstance(filters, (list, tuple)):
            filters = ",".join(filters)
        outs = [self.label(prefix) for _ in range(outputs)]
        head = "".join("[%s]" % i for i in inputs)
        self.statements.append("%s%s%s" % (head, filters, "".join("[%s]" % o for o in outs)))
        return outs[0] if outputs == 1 else outs

    def text(self):
        return ";\n".join(self.statements)


# --------------------------------------------------------------------------- timeline items

def _atempo_chain(speed):
    """atempo only accepts 0.5..2 in older ffmpeg builds, so chain it."""
    parts, left = [], speed
    while left < 0.5:
        parts.append("atempo=0.5")
        left /= 0.5
    while left > 2.0:
        parts.append("atempo=2.0")
        left /= 2.0
    if abs(left - 1.0) > 1e-9:
        parts.append("atempo=%s" % _n(left))
    return parts


def _fit_filters(g, label, item, W, H, bg):
    """Scale/pad/crop the picture to the output frame according to item['fit']."""
    fit = item["fit"]
    if fit == "stretch":
        return g.add([label], "scale=%d:%d,setsar=1" % (W, H))
    if fit == "cover":
        return g.add([label], "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1" % (W, H, W, H))
    if fit == "contain":
        return g.add([label], "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s,setsar=1"
                     % (W, H, W, H, esc(bg)))
    # blur: a blurred, cropped copy behind a contained copy of the picture.
    back, front = g.add([label], "split=2", outputs=2)
    small_w, small_h = max(2, W // 8 // 2 * 2), max(2, H // 8 // 2 * 2)
    back = g.add([back], "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,scale=%d:%d,gblur=sigma=6:steps=2,scale=%d:%d,setsar=1"
                 % (W, H, W, H, small_w, small_h, W, H))
    front = g.add([front], "scale=%d:%d:force_original_aspect_ratio=decrease,setsar=1" % (W, H))
    return g.add([back, front], "overlay=(W-w)/2:(H-h)/2")


def _ease_expr(p, ease):
    """Expression easing progress `p` (0..1)."""
    if ease == "in":
        return "(%s)*(%s)" % (p, p)
    if ease == "out":
        return "1-(1-(%s))*(1-(%s))" % (p, p)
    if ease == "inout":
        return "if(lt(%s,0.5),2*(%s)*(%s),1-pow(2-2*(%s),2)/2)" % (p, p, p, p)
    return p


def _keyframe_value(kfs, key, tvar):
    """Piecewise expression of one keyframed value over time expression `tvar` (holds before/after the ends)."""
    expr = _n(kfs[-1][key])
    for i in range(len(kfs) - 1, 0, -1):
        a, b = kfs[i - 1], kfs[i]
        if a[key] == b[key]:
            seg = _n(a[key])
        else:
            p = "clip((%s-%s)/%s,0,1)" % (tvar, _n(a["t"]), _n(b["t"] - a["t"]))
            seg = "%s+(%s)*(%s)" % (_n(a[key]), _n(b[key] - a[key]), _ease_expr(p, b["ease"]))
        expr = "if(lt(%s,%s),%s,%s)" % (tvar, _n(b["t"]), seg, expr)
    return "if(lt(%s,%s),%s,%s)" % (tvar, _n(kfs[0]["t"]), _n(kfs[0][key]), expr)


def _keyframe_filters(g, label, kfs, W, H, fps_frac):
    """Camera move on the fitted W x H frame: zoom into a window that follows (x, y) and never leaves the picture."""
    fps = fps_frac[0] / float(fps_frac[1])
    tvar = "(on/%s)" % _n(fps)
    factor = 2 if max(k["zoom"] for k in kfs) <= 2 else 3
    z = _keyframe_value(kfs, "zoom", tvar)
    cx = _keyframe_value(kfs, "x", tvar)
    cy = _keyframe_value(kfs, "y", tvar)
    x = "max(0,min(iw-iw/zoom,(%s)*iw-iw/zoom/2))" % cx
    y = "max(0,min(ih-ih/zoom,(%s)*ih-ih/zoom/2))" % cy
    return g.add([label], "scale=%d:%d,zoompan=z=%s:x=%s:y=%s:d=1:s=%dx%d:fps=%s,setsar=1"
                 % (W * factor, H * factor, esc(z), esc(x), esc(y), W, H, "%d/%d" % tuple(fps_frac)))


def _item_video(g, inputs, item, out, bg):
    """Return the label of the finished picture stream for one timeline item."""
    W, H = out["width"], out["height"]
    fps = "%d/%d" % tuple(out["fps_frac"])
    frames = item["dur_frames"]
    dur = item["dur"]
    kind = item["kind"]
    if kind == "color":
        label = g.add([], "color=c=%s:s=%dx%d:r=%s:d=%s,trim=end_frame=%d,setpts=PTS-STARTPTS" % (esc(item["color_value"]), W, H, fps, _n(dur + 1.0), frames))
        filters = []
    else:
        idx = len(inputs)
        if kind == "video":
            opts = []
            if item["in"] > 0:
                opts += ["-ss", _n(item["in"])]
            if item["explicit_out"]:
                opts += ["-t", _n(item["src_dur"] + 0.5)]
            inputs.append((opts, item["src"]))
            pre = ["trim=duration=%s" % _n(item["src_dur"]), "setpts=PTS-STARTPTS"]
            if item["reverse"]:
                pre.append("reverse")
            if item["speed"] != 1.0:
                pre.append("setpts=PTS/%s" % _n(item["speed"]))
            pre.append("fps=%s" % fps)
            # Clone the last frame so a source a hair too short still yields the exact frame count.
            pre.append("tpad=stop_mode=clone:stop_duration=1")
        else:
            inputs.append((["-loop", "1", "-framerate", fps, "-t", _n(dur + 0.5)], item["src"]))
            pre = ["fps=%s" % fps]
        pre += ["trim=end_frame=%d" % frames, "setpts=PTS-STARTPTS"]
        label = g.add(["%d:v:0" % idx], pre)
        filters = []
        if item["crop"]:
            c = item["crop"]
            filters.append("crop=w='trunc(iw*%s/2)*2':h='trunc(ih*%s/2)*2':x='iw*%s':y='ih*%s'" % (_n(c["w"]), _n(c["h"]), _n(c["x"]), _n(c["y"])))
        if item["rotate"] == 90:
            filters.append("transpose=1")
        elif item["rotate"] == 180:
            filters += ["hflip", "vflip"]
        elif item["rotate"] == 270:
            filters.append("transpose=2")
        if item["hflip"]:
            filters.append("hflip")
        if item["vflip"]:
            filters.append("vflip")
        c = item["color"]
        if c:
            if (c["brightness"], c["contrast"], c["saturation"], c["gamma"]) != (0, 1, 1, 1):
                filters.append("eq=brightness=%s:contrast=%s:saturation=%s:gamma=%s" % (_n(c["brightness"]), _n(c["contrast"]), _n(c["saturation"]), _n(c["gamma"])))
            if c["hue"]:
                filters.append("hue=h=%s" % _n(c["hue"]))
            if c["temperature"]:
                t = c["temperature"] * 0.3  # warm = more red, less blue
                filters.append("colorbalance=rs=%s:gs=0:bs=%s:rm=%s:bm=%s" % (_n(t), _n(-t), _n(t / 2), _n(-t / 2)))
        if item["grayscale"]:
            filters.append("hue=s=0")
        if item["denoise"]:
            filters.append("hqdn3d")
        if item["blur"]:
            filters.append("gblur=sigma=%s" % _n(item["blur"]))
        if item["sharpen"]:
            filters.append("unsharp=5:5:%s" % _n(item["sharpen"]))
        if item["vignette"]:
            filters.append("vignette")
        if item["lut"]:
            filters.append("lut3d=file=%s" % esc_path(item["lut"]))
        if filters:
            label = g.add([label], filters)
        label = _fit_filters(g, label, item, W, H, bg)
        if item.get("keyframes"):
            label = _keyframe_filters(g, label, item["keyframes"], W, H, out["fps_frac"])
        if item["zoom"]:
            z0, z1 = item["zoom"]
            span = max(frames - 1, 1)
            label = g.add([label], "scale=%d:%d,zoompan=z='%s+(%s)*on/%d':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=%dx%d:fps=%s,setsar=1"
                          % (W * 2, H * 2, _n(z0), _n(z1 - z0), span, W, H, fps))
    tail = []
    if item["fade_in"] > 0:
        tail.append("fade=t=in:st=0:d=%s" % _n(item["fade_in"]))
    if item["fade_out"] > 0:
        tail.append("fade=t=out:st=%s:d=%s" % (_n(max(0, dur - item["fade_out"])), _n(item["fade_out"])))
    tail += ["format=yuv420p", "setsar=1"]
    return g.add([label], tail)


def _item_audio(g, inputs_index, item):
    """Stereo 48 kHz sound of exactly item['dur'] seconds (silence when the item has none)."""
    dur = item["dur"]
    if item["kind"] == "video" and item["has_audio"] and not item["mute"]:
        pre = ["atrim=duration=%s" % _n(item["src_dur"]), "asetpts=PTS-STARTPTS", "aresample=%d" % AUDIO_RATE,
               "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if item["reverse"]:
            pre.append("areverse")
        pre += _atempo_chain(item["speed"])
        if item["volume"] != 1.0:
            pre.append("volume=%s" % _n(item["volume"]))
        pre += ["apad", "atrim=duration=%s" % _n(dur), "asetpts=PTS-STARTPTS"]
        label = g.add(["%d:a:0" % inputs_index], pre)
    else:
        label = g.add([], "anullsrc=channel_layout=stereo:sample_rate=%d:duration=%s,aformat=sample_fmts=fltp,atrim=duration=%s,asetpts=PTS-STARTPTS"
                      % (AUDIO_RATE, _n(dur), _n(dur)))
    tail = []
    if item["fade_in"] > 0:
        tail.append("afade=t=in:st=0:d=%s" % _n(item["fade_in"]))
    if item["fade_out"] > 0:
        tail.append("afade=t=out:st=%s:d=%s" % (_n(max(0, dur - item["fade_out"])), _n(item["fade_out"])))
    return g.add([label], tail) if tail else label


def _join(g, items, out, streams):
    """Join per-item (video, audio) streams: concat for cuts, xfade+acrossfade for transitions."""
    fps = "%d/%d" % tuple(out["fps_frac"])
    rate = Fraction(*out["fps_frac"])

    def flush(group):
        if len(group) == 1:
            return group[0]
        flat = []
        for v, a in group:
            flat += [v, a]
        v, a = g.add(flat, "concat=n=%d:v=1:a=1" % len(group), outputs=2)
        return g.add([v], "fps=%s" % fps), a

    group = [streams[0]]
    frames = items[0]["dur_frames"]
    for item, (v, a) in zip(items[1:], streams[1:]):
        tr = item["transition"]
        if tr:
            cv, ca = flush(group)
            offset = float((frames - tr["frames"]) / rate)
            nv = g.add([cv, v], "xfade=transition=%s:duration=%s:offset=%s" % (tr["type"], _n(tr["duration"]), _n(offset)))
            na = g.add([ca, a], "acrossfade=d=%s" % _n(tr["duration"]))
            group = [(nv, na)]
            frames += item["dur_frames"] - tr["frames"]
        else:
            group.append((v, a))
            frames += item["dur_frames"]
    return flush(group)


# --------------------------------------------------------------------------- overlays

def _xy(value, axis, margin):
    """drawtext expression for a coordinate (named position or pixels)."""
    m = _n(margin)
    if isinstance(value, str):
        if axis == "x":
            return {"left": m, "center": "(w-text_w)/2", "right": "w-text_w-" + m}[value]
        return {"top": m, "center": "(h-text_h)/2", "bottom": "h-text_h-" + m}[value]
    return _n(value)


def _overlay_xy(value, axis, margin):
    main, over = ("main_w", "overlay_w") if axis == "x" else ("main_h", "overlay_h")
    low, high = ("left", "right") if axis == "x" else ("top", "bottom")
    if isinstance(value, str):
        if value == low:
            return _n(margin)
        if value == high:
            return "%s-%s-%s" % (main, over, _n(margin))
        return "(%s-%s)/2" % (main, over)
    return _n(value)


def _enable(start, end):
    if end is None:
        return None if start <= 0 else "gte(t,%s)" % _n(start)
    return "between(t,%s,%s)" % (_n(start), _n(end))


def _prog_in(anim, start):
    return "clip((t-%s)/%s,0,1)" % (_n(start), _n(anim["duration"]))


def _prog_out(anim, end):
    return "clip((t-%s)/%s,0,1)" % (_n(end - anim["duration"]), _n(anim["duration"]))


def _anim_fades(anim, start, end):
    """0..1 expressions that fade the overlay in/out (fade and pop both fade)."""
    parts = []
    if anim["in"] in ("fade", "pop"):
        parts.append("clip((t-%s)/%s,0,1)" % (_n(start), _n(anim["duration"])))
    if anim["out"] in ("fade", "pop"):
        parts.append("clip((%s-t)/%s,0,1)" % (_n(end), _n(anim["duration"])))
    return parts


def _anim_scale(anim, start, end):
    """Size factor expression for pop animations, None when neither edge pops."""
    factors = []
    if anim["in"] == "pop":
        factors.append("(0.6+0.4*(1-(1-%s)*(1-%s)))" % (_prog_in(anim, start), _prog_in(anim, start)))
    if anim["out"] == "pop":
        factors.append("(1-0.4*%s*%s)" % (_prog_out(anim, end), _prog_out(anim, end)))
    return "*".join(factors) if factors else None


def _anim_slide(anim, xf, yf, names, start, end):
    """x/y expressions: (xf, yf) plus slide offsets. names = (main_w, main_h, own_w, own_h) expression names."""
    mw, mh, ow, oh = names
    ip = "(1-(1-%s)*(1-%s))" % (_prog_in(anim, start), _prog_in(anim, start))
    op = "(%s*%s)" % (_prog_out(anim, end), _prog_out(anim, end))
    dx, dy = [], []
    kind = anim["in"]
    if kind == "slide-up":
        dy.append("(1-%s)*(%s-(%s))" % (ip, mh, yf))
    elif kind == "slide-down":
        dy.append("-(1-%s)*((%s)+%s)" % (ip, yf, oh))
    elif kind == "slide-left":
        dx.append("(1-%s)*(%s-(%s))" % (ip, mw, xf))
    elif kind == "slide-right":
        dx.append("-(1-%s)*((%s)+%s)" % (ip, xf, ow))
    kind = anim["out"]
    if kind == "slide-up":
        dy.append("-%s*((%s)+%s)" % (op, yf, oh))
    elif kind == "slide-down":
        dy.append("%s*(%s-(%s))" % (op, mh, yf))
    elif kind == "slide-left":
        dx.append("-%s*((%s)+%s)" % (op, xf, ow))
    elif kind == "slide-right":
        dx.append("%s*(%s-(%s))" % (op, mw, xf))
    return "(%s)%s" % (xf, "".join("+(%s)" % d for d in dx)), "(%s)%s" % (yf, "".join("+(%s)" % d for d in dy))


def _text_overlay(g, label, o, W, H, work, total=None):
    scale = H / 1080.0
    size = max(1, int(round(o["size"] * scale)))
    margin = o["margin"] * scale
    textfile = work.write("text.txt", o["text"].replace("\r\n", "\n"))
    anim = o.get("animate")
    start, end, fade = o["start"], o["end"], o["fade"]
    stop = end if end is not None else total
    xe, ye = _xy(o["x"], "x", margin), _xy(o["y"], "y", margin)
    fontsize = "%d" % size
    if anim:
        xe, ye = _anim_slide(anim, xe, ye, ("w", "h", "text_w", "text_h"), start, stop)
        grow = _anim_scale(anim, start, stop)
        if grow:
            fontsize = esc("%d*%s" % (size, grow))
    opts = ["textfile=%s" % esc_path(textfile), "expansion=none", "fontsize=%s" % fontsize,
            "fontcolor=%s" % esc(o["color"]),
            "x=%s" % esc(xe), "y=%s" % esc(ye)]
    font = ff.find_font(o["font"], o["bold"]) if o["font"] else ff.find_font(None, o["bold"])
    if font:
        opts.append("fontfile=%s" % esc_path(font))
    if "\n" in o["text"]:
        opts.append("text_align=%s" % o["align"])
    if o["box"]:
        opts += ["box=1", "boxcolor=%s" % esc(o["box_color"]), "boxborderw=%d" % max(1, int(round(size * 0.25)))]
    if o["outline"] > 0:
        opts += ["borderw=%d" % max(1, int(round(o["outline"] * scale))), "bordercolor=%s" % esc(o["outline_color"])]
    if o["shadow"]:
        off = max(1, int(round(size * 0.04)))
        opts += ["shadowx=%d" % off, "shadowy=%d" % off, "shadowcolor=%s" % esc("black@0.6")]
    enable = _enable(start, end)
    if enable:
        opts.append("enable=%s" % esc(enable))
    fades = _anim_fades(anim, start, stop) if anim else []
    if fade > 0 or fades:
        expr = "1"
        if fade > 0:
            expr = "min(1,(t-%s)/%s)" % (_n(start), _n(fade))
            if end is not None:
                expr = "min(%s,(%s-t)/%s)" % (expr, _n(end), _n(fade))
        for part in fades:
            expr = "min(%s,%s)" % (expr, part)
        opts.append("alpha=%s" % esc("max(0,%s)" % expr))
    return g.add([label], "drawtext=" + ":".join(opts))


def _ass_filter(g, label, path, fontsdir):
    """Burn an ASS file (captions or title template) into the picture."""
    opts = "filename=%s" % esc_path(path)
    if fontsdir:
        opts += ":fontsdir=%s" % esc_path(fontsdir)
    return g.add([label], "ass=" + opts)


def _round_alpha():
    """geq alpha expression for rounded corners (radius 12 % of the shorter side)."""
    r = "(min(W,H)*0.12)"
    return ("if(gt(abs(X-W/2),W/2-%s)*gt(abs(Y-H/2),H/2-%s)*gt(hypot(abs(X-W/2)-(W/2-%s),abs(Y-H/2)-(H/2-%s)),%s),0,255)"
            % (r, r, r, r, r))


def _picture_overlay(g, inputs, label, o, W, H, total, fps=None):
    scale = H / 1080.0
    margin = o["margin"] * scale
    idx = len(inputs)
    width = max(2, int(W * o["scale"]) // 2 * 2)
    start, end = o["start"], o["end"]
    anim = o.get("animate")
    stop = end if end is not None else total
    fades = _anim_fades(anim, start, stop) if anim else []
    grow = _anim_scale(anim, start, stop) if anim else None
    # Size of the finished picture, needed to anchor a pop animation on the picture's centre.
    pw, ph = o["probe"]["width"] or 1, o["probe"]["height"] or 1
    full_h = max(2, int(round(width * ph / float(pw))) // 2 * 2)
    if grow:
        pop_size = "scale=w=%s:h=%s:eval=frame" % (esc("trunc(%d*(%s)/2)*2" % (width, grow)), esc("trunc(%d*(%s)/2)*2" % (full_h, grow)))
    else:
        pop_size = None
    size_filter = pop_size or "scale=%d:-2" % width

    def fade_filters():
        """Alpha fades for the animate option (the picture stream carries its output time stamps)."""
        parts = []
        if anim and anim["in"] in ("fade", "pop"):
            parts.append("fade=t=in:st=%s:d=%s:alpha=1" % (_n(start), _n(anim["duration"])))
        if anim and anim["out"] in ("fade", "pop"):
            parts.append("fade=t=out:st=%s:d=%s:alpha=1" % (_n(max(start, stop - anim["duration"])), _n(anim["duration"])))
        return parts

    if o["type"] == "image":
        # An animated image is a looped input, so every output frame can differ.
        inputs.append((["-loop", "1", "-framerate", fps, "-t", _n(total + 0.5)] if anim and fps else [], o["src"]))
        chain = ["setpts=PTS-STARTPTS+%s/TB" % _n(start)] if anim else []
        chain += ["format=rgba", size_filter]
        if o["opacity"] < 1:
            chain.append("colorchannelmixer=aa=%s" % _n(o["opacity"]))
        if o["fade"] > 0:
            if not anim:
                chain.append("setpts=PTS-STARTPTS+%s/TB" % _n(start))
            chain.append("fade=t=in:st=%s:d=%s:alpha=1" % (_n(start), _n(o["fade"])))
            if end is not None:
                chain.append("fade=t=out:st=%s:d=%s:alpha=1" % (_n(max(start, end - o["fade"])), _n(o["fade"])))
        chain += fade_filters()
        extra = ":eof_action=repeat"
    else:
        opts = ["-ss", _n(o["in"])] if o["in"] > 0 else []
        if end is not None:
            opts += ["-t", _n(end - start + 0.5)]
        inputs.append((opts, o["src"]))
        chain = ["setpts=PTS-STARTPTS+%s/TB" % _n(start), size_filter]
        if o["round"]:
            chain += ["format=yuva420p", "geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='%s'" % _round_alpha()]
        else:
            chain.append("format=yuva420p" if fades else "format=yuv420p")
        chain += fade_filters()
        extra = ":eof_action=pass"
    pic = g.add(["%d:v:0" % idx], chain)
    enable = _enable(start, end)
    xe, ye = _overlay_xy(o["x"], "x", margin), _overlay_xy(o["y"], "y", margin)
    if anim:
        if grow:  # anchor on the final picture's centre while it scales
            xe = "(%s)+(%d-overlay_w)/2" % (xe.replace("overlay_w", str(width)), width)
            ye = "(%s)+(%d-overlay_h)/2" % (ye.replace("overlay_h", str(full_h)), full_h)
        xe, ye = _anim_slide(anim, xe, ye, ("main_w", "main_h", "overlay_w", "overlay_h"), start, stop)
    args = "x=%s:y=%s:format=auto%s" % (esc(xe), esc(ye), extra)
    if enable:
        args += ":enable=%s" % esc(enable)
    return g.add([label, pic], "overlay=" + args)


_ASS_COLOURS = {"white": "FFFFFF", "black": "000000", "yellow": "FFFF00", "red": "FF0000", "green": "00FF00",
                "blue": "0000FF", "cyan": "00FFFF", "magenta": "FF00FF", "orange": "FFA500", "gray": "808080", "grey": "808080"}


def _ass_colour(value):
    base = value.split("@")[0]
    if base.lower() in _ASS_COLOURS:
        rgb = _ASS_COLOURS[base.lower()]
    elif base.startswith("#") and len(base) >= 7:
        rgb = base[1:7].upper()
    elif base.lower().startswith("0x") and len(base) >= 8:
        rgb = base[2:8].upper()
    else:
        raise VSError("subtitles.color: unsupported colour %r; use #RRGGBB or one of %s" % (value, ", ".join(sorted(_ASS_COLOURS))))
    return "&H00%s%s%s" % (rgb[4:6], rgb[2:4], rgb[0:2])


_CUE_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)(\s*-->\s*)(\d+):(\d+):(\d+)[,.](\d+)")


def _clipped_srt(path, total, work):
    """Soft subtitle tracks last as long as their last cue, which would stretch the file.

    For .srt files, write a copy whose cues are clamped to the video length; other
    subtitle formats are used as they are.
    """
    if Path(path).suffix.lower() != ".srt":
        return path
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")

    def seconds(h, m, s, ms):
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000.0

    def stamp(value):
        ms = int(round(value * 1000))
        return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)

    blocks, number = [], 0
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        m = _CUE_RE.search(block)
        if not m:
            continue
        start, end = seconds(*m.group(1, 2, 3, 4)), seconds(*m.group(6, 7, 8, 9))
        if start >= total:
            continue
        number += 1
        body = block[m.end():].lstrip("\n")
        blocks.append("%d\n%s --> %s\n%s" % (number, stamp(start), stamp(min(end, total)), body))
    return work.write("subs.srt", "\n\n".join(blocks) + "\n")


# --------------------------------------------------------------------------- audio mix

def _extra_audio(g, inputs, src, delay, opts_in, seg_len, o, total, vol, loop):
    """Prepare one extra sound (track or picture-in-picture audio) aligned to output time."""
    idx = len(inputs)
    inputs.append((opts_in, src))
    room = max(0.0, total - delay)
    length = room if loop else min(seg_len, room)
    chain = ["aresample=%d" % AUDIO_RATE, "aformat=sample_fmts=fltp:channel_layouts=stereo"]
    if o is not None:
        chain.append("atrim=start=%s:end=%s" % (_n(o["in"]), _n(o["out"])))
    chain.append("asetpts=PTS-STARTPTS")
    if loop:
        chain += ["aloop=loop=-1:size=%d" % (int(seg_len * AUDIO_RATE) + 4800), "asetpts=N/SR/TB"]
    chain.append("atrim=duration=%s" % _n(length))
    if vol != 1.0:
        chain.append("volume=%s" % _n(vol))
    return g.add(["%d:a:0" % idx], chain), length


def _mix_audio(g, inputs, spec, audio_label, total):
    tracks = []  # (label, duck)
    for track in spec["audio"]:
        seg = track["out"] - track["in"]
        label, length = _extra_audio(g, inputs, track["src"], track["start"], [], seg, track, total, track["volume"], track["loop"])
        tail = []
        if track["fade_in"] > 0:
            tail.append("afade=t=in:st=0:d=%s" % _n(track["fade_in"]))
        if track["fade_out"] > 0:
            tail.append("afade=t=out:st=%s:d=%s" % (_n(max(0, length - track["fade_out"])), _n(track["fade_out"])))
        if track["start"] > 0:
            ms = int(round(track["start"] * 1000))
            tail.append("adelay=%d|%d" % (ms, ms))
        if tail:
            label = g.add([label], tail)
        tracks.append((label, track["duck"]))
    return tracks


# --------------------------------------------------------------------------- command construction

def _codec_args(out, container, pix_fmt):
    vcodec = out["vcodec"] or ("vp9" if container == ".webm" else "h264")
    acodec = out["acodec"] or ("opus" if container == ".webm" else "aac")
    if container == ".webm" and (vcodec != "vp9" or acodec != "opus"):
        raise VSError("output: .webm only holds vp9 video and opus audio; set output.vcodec to 'vp9' and output.acodec to 'opus' or choose .mp4/.mkv")
    if vcodec == "prores" and container not in (".mov", ".mkv"):
        raise VSError("output: prores needs a .mov or .mkv output file")
    args = []
    if vcodec == "h264":
        args += ["-c:v", "libx264", "-crf", _n(out["crf"]), "-preset", out["preset"]]
    elif vcodec == "h265":
        args += ["-c:v", "libx265", "-crf", _n(out["crf"]), "-preset", out["preset"], "-x265-params", "log-level=error"]
        if container in (".mp4", ".mov"):
            args += ["-tag:v", "hvc1"]
    elif vcodec == "vp9":
        args += ["-c:v", "libvpx-vp9", "-crf", _n(out["crf"]), "-b:v", "0", "-row-mt", "1"]
    else:
        args += ["-c:v", "prores_ks", "-profile:v", "3"]
    args += ["-pix_fmt", pix_fmt]
    if acodec == "aac":
        args += ["-c:a", "aac", "-b:a", out["audio_bitrate"]]
    else:
        args += ["-c:a", "libopus", "-b:a", out["audio_bitrate"]]
    args += ["-ar", str(AUDIO_RATE), "-ac", "2"]
    if out["faststart"] and container in (".mp4", ".mov"):
        args += ["-movflags", "+faststart"]
    return args


def _build(spec, out_path, work):
    """Return (argv-after-ffmpeg, graph text) for the whole edit, writing to out_path."""
    out = spec["output"]
    items = spec["timeline"]
    W, H = out["width"], out["height"]
    total = spec["duration"]
    container = Path(out_path).suffix.lower()
    pix_fmt = out["pix_fmt"] or ("yuv422p10le" if out["vcodec"] == "prores" else "yuv420p")
    g = _Graph()
    inputs = []  # (input options, path)

    streams = []
    for item in items:
        before = len(inputs)
        v = _item_video(g, inputs, item, out, spec["background"])
        a = _item_audio(g, before, item)
        streams.append((v, a))
    video, audio = _join(g, items, out, streams)

    # Overlays, in order, on top of the finished timeline.
    pip_sounds = []
    for o in spec["overlays"]:
        if o["type"] == "text":
            video = _text_overlay(g, video, o, W, H, work, total)
        elif o["type"] == "title":
            ass, fonts = templates.build_ass(o, W, H, total)
            video = _ass_filter(g, video, work.write("title.ass", ass), fonts)
        else:
            video = _picture_overlay(g, inputs, video, o, W, H, total, "%d/%d" % tuple(out["fps_frac"]))
            if o["type"] == "video" and o["volume"] > 0 and o["probe"]["has_audio"]:
                end = o["end"] if o["end"] is not None else total
                seg = min(end - o["start"], o["probe"]["duration"] - o["in"])
                pip_sounds.append((o, seg))

    sub_input = None
    sub = spec["subtitles"]
    if sub and sub["burn"]:
        style = "FontSize=%s,PrimaryColour=%s" % (_n(sub["size"]), _ass_colour(sub["color"]))
        video = g.add([video], "subtitles=filename=%s:force_style=%s" % (esc_path(sub["file"]), esc(style)))
    cap = spec.get("captions")
    if cap:
        ass, fonts, _ = captions.build_ass(cap, cap["transcript"], W, H)
        video = _ass_filter(g, video, work.write("captions.ass", ass), fonts)
    video = g.add([video], "format=%s" % pix_fmt)

    # Sound: timeline mix + extra tracks (+ ducking) + optional loudness normalisation.
    extras = _mix_audio(g, inputs, spec, audio, total)
    for o, seg in pip_sounds:
        label, _ = _extra_audio(g, inputs, o["src"], o["start"], ["-ss", _n(o["in"])] if o["in"] > 0 else [], seg, None, total, o["volume"], False)
        ms = int(round(o["start"] * 1000))
        if ms:
            label = g.add([label], "adelay=%d|%d" % (ms, ms))
        extras.append((label, False))
    if extras:
        ducks = sum(1 for _, d in extras if d)
        main = audio
        if ducks:
            copies = g.add([audio], "asplit=%d" % (ducks + 1), outputs=ducks + 1)
            main, keys = copies[0], list(copies[1:])
        mixed = []
        for label, duck in extras:
            if duck:
                label = g.add([label, keys.pop(0)], "sidechaincompress=threshold=0.04:ratio=10:attack=15:release=350")
            mixed.append(label)
        audio = g.add([main] + mixed, "amix=inputs=%d:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.97" % (len(mixed) + 1))
    final_audio = []
    if out["loudnorm"]:
        final_audio += ["loudnorm=I=-14:TP=-1.5:LRA=11", "aresample=%d" % AUDIO_RATE]
    final_audio += ["aformat=sample_fmts=fltp:sample_rates=%d:channel_layouts=stereo" % AUDIO_RATE]
    audio = g.add([audio], final_audio)

    graph = g.text()
    args = []
    for opts, path in inputs:
        args += list(opts) + ["-i", path]
    if sub and not sub["burn"]:
        sub_input = len(inputs)
        args += ["-i", str(_clipped_srt(sub["file"], total, work))]
    if len(graph) > LONG_GRAPH:
        script = work.write("graph.txt", graph)
        args += ["-filter_complex_script", str(script)]
    else:
        args += ["-filter_complex", graph]
    args += ["-map", "[%s]" % video, "-map", "[%s]" % audio]
    if sub_input is not None:
        codec = {".mp4": "mov_text", ".mov": "mov_text", ".mkv": "srt", ".webm": "webvtt"}[container]
        args += ["-map", "%d:0" % sub_input, "-c:s", codec]
    args += _codec_args(out, container, pix_fmt)
    args += ["-r", "%d/%d" % tuple(out["fps_frac"]), "-t", _n(total), str(out_path)]
    return args, graph


# --------------------------------------------------------------------------- public entry

def render_spec(spec, out, dry_run=False, overwrite=False):
    """Render `spec` (path, dict or normalised spec) to `out`; return the result dict."""
    ff.require_tools()
    sp = load_spec(spec)
    out = Path(out)
    container = out.suffix.lower()
    if container not in CONTAINERS:
        raise VSError("output: unsupported file type %r; use one of %s" % (out.suffix, ", ".join(CONTAINERS)))
    resolved = out.resolve()
    inputs_used = {Path(i["src"]).resolve() for i in sp["timeline"] if "src" in i}
    inputs_used |= {Path(o["src"]).resolve() for o in sp["overlays"] if "src" in o}
    inputs_used |= {Path(a["src"]).resolve() for a in sp["audio"]}
    if sp["subtitles"]:
        inputs_used.add(Path(sp["subtitles"]["file"]).resolve())
    if resolved in inputs_used:
        raise VSError("refusing to overwrite an input file: %s (choose a different -o)" % out)
    if out.exists() and not overwrite and not dry_run:
        raise VSError("output already exists: %s (use -y/--overwrite to replace it)" % out)
    if out.is_dir():
        raise VSError("output is a directory: %s" % out)

    work = _Work(dry_run)
    try:
        # Encode to a hidden sibling file and move it into place, so a failure never leaves a broken output.
        partial = out.parent / (".%s.partial%s" % (out.name, out.suffix))
        target = out if dry_run else partial
        args, graph = _build(sp, target, work)
        result = {"ok": True, "output": str(out), "duration": round(sp["duration"], 3), "width": sp["output"]["width"],
                  "height": sp["output"]["height"], "fps": round(sp["output"]["fps"], 3)}
        if dry_run:
            shown = ["ffmpeg", "-hide_banner", "-y"] + args
            result.update({"dry_run": True, "commands": [ff.cmdline(shown)], "filter_complex": graph, "size_bytes": 0})
            return result
        out.parent.mkdir(parents=True, exist_ok=True)
        ff.log("rendering %s (%.2f s, %dx%d @ %s fps)" % (out.name, sp["duration"], result["width"], result["height"], result["fps"]))
        try:
            ff.run_ffmpeg(["-y"] + args, duration=sp["duration"])
            os.replace(partial, out)
        finally:
            if partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass
        result["size_bytes"] = out.stat().st_size
        return result
    finally:
        work.cleanup()
