"""One-shot commands. Most build an edit spec and hand it to render.render_spec so behaviour is uniform;
trim --fast, gif, extract-audio, thumbnail, convert, compress, stabilize and split call ffmpeg directly."""
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from .ff import VSError, probe, run_ffmpeg
from .render import render_spec
from .analyze import _flags, find_silences

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


# --------------------------------------------------------------------------- shared helpers

def _dry(a):
    return bool(getattr(a, "dry_run", False))


def _overwrite(a):
    return bool(getattr(a, "overwrite", False) or getattr(a, "yes", False) or getattr(a, "y", False))


def _quiet(a):
    return bool(getattr(a, "quiet", False))


def _abs(p):
    return str(Path(p).expanduser().resolve())


def _need_file(path, what="input file"):
    p = Path(path).expanduser()
    if not p.is_file():
        raise VSError("%s not found: %s" % (what, path))
    return p


def _out_path(a, inputs=()):
    """Validate -o: required, never an input, existing files replaced only with -y. Creates parents (not on --dry-run)."""
    if not getattr(a, "output", None):
        raise VSError("-o OUT is required: this command writes a new file and never overwrites its input")
    out = Path(a.output).expanduser()
    for i in inputs:
        if out.resolve() == Path(i).expanduser().resolve():
            raise VSError("output %s is the same file as the input; choose a different -o" % out)
    if out.exists() and not _overwrite(a) and not _dry(a):
        raise VSError("output already exists: %s (use -y / --overwrite to replace it)" % out)
    if not _dry(a):
        out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _time(v, name):
    """Seconds from a number or [[HH:]MM:]SS[.mmm]."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        total = 0.0
        for part in s.split(":"):
            total = total * 60 + float(part)
        if total < 0:
            raise ValueError
        return total
    except ValueError:
        raise VSError("%s must be seconds or HH:MM:SS.mmm, got %r" % (name, v))


def _ranges(text, name):
    """'0-5,12-20.5' -> [(0.0, 5.0), (12.0, 20.5)]"""
    if not text:
        raise VSError("%s is required, e.g. 0-5,12-20" % name)
    out = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            raise VSError("%s: range %r must look like START-END, e.g. 5-9" % (name, part))
        a, b = part.split("-", 1)
        s, e = _time(a, name), _time(b, name)
        if e <= s:
            raise VSError("%s: range %r ends before it starts" % (name, part))
        out.append((s, e))
    if not out:
        raise VSError("%s is required, e.g. 0-5,12-20" % name)
    return out


def _even(n):
    return max(2, int(round(n / 2.0)) * 2)


def _coord(v, default):
    """CLI x/y: keep keywords (left, center, bottom...), turn numbers into pixels."""
    if v is None:
        return default
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return v


def _spec_result(spec, out, a):
    res = render_spec(spec, str(out), dry_run=_dry(a), overwrite=_overwrite(a))
    res.setdefault("ok", True)
    return res


def _clip(src, **kw):
    d = {"src": _abs(src)}
    d.update({k: v for k, v in kw.items() if v is not None})
    return d


def _single(a, **opts):
    """Spec with one clip. Keys in `clip` go on the clip, the rest are top level (output/overlays/audio...)."""
    src = _need_file(a.file)
    out = _out_path(a, [src])
    clip = opts.pop("clip", {})
    spec = {"timeline": [_clip(src, **clip)]}
    spec.update(opts)
    return _spec_result(spec, out, a)


def _ff(a, cmds, args, duration=None):
    """Run (or, with --dry-run, record) one ffmpeg invocation. `args` excludes the program name."""
    args = [str(x) for x in args]
    if _dry(a):
        cmds.append(["ffmpeg"] + args)
    else:
        run_ffmpeg(args, duration=duration, progress=not _quiet(a))


def _file_result(out, a, cmds, **extra):
    res = {"ok": True, "output": str(out)}
    if _dry(a):
        res["dry_run"] = True
        res["commands"] = cmds
    else:
        res["size_bytes"] = Path(out).stat().st_size
        if Path(out).suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            try:
                res["duration"] = probe(str(out)).get("duration")
            except VSError:
                pass
    res.update(extra)
    return res


# --------------------------------------------------------------------------- cutting and joining

def cmd_trim(a):
    src = _need_file(a.file)
    start, end = _time(a.start, "--start") or 0.0, _time(a.end, "--end")
    if end is not None and end <= start:
        raise VSError("--end (%s) must be after --start (%s)" % (end, start))
    if not a.fast:
        return _single(a, clip={"in": start, "out": end})
    out = _out_path(a, [src])
    cmds = []
    args = ["-y", "-ss", "%.3f" % start, "-i", _abs(src)]
    if end is not None:
        args += ["-t", "%.3f" % (end - start)]
    args += ["-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", str(out)]
    _ff(a, cmds, args, duration=(end - start) if end is not None else None)
    return _file_result(out, a, cmds, fast=True)


def cmd_cut(a):
    src = _need_file(a.file)
    dur = float(probe(str(src))["duration"])
    removes = sorted((max(0.0, s), min(dur, e)) for s, e in _ranges(a.remove, "--remove") if s < dur)
    keep, cursor = [], 0.0
    for s, e in removes:
        if s > cursor + 0.01:
            keep.append((cursor, s))
        cursor = max(cursor, e)
    if dur - cursor > 0.01:
        keep.append((cursor, dur))
    if not keep:
        raise VSError("--remove %s deletes the whole %.2f s video" % (a.remove, dur))
    out = _out_path(a, [src])
    return _spec_result({"timeline": [_clip(src, **{"in": s, "out": e}) for s, e in keep]}, out, a)


def cmd_keep(a):
    src = _need_file(a.file)
    dur = float(probe(str(src))["duration"])
    ranges = _ranges(a.ranges, "--ranges")
    for s, e in ranges:
        if s >= dur:
            raise VSError("--ranges: %s starts after the end of the video (%.2f s)" % (s, dur))
    out = _out_path(a, [src])
    return _spec_result({"timeline": [_clip(src, **{"in": s, "out": min(e, dur)}) for s, e in ranges]}, out, a)


def cmd_concat(a):
    files = [_need_file(f) for f in a.files]
    out = _out_path(a, files)
    items = []
    for f in files:
        if f.suffix.lower() in IMAGE_EXTS:
            items.append({"image": _abs(f), "duration": 3})
        else:
            items.append(_clip(f))
    if a.transition:
        d = float(a.duration)
        for it in items[1:]:
            it["transition"] = {"type": a.transition, "duration": d}
    return _spec_result({"timeline": items}, out, a)


def cmd_speed(a):
    f = float(a.factor)
    if not 0.25 <= f <= 4:
        raise VSError("--factor must be between 0.25 and 4 (got %s); run speed twice for more" % a.factor)
    return _single(a, clip={"speed": f})


def cmd_reverse(a):
    return _single(a, clip={"reverse": True})


def cmd_remove_silence(a):
    src = _need_file(a.file)
    info = probe(str(src))
    if not info.get("has_audio"):
        raise VSError("%s has no audio track, so there is no silence to remove" % a.file)
    dur = float(info["duration"])
    _sil, speech = find_silences(src, float(a.noise), float(a.min), info)
    pad = float(a.pad)
    padded = []
    for s in speech:
        s0, e0 = max(0.0, s["start"] - pad), min(dur, s["end"] + pad)
        if padded and s0 <= padded[-1][1] + 0.001:
            padded[-1][1] = max(padded[-1][1], e0)
        else:
            padded.append([s0, e0])
    if not padded:
        raise VSError("the whole file is quieter than %s dB; lower --noise or raise it to keep something" % a.noise)
    out = _out_path(a, [src])
    kept = sum(e - s for s, e in padded)
    res = _spec_result({"timeline": [_clip(src, **{"in": round(s, 3), "out": round(e, 3)}) for s, e in padded]}, out, a)
    res["removed_seconds"] = round(dur - kept, 3)
    res["segments_kept"] = len(padded)
    return res


# --------------------------------------------------------------------------- picture

def cmd_resize(a):
    src = _need_file(a.file)
    info = probe(str(src))
    sw, sh = info.get("width"), info.get("height")
    if not sw or not sh:
        raise VSError("%s has no video picture to resize" % a.file)
    if a.aspect:
        try:
            aw, ah = [float(x) for x in a.aspect.split(":")]
            ratio = aw / ah
        except (ValueError, ZeroDivisionError):
            raise VSError("--aspect must look like 9:16, 1:1, 16:9 or 4:5 (got %r)" % a.aspect)
        short = min(sw, sh)
        w, h = (short, short / ratio) if ratio < 1 else (short * ratio, short)
        w, h = _even(w), _even(h)
    elif a.width or a.height:
        if a.width and a.height:
            w, h = _even(a.width), _even(a.height)
        elif a.width:
            w, h = _even(a.width), _even(a.width * sh / float(sw))
        else:
            w, h = _even(a.height * sw / float(sh)), _even(a.height)
    else:
        raise VSError("resize needs --width/--height or --aspect")
    return _single(a, clip={"fit": a.fit}, output={"width": w, "height": h})


def cmd_crop(a):
    src = _need_file(a.file)
    info = probe(str(src))
    x, y, w, h = float(a.x), float(a.y), float(a.w), float(a.h)
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
        raise VSError("crop values are fractions of the picture: need 0<=x,y<1, 0<w,h<=1, x+w<=1 and y+h<=1 "
                      "(got x=%s y=%s w=%s h=%s)" % (x, y, w, h))
    ow, oh = _even(info["width"] * w), _even(info["height"] * h)
    return _single(a, clip={"crop": {"x": x, "y": y, "w": w, "h": h}}, output={"width": ow, "height": oh})


def cmd_rotate(a):
    src = _need_file(a.file)
    if not (a.deg or a.hflip or a.vflip):
        raise VSError("rotate needs --deg 90|180|270, --hflip or --vflip")
    clip, output = {}, {}
    if a.deg:
        if int(a.deg) not in (90, 180, 270):
            raise VSError("--deg must be 90, 180 or 270 (got %s)" % a.deg)
        clip["rotate"] = int(a.deg)
        if int(a.deg) in (90, 270):
            info = probe(str(src))
            output = {"width": _even(info["height"]), "height": _even(info["width"])}
    if a.hflip:
        clip["hflip"] = True
    if a.vflip:
        clip["vflip"] = True
    return _single(a, clip=clip, **({"output": output} if output else {}))


def cmd_color(a):
    color = {}
    for k in ("brightness", "contrast", "saturation", "gamma", "temperature"):
        v = getattr(a, k)
        if v is not None:
            color[k] = float(v)
    if not (color or a.grayscale or a.lut):
        raise VSError("color needs at least one of --brightness --contrast --saturation --gamma --temperature "
                      "--grayscale --lut")
    clip = {}
    if color:
        clip["color"] = color
    if a.grayscale:
        clip["grayscale"] = True
    if a.lut:
        clip["lut"] = _abs(_need_file(a.lut, "LUT file"))
    return _single(a, clip=clip)


def cmd_denoise(a):
    return _single(a, clip={"denoise": True})


def cmd_text(a):
    ov = {"type": "text", "text": a.text.replace("\\n", "\n"), "start": _time(a.start, "--start") or 0.0,
          "end": _time(a.end, "--end"), "x": _coord(a.x, "center"), "y": _coord(a.y, "center"),
          "size": int(a.size), "color": a.color, "box": bool(a.box)}
    if a.box_color:
        ov["box_color"] = a.box_color
    if a.font:
        ov["font"] = a.font
    if a.margin is not None:
        ov["margin"] = int(a.margin)
    return _single(a, overlays=[ov])


def _margin(a, default=40):
    """Margin in px at 1080p; the renderer scales it to the output height."""
    return default if a.margin is None else float(a.margin)


def cmd_watermark(a):
    src = _need_file(a.file)
    img = _need_file(a.image, "watermark image")
    ov = {"type": "image", "src": _abs(img), "start": 0, "end": None, "x": _coord(a.x, "right"),
          "y": _coord(a.y, "top"), "margin": _margin(a), "scale": float(a.scale), "opacity": float(a.opacity)}
    return _single(a, overlays=[ov])


def cmd_pip(a):
    src = _need_file(a.file)
    ov_src = _need_file(a.overlay, "overlay video")
    ov = {"type": "video", "src": _abs(ov_src), "in": 0, "start": _time(a.start, "--start") or 0.0,
          "end": _time(a.end, "--end"), "x": _coord(a.x, "right"), "y": _coord(a.y, "bottom"),
          "margin": _margin(a), "scale": float(a.scale), "volume": float(a.volume), "round": bool(a.round)}
    return _single(a, overlays=[ov])


def cmd_subtitles(a):
    _need_file(a.file)
    srt = _need_file(a.srt, "subtitle file")
    sub = {"file": _abs(srt), "burn": not a.soft}
    if a.size is not None:
        sub["size"] = int(a.size)
    if a.color:
        sub["color"] = a.color
    return _single(a, subtitles=sub)


def cmd_fade(a):
    fin, fout = a.fade_in, a.fade_out
    if fin is None and fout is None:
        fin = fout = 1.0
    return _single(a, clip={"fade_in": float(fin or 0), "fade_out": float(fout or 0)})


# --------------------------------------------------------------------------- sound

def cmd_music(a):
    src = _need_file(a.file)
    music = _need_file(a.audio, "audio file")
    dur = float(probe(str(src))["duration"])
    track = {"src": _abs(music), "start": 0, "in": 0, "out": None, "volume": float(a.volume), "loop": True,
             "fade_in": 0, "fade_out": round(min(1.0, dur / 3.0), 2), "duck": bool(a.duck)}
    if a.replace and a.duck:
        raise VSError("--duck needs the original sound; drop --replace or --duck")
    return _single(a, clip={"mute": True} if a.replace else {}, audio=[track])


def cmd_mute(a):
    return _single(a, clip={"mute": True})


def cmd_volume(a):
    g = float(a.gain)
    if g < 0:
        raise VSError("--gain must be 0 or more (1 = unchanged)")
    return _single(a, clip={"volume": g})


def cmd_normalize(a):
    src = _need_file(a.file)
    if not probe(str(src)).get("has_audio"):
        raise VSError("%s has no audio track to normalize" % a.file)
    return _single(a, output={"loudnorm": True})


# --------------------------------------------------------------------------- direct ffmpeg

def cmd_stabilize(a):
    src = _need_file(a.file)
    out = _out_path(a, [src])
    probe(str(src))
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True).stdout
    if "vidstabdetect" not in filters or "vidstabtransform" not in filters:
        raise VSError("this ffmpeg build lacks the vidstab filters (libvidstab). Install an ffmpeg with libvidstab "
                      "(e.g. apt install ffmpeg on Debian/Ubuntu, brew install ffmpeg, or a full gyan.dev build on Windows)")
    tmp = Path(tempfile.mkdtemp(prefix="videoswami_stab_"))
    try:
        trf = str(tmp / "transforms.trf").replace("\\", "/").replace(":", "\\:")
        cmds = []
        _ff(a, cmds, ["-y", "-i", _abs(src), "-an", "-vf", "vidstabdetect=shakiness=5:accuracy=15:result='%s'" % trf,
                      "-f", "null", "-"])
        _ff(a, cmds, ["-y", "-i", _abs(src), "-vf",
                      "vidstabtransform=input='%s':smoothing=15:zoom=0:optzoom=1,unsharp=5:5:0.8:3:3:0.4" % trf,
                      "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
        return _file_result(out, a, cmds)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def cmd_compress(a):
    src = _need_file(a.file)
    out = _out_path(a, [src])
    info = probe(str(src))
    if not info.get("has_video"):
        raise VSError("%s has no video to compress" % a.file)
    if not a.target_mb and a.crf is None:
        raise VSError("compress needs --target-mb SIZE or --crf N (e.g. --target-mb 25 or --crf 28)")
    vf = ["scale=-2:%d" % _even(a.height)] if a.height else []
    vf.append("format=yuv420p")
    has_a = bool(info.get("has_audio"))
    cmds = []
    if a.target_mb:
        dur = float(info["duration"])
        if dur <= 0:
            raise VSError("cannot size a two-pass encode: %s has no duration" % a.file)
        limit = float(a.target_mb) * 1024 * 1024
        tmp = Path(tempfile.mkdtemp(prefix="videoswami_2pass_"))
        try:
            safety = 0.92
            for attempt in range(4):
                total = limit * 8 * safety / dur
                abps = min(128000.0, max(24000.0, total * 0.15)) if has_a else 0.0
                vbps = total - abps
                if vbps < 15000:
                    raise VSError("--target-mb %s is too small for %.1f s of video (needs at least ~%.2f MB)" % (
                        a.target_mb, dur, (15000 + abps) * dur / 8 / 1024 / 1024 / safety))
                common = ["-i", _abs(src), "-map", "0:v:0", "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium",
                          "-b:v", "%d" % vbps, "-passlogfile", str(tmp / "pass")]
                _ff(a, cmds, ["-y"] + common + ["-pass", "1", "-an", "-f", "null", "-"], duration=dur)
                audio = ["-map", "0:a:0", "-c:a", "aac", "-b:a", "%d" % abps] + (["-ac", "1"] if abps < 48000 else []) \
                    if has_a else ["-an"]
                _ff(a, cmds, ["-y"] + common + ["-pass", "2"] + audio + ["-movflags", "+faststart", str(out)], duration=dur)
                if _dry(a) or out.stat().st_size <= limit:
                    break
                safety *= limit / out.stat().st_size * 0.97  # overshot: retry with a smaller bitrate budget
            else:
                raise VSError("could not get the file under %s MB (best attempt %.2f MB); try a smaller --height" % (
                    a.target_mb, out.stat().st_size / 1024.0 / 1024))
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)
    else:
        args = ["-y", "-i", _abs(src), "-map", "0:v:0", "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium",
                "-crf", str(int(a.crf))]
        args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"] if has_a else ["-an"]
        _ff(a, cmds, args + ["-movflags", "+faststart", str(out)], duration=info.get("duration"))
    return _file_result(out, a, cmds)


def cmd_gif(a):
    src = _need_file(a.file)
    out = _out_path(a, [src])
    if out.suffix.lower() != ".gif":
        raise VSError("gif output must end in .gif (got %s)" % out.name)
    info = probe(str(src))
    start = _time(a.start, "--start") or 0.0
    end = _time(a.end, "--end")
    length = (end if end is not None else float(info["duration"])) - start
    if length <= 0:
        raise VSError("--end must be after --start")
    graph = ("fps=%s,scale=w='min(%d,iw)':h=-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];"
             "[b][p]paletteuse=dither=bayer:bayer_scale=5" % (a.fps, int(a.width)))
    cmds = []
    _ff(a, cmds, ["-y", "-ss", "%.3f" % start, "-t", "%.3f" % length, "-i", _abs(src), "-an", "-vf", graph,
                  "-loop", "0", str(out)], duration=length)
    return _file_result(out, a, cmds, duration=round(length, 3))


AUDIO_CODECS = {".mp3": ["-c:a", "libmp3lame", "-q:a", "2"], ".wav": ["-c:a", "pcm_s16le"],
                ".m4a": ["-c:a", "aac", "-b:a", "192k"], ".aac": ["-c:a", "aac", "-b:a", "192k"],
                ".flac": ["-c:a", "flac"], ".ogg": ["-c:a", "libvorbis", "-q:a", "5"],
                ".opus": ["-c:a", "libopus", "-b:a", "128k"]}


def cmd_extract_audio(a):
    src = _need_file(a.file)
    out = _out_path(a, [src])
    codec = AUDIO_CODECS.get(out.suffix.lower())
    if codec is None:
        raise VSError("extract-audio output must end in one of %s (got %s)" % (" ".join(sorted(AUDIO_CODECS)), out.name))
    info = probe(str(src))
    if not info.get("has_audio"):
        raise VSError("%s has no audio track to extract" % a.file)
    cmds = []
    _ff(a, cmds, ["-y", "-i", _abs(src), "-vn", "-map", "0:a:0"] + codec + [str(out)], duration=info.get("duration"))
    return _file_result(out, a, cmds)


def cmd_thumbnail(a):
    src = _need_file(a.file)
    out = _out_path(a, [src])
    info = probe(str(src))
    if not info.get("has_video"):
        raise VSError("%s has no video picture" % a.file)
    dur = float(info.get("duration") or 0)
    at = _time(a.at, "--at") if a.at is not None else dur / 3.0
    if at < 0 or (dur and at > dur):
        raise VSError("--at %s is outside the video (0 to %.2f s)" % (at, dur))
    at = min(at, max(0.0, dur - 0.05))
    cmds = []
    _ff(a, cmds, ["-y", "-ss", "%.3f" % at, "-i", _abs(src), "-frames:v", "1", "-q:v", "2", str(out)])
    return _file_result(out, a, cmds, at=round(at, 3))


def cmd_convert(a):
    src = _need_file(a.file)
    out = _out_path(a, [src])
    info = probe(str(src))
    if not info.get("has_video"):
        raise VSError("%s has no video; use extract-audio for sound only" % a.file)
    ext = out.suffix.lower()
    if ext == ".webm":
        v = ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-row-mt", "1", "-cpu-used", "4", "-pix_fmt", "yuv420p"]
        au = ["-c:a", "libopus", "-b:a", "128k"]
    elif ext in (".mp4", ".mov", ".mkv"):
        v = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
        au = ["-c:a", "aac", "-b:a", "192k"]
        if ext != ".mkv":
            au += ["-movflags", "+faststart"]
    else:
        raise VSError("convert output must end in .mp4, .mov, .mkv or .webm (got %s)" % out.name)
    args = ["-y", "-i", _abs(src), "-map", "0:v:0"] + (["-map", "0:a:0"] if info.get("has_audio") else []) + v
    args += au if info.get("has_audio") else (["-movflags", "+faststart"] if ext in (".mp4", ".mov") else [])
    cmds = []
    _ff(a, cmds, args + [str(out)], duration=info.get("duration"))
    return _file_result(out, a, cmds)


def cmd_split(a):
    src = _need_file(a.file)
    info = probe(str(src))
    dur = float(info["duration"])
    if not a.output:
        raise VSError("-o DIR is required: the directory that will receive part_001.mp4, part_002.mp4, ...")
    out_dir = Path(a.output).expanduser()
    if bool(a.every) == bool(a.at):
        raise VSError("split needs exactly one of --every SECONDS or --at T1,T2,...")
    if a.every:
        step = _time(a.every, "--every")
        if step <= 0:
            raise VSError("--every must be greater than 0")
        cuts = [step * i for i in range(1, int(math.ceil(dur / step)) + 1) if step * i < dur - 0.05]
    else:
        cuts = sorted(_time(t, "--at") for t in str(a.at).split(",") if t.strip())
        if any(c <= 0 or c >= dur for c in cuts):
            raise VSError("--at times must be inside the video (0 to %.2f s)" % dur)
    bounds = [0.0] + cuts + [dur]
    outputs = [out_dir / ("part_%03d.mp4" % (i + 1)) for i in range(len(bounds) - 1)]
    if not _dry(a):
        clash = [p for p in outputs if p.exists()]
        if clash and not _overwrite(a):
            raise VSError("output already exists: %s (use -y / --overwrite to replace)" % clash[0])
        out_dir.mkdir(parents=True, exist_ok=True)
    cmds = []
    for (s, e), p in zip(zip(bounds[:-1], bounds[1:]), outputs):
        args = ["-y", "-ss", "%.3f" % s, "-i", _abs(src), "-t", "%.3f" % (e - s), "-map", "0:v:0"]
        args += ["-map", "0:a:0"] if info.get("has_audio") else []
        args += ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
        args += ["-c:a", "aac", "-b:a", "192k"] if info.get("has_audio") else []
        _ff(a, cmds, args + ["-movflags", "+faststart", str(p)], duration=e - s)
    res = {"ok": True, "output": str(out_dir), "outputs": [str(p) for p in outputs], "count": len(outputs),
           "duration": round(dur, 3)}
    if _dry(a):
        res["dry_run"] = True
        res["commands"] = cmds
    return res


def cmd_slideshow(a):
    imgs = [_need_file(i, "image") for i in a.images]
    out = _out_path(a, imgs)
    each = float(a.each)
    first = probe(str(imgs[0]))
    w, h = first.get("width") or 1920, first.get("height") or 1080
    scale = min(1.0, 1920.0 / w, 1920.0 / h)
    output = {"width": _even(w * scale), "height": _even(h * scale), "fps": 30}
    items = []
    for i, img in enumerate(imgs):
        it = {"image": _abs(img), "duration": each, "fit": "blur"}
        if a.zoom:
            it["zoom"] = {"from": 1.0, "to": 1.15}
        if a.transition and i > 0:
            it["transition"] = {"type": a.transition, "duration": float(a.duration)}
        items.append(it)
    spec = {"output": output, "timeline": items}
    if a.audio:
        spec["audio"] = [{"src": _abs(_need_file(a.audio, "audio file")), "start": 0, "volume": 1.0, "loop": True,
                          "fade_out": 1.0}]
    return _spec_result(spec, out, a)


# --------------------------------------------------------------------------- registration

def register(subparsers):
    def add(name, fn, help_text, files=True, out=True):
        p = subparsers.add_parser(name, help=help_text)
        if files:
            p.add_argument("file", metavar="FILE")
        if out:
            p.add_argument("-o", "--output", help="output file (required)")
        p.set_defaults(func=fn)
        _flags(p)
        return p

    p = add("trim", cmd_trim, "keep one time range")
    p.add_argument("--start", default=0, help="start time (default 0)")
    p.add_argument("--end", help="end time (default end of file)")
    p.add_argument("--fast", action="store_true", help="stream copy: instant but keyframe-accurate only")

    add("cut", cmd_cut, "delete ranges and join the rest").add_argument("--remove", required=True, help="e.g. 5-9,20-22.5")
    add("keep", cmd_keep, "keep only these ranges, joined").add_argument("--ranges", required=True, help="e.g. 0-5,12-20")

    p = add("concat", cmd_concat, "join files one after another", files=False)
    p.add_argument("files", nargs="+", metavar="FILE")
    p.add_argument("--transition", help="xfade name between clips: fade, wipeleft, slideup, dissolve, ...")
    p.add_argument("--duration", type=float, default=0.5, help="transition length in seconds (default 0.5)")

    add("speed", cmd_speed, "change playback speed").add_argument("--factor", type=float, required=True, help="0.25 to 4")
    add("reverse", cmd_reverse, "play backwards")

    p = add("resize", cmd_resize, "change size or aspect ratio")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--aspect", help="9:16, 1:1, 16:9, 4:5 (keeps the short side)")
    p.add_argument("--fit", default="contain", choices=["cover", "contain", "blur"], help="how the picture fills the frame")

    p = add("crop", cmd_crop, "crop to a rectangle given as fractions of the picture")
    p.add_argument("--x", type=float, default=0.0)
    p.add_argument("--y", type=float, default=0.0)
    p.add_argument("--w", type=float, default=1.0)
    p.add_argument("--h", type=float, default=1.0)

    p = add("rotate", cmd_rotate, "rotate or flip")
    p.add_argument("--deg", type=int, choices=[90, 180, 270])
    p.add_argument("--hflip", action="store_true")
    p.add_argument("--vflip", action="store_true")

    p = add("color", cmd_color, "colour correction")
    for k in ("brightness", "contrast", "saturation", "gamma", "temperature"):
        p.add_argument("--" + k, type=float)
    p.add_argument("--grayscale", action="store_true")
    p.add_argument("--lut", help=".cube LUT file")

    p = add("text", cmd_text, "burn text onto the picture")
    p.add_argument("--text", required=True)
    p.add_argument("--start", default=0)
    p.add_argument("--end")
    p.add_argument("--x", help="left|center|right|<px> (default center)")
    p.add_argument("--y", help="top|center|bottom|<px> (default center)")
    p.add_argument("--size", type=int, default=64, help="px at 1080p height")
    p.add_argument("--color", default="white")
    p.add_argument("--box", action="store_true", help="draw a box behind the text")
    p.add_argument("--box-color", dest="box_color")
    p.add_argument("--font")
    p.add_argument("--margin", type=int)

    p = add("watermark", cmd_watermark, "overlay a logo image")
    p.add_argument("--image", required=True)
    p.add_argument("--x", help="left|center|right|<px> (default right)")
    p.add_argument("--y", help="top|center|bottom|<px> (default top)")
    p.add_argument("--scale", type=float, default=0.15, help="logo width as a fraction of the picture width")
    p.add_argument("--opacity", type=float, default=0.9)
    p.add_argument("--margin", type=float, help="px at 1080p (default 40)")

    p = add("pip", cmd_pip, "picture-in-picture")
    p.add_argument("--overlay", required=True, help="the small video")
    p.add_argument("--x", help="left|center|right|<px> (default right)")
    p.add_argument("--y", help="top|center|bottom|<px> (default bottom)")
    p.add_argument("--scale", type=float, default=0.3)
    p.add_argument("--start", default=0)
    p.add_argument("--end")
    p.add_argument("--volume", type=float, default=0.0, help="overlay sound level (default muted)")
    p.add_argument("--margin", type=float, help="px at 1080p (default 40)")
    p.add_argument("--round", action="store_true", help="round mask")

    p = add("music", cmd_music, "add a music track")
    p.add_argument("--audio", required=True)
    p.add_argument("--volume", type=float, default=0.25)
    p.add_argument("--duck", action="store_true", help="lower the music under the original sound")
    p.add_argument("--replace", action="store_true", help="drop the original sound")

    add("mute", cmd_mute, "remove the sound")
    add("volume", cmd_volume, "change loudness by a gain factor").add_argument("--gain", type=float, required=True)
    add("normalize", cmd_normalize, "loudness to -14 LUFS")

    p = add("fade", cmd_fade, "fade picture and sound in and/or out")
    p.add_argument("--in", dest="fade_in", type=float, help="fade-in seconds")
    p.add_argument("--out", dest="fade_out", type=float, help="fade-out seconds")

    p = add("subtitles", cmd_subtitles, "burn in or attach subtitles")
    p.add_argument("--srt", required=True)
    p.add_argument("--soft", action="store_true", help="separate subtitle track instead of burning in")
    p.add_argument("--size", type=int)
    p.add_argument("--color")

    p = add("remove-silence", cmd_remove_silence, "jump-cut the silent parts")
    p.add_argument("--noise", type=float, default=-35.0, help="dB threshold (default -35)")
    p.add_argument("--min", type=float, default=0.6, help="shortest silence to cut, seconds (default 0.6)")
    p.add_argument("--pad", type=float, default=0.1, help="keep this much around speech, seconds (default 0.1)")

    add("stabilize", cmd_stabilize, "two-pass vidstab stabilisation")
    add("denoise", cmd_denoise, "reduce noise")

    p = add("compress", cmd_compress, "shrink the file")
    p.add_argument("--target-mb", dest="target_mb", type=float, help="two-pass bitrate so the file lands under this size")
    p.add_argument("--crf", type=int, help="quality instead of a size target (higher = smaller)")
    p.add_argument("--height", type=int, help="also scale down to this height")

    p = add("gif", cmd_gif, "make an animated gif")
    p.add_argument("--start", default=0)
    p.add_argument("--end")
    p.add_argument("--width", type=int, default=480)
    p.add_argument("--fps", type=int, default=12)

    add("extract-audio", cmd_extract_audio, "write the sound track (.mp3 .wav .m4a ...)")
    add("thumbnail", cmd_thumbnail, "save one frame as an image").add_argument("--at", help="time (default one third in)")
    add("convert", cmd_convert, "re-wrap/encode to .mp4 .mov .mkv .webm")

    p = add("slideshow", cmd_slideshow, "make a video from images", files=False)
    p.add_argument("images", nargs="+", metavar="IMG")
    p.add_argument("--each", type=float, default=3.0, help="seconds per image (default 3)")
    p.add_argument("--transition", help="xfade name between images")
    p.add_argument("--duration", type=float, default=0.5, help="transition length (default 0.5)")
    p.add_argument("--zoom", action="store_true", help="slow push-in on every image")
    p.add_argument("--audio", help="music track")

    p = add("split", cmd_split, "cut into parts: part_001.mp4 ...")
    p.add_argument("--every", help="part length in seconds")
    p.add_argument("--at", help="cut points, e.g. 10,25")
