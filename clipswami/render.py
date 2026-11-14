"""Rendering: cut, reframe, tighten dead air, caption, normalise loudness, encode."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path

from . import captions, ff, reframe
from . import transcript as tr

ASPECTS = {"9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5), "16:9": (16, 9)}


@dataclass
class Options:
    aspect: str = "9:16"        # 9:16 | 1:1 | 4:5 | 16:9 | source
    fit: str = "track"          # track (follow the action) | center | blur (letterbox on blur)
    res: int = 1080             # short side of the output, in pixels
    captions: str = "bold"      # bold | clean | off
    tighten: bool = True        # jump-cut long pauses out of the clip
    loudnorm: bool = True       # normalise to -14 LUFS
    title_secs: float = 3.5
    progress: bool = False      # thin progress bar along the bottom edge
    font: str = "DejaVu Sans"
    crf: int = 20
    preset: str = "medium"
    max_fps: float = 60.0

    @classmethod
    def from_dict(cls, data: dict) -> "Options":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names and v is not None})

    def merged(self, overrides: dict) -> "Options":
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data.update({k: v for k, v in overrides.items() if k in data and v is not None})
        return Options(**data)


def _even(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


def output_size(aspect: str, res: int, src_w: int, src_h: int) -> tuple[int, int]:
    if aspect == "source":
        scale = min(1.0, res / min(src_w, src_h))
        return _even(src_w * scale), _even(src_h * scale)
    if aspect not in ASPECTS:
        raise ff.ClipError(f"unknown aspect {aspect!r} - use one of {', '.join(ASPECTS)} or source")
    aw, ah = ASPECTS[aspect]
    if aw <= ah:
        return _even(res), _even(res * ah / aw)
    return _even(res * aw / ah), _even(res)


def keep_segments(silences: list, start: float, end: float, min_gap: float = 0.6,
                  pad: float = 0.18, limit: int = 40) -> list[tuple[float, float]]:
    """Clip-relative spans to keep after removing long pauses. [] means keep everything."""
    length = end - start
    removals: list[tuple[float, float]] = []
    for a, b in silences:
        a, b = max(a, start) - start, min(b, end) - start
        if b - a < min_gap:
            continue
        cut_a = 0.0 if a <= 0.05 else a + pad
        cut_b = length if b >= length - 0.05 else b - pad
        if cut_b >= length:                                  # keep a little tail room
            cut_a = max(cut_a, a + 0.3)
        if cut_b - cut_a >= 0.2:
            removals.append((cut_a, cut_b))
    if len(removals) > limit - 1:
        removals = sorted(sorted(removals, key=lambda r: r[1] - r[0], reverse=True)[:limit - 1])
    if sum(b - a for a, b in removals) < 0.5:
        return []
    keeps, cursor = [], 0.0
    for a, b in removals:
        if a - cursor > 0.05:
            keeps.append((round(cursor, 3), round(a, 3)))
        cursor = b
    if length - cursor > 0.05:
        keeps.append((round(cursor, 3), round(length, 3)))
    return keeps if len(keeps) >= 1 and keeps != [(0.0, round(length, 3))] else []


def remapper(keeps: list[tuple[float, float]]):
    """Map a clip-relative time to its position after the kept segments are joined."""
    if not keeps:
        return lambda t: t

    def remap(t: float) -> float:
        acc = 0.0
        for a, b in keeps:
            if t < a:
                return acc
            if t <= b:
                return acc + (t - a)
            acc += b - a
        return acc
    return remap


def measure_loudness(path: str, start: float, duration: float) -> str | None:
    """First pass of a two-pass EBU R128 normalise. Returns the loudnorm filter to apply.

    Measuring first lets the second pass apply one clean linear gain instead of
    riding the level dynamically. Returns None for silent audio (nothing to normalise).
    """
    base = "loudnorm=I=-14:TP=-1.5:LRA=11"
    proc = ff.run([ff.need("ffmpeg"), "-hide_banner", "-nostats", "-ss", f"{start:.3f}",
                   "-t", f"{duration:.3f}", "-i", path, "-vn", "-sn",
                   "-af", base + ":print_format=json", "-f", "null", "-"], check=False)
    found = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr or "")
    if proc.returncode != 0 or not found:
        return base
    try:
        m = json.loads(found[-1])
        if float(m["input_i"]) < -70:
            return None
        return (f"{base}:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
                f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
                f":offset={m['target_offset']}:linear=true")
    except (ValueError, KeyError):
        return base


def _reframe_filter(info: dict, start: float, duration: float, width: int, height: int,
                    opts: Options) -> str:
    src_w, src_h = info["width"], info["height"]
    src_ar, out_ar = src_w / src_h, width / height
    scale = f"scale={width}:{height}:flags=lanczos,setsar=1"
    if abs(src_ar - out_ar) / out_ar < 0.01:
        return scale
    if opts.fit == "blur":
        return (
            f"split[bgsrc][fgsrc];"
            f"[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=28,eq=brightness=-0.1[bg];"
            f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    if src_ar > out_ar:
        crop_w, crop_h = min(src_w, _even(src_h * out_ar)), src_h
    else:
        crop_w, crop_h = src_w, min(src_h, _even(src_w / out_ar))
    x_expr, y_expr = reframe.plan_crop(info["path"], start, duration, src_w, src_h,
                                       crop_w, crop_h, track=(opts.fit == "track"))
    return f"crop={crop_w}:{crop_h}:x='{x_expr}':y='{y_expr}',{scale}"


def render_clip(analysis: dict, cues: list[dict], clip: dict, opts: Options,
                out_path: Path) -> dict:
    """Render one clip described by {start, end, title?}. Returns facts about the output."""
    info = analysis["info"]
    start = max(0.0, ff.parse_time(clip["start"]))
    end = min(info["duration"], ff.parse_time(clip["end"]))
    if end - start < 0.5:
        raise ff.ClipError(f"clip {clip.get('title') or ''} is empty: {start:.2f} -> {end:.2f}")
    duration = end - start
    width, height = output_size(opts.aspect, opts.res, info["width"], info["height"])
    fps = min(info["fps"], opts.max_fps)

    keeps = keep_segments(analysis["silences"], start, end) if opts.tighten and info["has_audio"] else []
    keeps = [(round(a * fps) / fps, round(b * fps) / fps) for a, b in keeps]
    final = sum(b - a for a, b in keeps) if keeps else duration
    remap = remapper(keeps)

    tmp = Path(tempfile.mkdtemp(prefix="clipswami-"))
    try:
        graph = [f"[0:v]fps={fps:.3f},{_reframe_filter(info, start, duration, width, height, opts)}[vr]"]
        v_label, a_label = "vr", None
        if info["has_audio"]:
            graph.append("[0:a]aresample=48000[ar]")
            a_label = "ar"

        if keeps:
            # One pass, no buffering: keep only frames/samples inside the kept spans and
            # re-stamp them. Spans sit on the frame grid so picture and sound stay locked.
            keep_expr = "+".join(f"gte(t,{a:.4f})*lt(t,{b:.4f})" for a, b in keeps)
            graph.append(f"[{v_label}]select='{keep_expr}',setpts=N/FRAME_RATE/TB[vc]")
            v_label = "vc"
            if a_label:
                graph.append(f"[{a_label}]asetnsamples=n=480:p=0,aselect='{keep_expr}',"
                             f"asetpts=N/SR/TB[ac]")
                a_label = "ac"

        video_tail = []
        clip_cues = tr.window(cues, start, end) if opts.captions != "off" else []
        title = (clip.get("title") or "").strip() if opts.title_secs > 0 else ""
        if clip_cues or title:
            (tmp / "captions.ass").write_text(captions.build_ass(
                clip_cues, width, height, style=opts.captions, title=title or None,
                title_secs=opts.title_secs, font=opts.font, remap=remap, total=final),
                encoding="utf-8")
            video_tail.append("ass=captions.ass")
        fade_out = max(0.0, final - 0.2)
        video_tail += ["fade=t=in:st=0:d=0.12", f"fade=t=out:st={fade_out:.3f}:d=0.2",
                       "format=yuv420p"]
        if opts.progress:
            bar = max(4, _even(height * 0.006))
            graph.append(f"color=c=0xFFE500:s={width}x{bar}:r={fps:.3f}:d={final:.3f}[bar]")
            graph.append(f"[{v_label}][bar]overlay=x='-w+w*t/{final:.3f}':y=H-h:shortest=1[vp]")
            v_label = "vp"
        graph.append(f"[{v_label}]{','.join(video_tail)}[vout]")

        cmd = [ff.need("ffmpeg"), "-y", "-hide_banner", "-v", "error",
               "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", info["path"]]
        if a_label:
            audio_tail = []
            if opts.loudnorm:
                loud = measure_loudness(info["path"], start, duration)
                if loud:
                    audio_tail.append(loud)
            audio_tail += ["aresample=48000", "afade=t=in:st=0:d=0.05",
                           f"afade=t=out:st={max(0.0, final - 0.15):.3f}:d=0.15"]
            graph.append(f"[{a_label}]{','.join(audio_tail)}[aout]")
        cmd += ["-filter_complex", ";".join(graph), "-map", "[vout]"]
        if a_label:
            cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
        cmd += ["-c:v", "libx264", "-crf", str(opts.crf), "-preset", opts.preset,
                "-movflags", "+faststart", "-map_metadata", "-1", "-sn", "-dn",
                str(out_path.resolve())]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ff.run(cmd, cwd=tmp)
        thumb = out_path.with_suffix(".jpg")
        ff.run([ff.need("ffmpeg"), "-y", "-v", "error", "-ss", f"{min(1.0, final / 2):.2f}",
                "-i", str(out_path.resolve()), "-frames:v", "1", "-q:v", "3",
                str(thumb.resolve())], check=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {
        "file": str(out_path),
        "thumbnail": str(out_path.with_suffix(".jpg")),
        "title": clip.get("title") or "",
        "source_start": round(start, 2),
        "source_end": round(end, 2),
        "duration": round(final, 2),
        "trimmed_pause": round(duration - final, 2),
        "size": f"{width}x{height}",
        "captions": bool(clip_cues),
    }


def contact_sheet(path: str, start: float, end: float, out_path: Path, frames: int = 8,
                  cell: int = 320) -> None:
    """A tiled strip of frames from a window, so the clip can be judged at a glance."""
    duration = max(end - start, 0.5)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ff.run([ff.need("ffmpeg"), "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-i", path, "-an", "-sn", "-frames:v", "1", "-q:v", "4",
            "-vf", f"fps={frames / duration:.5f},scale={cell}:-2,tile={(frames + 1) // 2}x2",
            str(out_path)])
