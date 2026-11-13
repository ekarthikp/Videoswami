"""Smart commands: reframe, highlights, beats, beat-sync, enhance.

Analysis reads small raw frames / PCM from ffmpeg pipes and computes in pure Python;
numpy and cv2 are used only when importable. Set VIDEOSWAMI_NO_NUMPY=1 to force the pure-Python paths.
"""
import array
import math
import os
import subprocess
import sys
from operator import mul
from pathlib import Path

from . import ff
from .ff import VSError, probe, run_ffmpeg

FRAME_W = 96          # analysis frame width in pixels
FRAME_FPS = 5         # analysis frame rate
PCM_RATE = 8000       # analysis audio sample rate
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")


# --------------------------------------------------------------------------- optional libraries

def _np():
    if os.environ.get("VIDEOSWAMI_NO_NUMPY"):
        return None
    try:
        import numpy
        return numpy
    except ImportError:
        return None


def _cv2():
    if _np() is None:
        return None
    try:
        import cv2
        return cv2
    except ImportError:
        return None


# --------------------------------------------------------------------------- shared helpers

def _dry(a):
    return bool(getattr(a, "dry_run", False))


def _quiet(a):
    return bool(getattr(a, "quiet", False))


def _overwrite(a):
    return bool(getattr(a, "overwrite", False))


def _abs(p):
    return str(Path(p).expanduser().resolve())


def _need_file(path, what="input file"):
    p = Path(path).expanduser()
    if not p.is_file():
        raise VSError("%s not found: %s" % (what, path))
    return p


def _out_path(a, inputs=()):
    if not getattr(a, "output", None):
        raise VSError("-o OUT is required: this command writes a new file and never overwrites its input")
    out = Path(a.output).expanduser()
    if out.suffix.lower() not in VIDEO_EXTS:
        raise VSError("output: unsupported file type %r; use one of %s" % (out.suffix, ", ".join(VIDEO_EXTS)))
    for i in inputs:
        if out.resolve() == Path(i).expanduser().resolve():
            raise VSError("output %s is the same file as the input; choose a different -o" % out)
    if out.is_dir():
        raise VSError("output is a directory: %s" % out)
    if out.exists() and not _overwrite(a) and not _dry(a):
        raise VSError("output already exists: %s (use -y / --overwrite to replace it)" % out)
    return out


def _even(n):
    return max(2, int(round(n / 2.0)) * 2)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _need_video(info, path):
    if not info.get("has_video"):
        raise VSError("%s has no video stream" % path)
    if not info.get("width") or not info.get("height"):
        raise VSError("cannot read the picture size of %s" % path)


def _codec_args(out):
    ext = Path(out).suffix.lower()
    if ext == ".webm":
        return ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-row-mt", "1", "-pix_fmt", "yuv420p", "-c:a", "libopus", "-b:a", "128k"]
    args = ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
    if ext in (".mp4", ".mov"):
        args += ["-movflags", "+faststart"]
    return args


def _encode(a, out, args, duration, extra):
    """Run ffmpeg writing to a hidden partial file that is moved into place; honours --dry-run."""
    out = Path(out)
    result = {"ok": True, "output": str(out)}
    if _dry(a):
        result["dry_run"] = True
        result["commands"] = [ff.cmdline(["ffmpeg", "-hide_banner", "-y"] + [str(x) for x in args] + [str(out)])]
        result.update(extra)
        return result
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.parent / (".%s.partial%s" % (out.name, out.suffix))
    try:
        run_ffmpeg(["-y"] + [str(x) for x in args] + [str(partial)], duration=duration, progress=not _quiet(a))
        os.replace(str(partial), str(out))
    finally:
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass
    result["size_bytes"] = out.stat().st_size
    try:
        result["duration"] = probe(str(out)).get("duration")
    except VSError:
        pass
    result.update(extra)
    return result


def _pipe(args):
    ff.require_tools()
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error"] + [str(x) for x in args]
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        tail = "\n".join(r.stderr.decode("utf-8", "replace").strip().splitlines()[-6:])
        raise VSError("ffmpeg failed (exit %d): %s" % (r.returncode, tail))
    return r.stdout


def _read_frames(path, width, height, fps=FRAME_FPS, pix="gray", vf_extra=""):
    """Raw frames from ffmpeg as a list of bytes objects (one per sampled frame)."""
    vf = "fps=%s,scale=%d:%d%s" % (fps, width, height, vf_extra)
    data = _pipe(["-i", _abs(path), "-an", "-vf", vf, "-pix_fmt", pix, "-f", "rawvideo", "-"])
    size = width * height * (3 if pix == "rgb24" else 1)
    return [data[i:i + size] for i in range(0, len(data) - size + 1, size)]


def _read_pcm(path):
    """Mono 8 kHz samples: a numpy int16 array, or array('h') without numpy."""
    data = _pipe(["-i", _abs(path), "-vn", "-ac", "1", "-ar", PCM_RATE, "-f", "s16le", "-"])
    np = _np()
    if np is not None:
        return np.frombuffer(data[:len(data) // 2 * 2], dtype="<i2")
    arr = array.array("h")
    arr.frombytes(data[:len(data) // 2 * 2])
    if sys.byteorder == "big":
        arr.byteswap()
    return arr


def _analysis_size(info):
    w = FRAME_W
    h = max(2, int(round(w * info["height"] / float(info["width"]))))
    return w, h


# --------------------------------------------------------------------------- frame analysis

MOTION_PIXEL = 16     # a pixel counts as moving when it changes by at least this much


def _frame_diffs(frames, w, h):
    """For each consecutive frame pair: (mean_abs_diff 0..1, moving_pixels, cx, cy) with cx, cy as fractions."""
    out = []
    np = _np()
    if np is not None:
        arr = [np.frombuffer(f, dtype=np.uint8).reshape(h, w).astype(np.int16) for f in frames]
        xs = (np.arange(w) + 0.5) / w
        ys = (np.arange(h) + 0.5) / h
        for p, q in zip(arr, arr[1:]):
            d = np.abs(q - p)
            m = float(d.mean()) / 255.0
            dm = np.where(d >= MOTION_PIXEL, d, 0).astype(np.float64)
            tot = float(dm.sum())
            n = int((d >= MOTION_PIXEL).sum())
            if tot > 0:
                out.append((m, n, float((dm.sum(axis=0) * xs).sum() / tot), float((dm.sum(axis=1) * ys).sum() / tot)))
            else:
                out.append((m, 0, 0.5, 0.5))
        return out
    for p, q in zip(frames, frames[1:]):
        tot_all = 0
        tot = sx = sy = 0
        n = 0
        for y in range(h):
            base = y * w
            rowtot = rowx = 0
            for x, (u, v) in enumerate(zip(p[base:base + w], q[base:base + w])):
                d = u - v
                if d < 0:
                    d = -d
                tot_all += d
                if d >= MOTION_PIXEL:
                    n += 1
                    rowtot += d
                    rowx += d * x
            tot += rowtot
            sx += rowx
            sy += rowtot * y
        m = tot_all / (255.0 * w * h)
        if tot > 0:
            out.append((m, n, (sx / tot + 0.5) / w, (sy / tot + 0.5) / h))
        else:
            out.append((m, 0, 0.5, 0.5))
    return out


def _saliency(frame, w, h):
    """Centroid of how much each pixel stands out above the darkest level; (0.5, 0.5) for a flat frame."""
    np = _np()
    if np is not None:
        a = np.frombuffer(frame, dtype=np.uint8).reshape(h, w).astype(np.float64)
        a = a - a.min()
        tot = float(a.sum())
        if tot <= 0:
            return 0.5, 0.5
        return (float((a.sum(axis=0) * ((np.arange(w) + 0.5) / w)).sum() / tot),
                float((a.sum(axis=1) * ((np.arange(h) + 0.5) / h)).sum() / tot))
    lo = min(frame)
    tot = sx = sy = 0
    for y in range(h):
        row = frame[y * w:(y + 1) * w]
        rt = rx = 0
        for x, v in enumerate(row):
            v -= lo
            rt += v
            rx += v * x
        tot += rt
        sx += rx
        sy += rt * y
    if tot <= 0:
        return 0.5, 0.5
    return (sx / tot + 0.5) / w, (sy / tot + 0.5) / h


def _smooth_series(values, radius):
    """Box average with edge truncation (only the samples that exist are averaged)."""
    if radius <= 0 or len(values) < 2:
        return list(values)
    n = len(values)
    pre = [0.0]
    for v in values:
        pre.append(pre[-1] + v)
    res = []
    for i in range(n):
        lo, hi = max(0, i - radius), min(n, i + radius + 1)
        res.append((pre[hi] - pre[lo]) / (hi - lo))
    return res


def _fill_gaps(points):
    """Replace None entries by the previous valid one (the first valid one for a leading gap)."""
    first = next((p for p in points if p is not None), None)
    last = first
    out = []
    for p in points:
        if p is not None:
            last = p
        out.append(last if last is not None else first)
    return out


def _face_track(path, info, dur):
    """Per-sample largest-face centre (or None), sampled at 2 fps; None list entries where no face is found."""
    cv2 = _cv2()
    if cv2 is None:
        return None
    np = _np()
    w = 320
    h = max(2, int(round(w * info["height"] / float(info["width"]))))
    frames = _read_frames(path, w, h, fps=2)
    cascade = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    if cascade.empty():
        return None
    pts = []
    for f in frames:
        img = np.frombuffer(f, dtype=np.uint8).reshape(h, w)
        faces = cascade.detectMultiScale(img, scaleFactor=1.1, minNeighbors=5, minSize=(max(12, w // 16), max(12, w // 16)))
        if len(faces):
            x, y, fw, fh = max(faces, key=lambda r: r[2] * r[3])
            pts.append(((x + fw / 2.0) / w, (y + fh / 2.0) / h))
        else:
            pts.append(None)
    return pts, 2.0


# --------------------------------------------------------------------------- reframe

def _parse_aspect(text):
    try:
        if ":" in str(text):
            a, b = str(text).split(":", 1)
            r = float(a) / float(b)
        else:
            r = float(text)
    except (ValueError, ZeroDivisionError):
        raise VSError("--aspect must look like 9:16, 1:1 or 4:5, got %r" % (text,))
    if not 0.1 <= r <= 10:
        raise VSError("--aspect %s is out of range" % text)
    return r


def _build_path(a, src, info, track, smooth):
    """Return (track_name, [(t, cx, cy)]) at the analysis rate, smoothed and speed limited."""
    dur = float(info["duration"] or 0) or 1.0
    w, h = _analysis_size(info)
    samples, rate, name = None, float(FRAME_FPS), "motion"
    if track in ("auto", "face"):
        face = _face_track(src, info, dur)
        if face is None:
            if track == "face":
                raise VSError("--track face needs the cv2 module (pip install opencv-python numpy); use --track motion or auto instead")
        else:
            pts, frate = face
            found = sum(1 for p in pts if p is not None)
            if pts and (found * 2 > len(pts) or (track == "face" and found > 0)):
                samples, rate, name = _fill_gaps(pts), frate, "face"
            elif track == "face":
                raise VSError("no faces found in %s; use --track motion or auto instead" % src)
    if samples is None:
        frames = _read_frames(src, w, h)
        if len(frames) < 2:
            samples = [_saliency(f, w, h) for f in frames] or [(0.5, 0.5)]
        else:
            diffs = _frame_diffs(frames, w, h)
            need = max(2, int(0.0015 * w * h))
            pts = [(d[2], d[3]) if d[1] >= need else None for d in diffs]
            valid = sum(1 for p in pts if p is not None)
            if valid * 10 < len(pts):  # nothing moves: follow the brightest region instead
                samples = [_saliency(f, w, h) for f in frames[1:]]
            else:
                samples = _fill_gaps(pts)
            samples = [samples[0]] + samples  # the first frame has no predecessor
    n = len(samples)
    radius = max(0, int(round(smooth * rate / 2.0)))
    xs = _smooth_series(_smooth_series([s[0] for s in samples], radius), radius)
    ys = _smooth_series(_smooth_series([s[1] for s in samples], radius), radius)
    # limited speed: the crop centre may travel at most half the frame per second
    step = 0.5 / rate
    for series in (xs, ys):
        for i in range(1, n):
            series[i] = series[i - 1] + _clamp(series[i] - series[i - 1], -step, step)
    return name, [(i / rate, xs[i], ys[i]) for i in range(n)], dur


def _decimate(path, dur):
    """Keep about one point per half second (at most 120) and always the last, ending exactly at `dur`."""
    spacing = max(0.5, dur / 120.0)
    keep, last_t = [], -1e9
    for p in path:
        if p[0] - last_t >= spacing - 1e-9 or not keep:
            keep.append(p)
            last_t = p[0]
    if keep[-1][0] < dur - 1e-6:
        keep.append((dur, path[-1][1], path[-1][2]))
    else:
        keep[-1] = (dur, keep[-1][1], keep[-1][2])
    if len(keep) < 2:
        keep.append((dur, keep[0][1], keep[0][2]))
    return keep


def _lerp_expr(points):
    """Flat piecewise-linear expression of t through (t, value) points (holds the last value afterwards)."""
    expr = "%.3f" % points[0][1]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        dt = t1 - t0
        if dt <= 1e-9 or abs(v1 - v0) < 1e-6:
            continue
        expr += "%+.4f*clip(t-%.3f,0,%.3f)" % ((v1 - v0) / dt, t0, dt)
    return expr


def cmd_reframe(a):
    src = _need_file(a.file)
    out = _out_path(a, [src])
    info = probe(str(src))
    _need_video(info, src)
    track = a.track
    if track not in ("auto", "face", "motion", "center"):
        raise VSError("--track must be auto, face, motion or center")
    if a.smooth is None or a.smooth < 0:
        raise VSError("--smooth must be zero or more seconds")
    ratio = _parse_aspect(a.aspect)
    W, H = int(info["width"]), int(info["height"])
    dur = float(info["duration"] or info["video_duration"] or 0)
    if W / float(H) > ratio:
        cw, ch = _even(H * ratio), _even(H)
    else:
        cw, ch = _even(W), _even(W / ratio)
    cw, ch = min(cw, W - W % 2), min(ch, H - H % 2)
    if track == "center":
        name, raw = "center", [(0.0, 0.5, 0.5), (dur, 0.5, 0.5)]
    else:
        name, full, dur2 = _build_path(a, src, info, track, a.smooth)
        raw = _decimate(full, dur2 if dur <= 0 else dur)
    path = []
    for t, cx, cy in raw:  # keep the crop window inside the picture
        cx = _clamp(cx, cw / 2.0 / W, 1 - cw / 2.0 / W)
        cy = _clamp(cy, ch / 2.0 / H, 1 - ch / 2.0 / H)
        path.append({"t": round(t, 3), "x": round(cx, 4), "y": round(cy, 4)})
    xe = _lerp_expr([(p["t"], p["x"] * W - cw / 2.0) for p in path])
    ye = _lerp_expr([(p["t"], p["y"] * H - ch / 2.0) for p in path])
    vf = "crop=%d:%d:x='%s':y='%s':exact=1" % (cw, ch, xe, ye)
    out_w, out_h = cw, ch
    if a.height:
        out_h = _even(a.height)
        out_w = _even(out_h * ratio)
        vf += ",scale=%d:%d:flags=lanczos" % (out_w, out_h)
    vf += ",setsar=1"
    args = ["-i", _abs(src), "-vf", vf, "-map", "0:v:0"]
    if info["has_audio"]:
        args += ["-map", "0:a:0"]
    args += _codec_args(out)
    return _encode(a, out, args, dur, {"track": name, "path": path, "aspect": a.aspect, "width": out_w, "height": out_h})


# --------------------------------------------------------------------------- highlights

BIN = 0.5  # seconds per scoring bin


def _bin_rms(pcm, nbins):
    per = int(PCM_RATE * BIN)
    np = _np()
    res = []
    if np is not None:
        x = pcm.astype(np.float64)
        for i in range(nbins):
            seg = x[i * per:(i + 1) * per]
            res.append(float(np.sqrt((seg * seg).mean())) if len(seg) else 0.0)
        return res
    for i in range(nbins):
        seg = pcm[i * per:(i + 1) * per]
        res.append(math.sqrt(sum(map(mul, seg, seg)) / len(seg)) if len(seg) else 0.0)
    return res


def _moment_scores(src, info):
    """Per half-second score = loudness + motion + scene-change bonus (each normalised to 0..1)."""
    dur = float(info["duration"])
    nbins = max(1, int(math.ceil(dur / BIN)))
    rms = _bin_rms(_read_pcm(src), nbins) if info["has_audio"] else [0.0] * nbins
    w, h = _analysis_size(info)
    frames = _read_frames(src, w, h)
    diffs = _frame_diffs(frames, w, h) if len(frames) > 1 else []
    motion, scene = [0.0] * nbins, [0.0] * nbins
    counts = [0] * nbins
    for i, d in enumerate(diffs):
        b = min(nbins - 1, int((i + 1) / float(FRAME_FPS) / BIN))
        motion[b] += d[0]
        counts[b] += 1
        if d[0] > 0.2:
            scene[b] = 1.0
    motion = [m / c if c else 0.0 for m, c in zip(motion, counts)]
    rms_hi, mot_hi = max(rms) or 1.0, max(motion) or 1.0
    if rms_hi < 30:  # digital silence: nothing to normalise against
        rms_hi = 1e9
    if mot_hi < 0.002:
        mot_hi = 1e9
    return [r / rms_hi + m / mot_hi + 0.5 * s for r, m, s in zip(rms, motion, scene)], dur


def _pick_windows(scores, dur, target, min_clip, max_clip):
    """Greedy: best-scoring non-overlapping windows until `target` seconds are filled."""
    n = len(scores)
    pre = [0.0]
    for s in scores:
        pre.append(pre[-1] + s)
    taken = [False] * n
    chosen, total = [], 0.0
    guard = 0
    while target - total > 0.25 and guard < 1000:
        guard += 1
        rem = target - total
        length = min(max_clip, max(rem, min_clip))
        if total + length > target * 1.15:
            length = rem
        bins = max(1, int(round(length / BIN)))
        best = None
        for i in range(0, n - bins + 1):
            if any(taken[i:i + bins]):
                continue
            mean = (pre[i + bins] - pre[i]) / bins
            if best is None or mean > best[0] + 1e-12:
                best = (mean, i)
        if best is None:  # no free window of that size: try a smaller one
            if bins <= 1:
                break
            bins = max(1, bins // 2)
            for i in range(0, n - bins + 1):
                if any(taken[i:i + bins]):
                    continue
                mean = (pre[i + bins] - pre[i]) / bins
                if best is None or mean > best[0] + 1e-12:
                    best = (mean, i)
            if best is None:
                break
        mean, i = best
        for j in range(i, i + bins):
            taken[j] = True
        start, end = i * BIN, min(dur, (i + bins) * BIN)
        chosen.append([start, end, mean])
        total += end - start
    chosen.sort()
    merged = []
    for s in chosen:  # touching windows become one clip
        if merged and abs(merged[-1][1] - s[0]) < 1e-9:
            merged[-1][1] = s[1]
            merged[-1][2] = (merged[-1][2] + s[2]) / 2.0
        else:
            merged.append(s)
    return merged


def _segments_graph(segments, has_audio, fade, extra_v="", extra_a=""):
    """filter_complex that trims `segments` [(start, end)] from input 0 and joins them with short fades."""
    k = len(segments)
    parts = []
    if k > 1:
        parts.append("[0:v]split=%d%s" % (k, "".join("[sv%d]" % i for i in range(k))))
        if has_audio:
            parts.append("[0:a]asplit=%d%s" % (k, "".join("[sa%d]" % i for i in range(k))))
    vlab = lambda i: "[sv%d]" % i if k > 1 else "[0:v]"
    alab = lambda i: "[sa%d]" % i if k > 1 else "[0:a]"
    for i, (s, e) in enumerate(segments):
        length = e - s
        f = min(fade, length / 3.0)
        vf = "trim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS" % (s, e)
        af = "atrim=start=%.3f:end=%.3f,asetpts=PTS-STARTPTS" % (s, e)
        if f > 0.01:
            if i > 0:
                vf += ",fade=t=in:st=0:d=%.3f" % f
                af += ",afade=t=in:st=0:d=%.3f" % f
            if i < k - 1:
                vf += ",fade=t=out:st=%.3f:d=%.3f" % (length - f, f)
                af += ",afade=t=out:st=%.3f:d=%.3f" % (length - f, f)
        parts.append("%s%s[tv%d]" % (vlab(i), vf, i))
        if has_audio:
            parts.append("%s%s[ta%d]" % (alab(i), af, i))
    if has_audio:
        parts.append("%sconcat=n=%d:v=1:a=1[v][a]" % ("".join("[tv%d][ta%d]" % (i, i) for i in range(k)), k))
    else:
        parts.append("%sconcat=n=%d:v=1:a=0[v]" % ("".join("[tv%d]" % i for i in range(k)), k))
    return ";".join(parts)


def cmd_highlights(a):
    src = _need_file(a.file)
    out = _out_path(a, [src])
    info = probe(str(src))
    _need_video(info, src)
    if a.duration <= 0 or a.min_clip <= 0 or a.max_clip < a.min_clip or a.transition < 0:
        raise VSError("--duration and --min-clip must be positive, --max-clip at least --min-clip, --transition zero or more")
    dur = float(info["duration"])
    if dur <= a.duration + 0.25:
        segs = [{"start": 0.0, "end": round(dur, 3), "score": 1.0}]
    else:
        scores, dur = _moment_scores(src, info)
        wins = _pick_windows(scores, dur, a.duration, a.min_clip, a.max_clip)
        if not wins:
            raise VSError("could not find any highlight in %s" % src)
        segs = [{"start": round(s, 3), "end": round(e, 3), "score": round(m, 4)} for s, e, m in wins]
    graph = _segments_graph([(s["start"], s["end"]) for s in segs], info["has_audio"], a.transition)
    args = ["-i", _abs(src), "-filter_complex", graph, "-map", "[v]"]
    if info["has_audio"]:
        args += ["-map", "[a]"]
    args += _codec_args(out)
    total = sum(s["end"] - s["start"] for s in segs)
    return _encode(a, out, args, total, {"segments": segs})


# --------------------------------------------------------------------------- beats

HOP = 64                      # samples per onset frame
ENV_RATE = PCM_RATE / float(HOP)   # onset frames per second (125)


def _onsets(pcm):
    """Onset strength per HOP block: rise of the block RMS (square-root compressed), as a list of floats."""
    np = _np()
    n = len(pcm) // HOP
    if n < 4:
        return []
    if np is not None:
        x = pcm[:n * HOP].astype(np.float64).reshape(n, HOP)
        env = np.sqrt(np.sqrt((x * x).mean(axis=1)))
        env = env / (env.max() or 1.0)
        on = np.maximum(0.0, np.diff(np.concatenate(([0.0], env))))
        return on.tolist()
    env = []
    for i in range(n):
        blk = pcm[i * HOP:(i + 1) * HOP]
        env.append(math.sqrt(math.sqrt(sum(map(mul, blk, blk)) / HOP)))
    top = max(env) or 1.0
    prev, on = 0.0, []
    for e in env:
        e /= top
        on.append(max(0.0, e - prev))
        prev = e
    return on


def _estimate_tempo(on):
    """(period_in_frames, bpm) from the autocorrelation of the (lightly smoothed) onset envelope."""
    kernel = [1, 2, 3, 2, 1]
    n = len(on)
    sm = [sum(kernel[j] * on[i + j - 2] for j in range(5) if 0 <= i + j - 2 < n) / 9.0 for i in range(n)]
    sm = sm[:int(300 * ENV_RATE)]
    lmin, lmax = int(ENV_RATE * 60 / 200.0), int(ENV_RATE * 60 / 55.0)
    if len(sm) < lmax * 3:
        lmax = max(lmin + 3, len(sm) // 3)
    np = _np()
    ac = {}
    for lag in range(lmin - 1, lmax + 2):
        if np is not None:
            v = np.asarray(sm)
            ac[lag] = float(np.dot(v[:-lag], v[lag:]))
        else:
            ac[lag] = sum(map(mul, sm, sm[lag:]))
    best, best_s = None, 0.0
    for lag in range(lmin, lmax + 1):
        bpm = 60.0 * ENV_RATE / lag
        score = ac[lag] * math.exp(-0.5 * (math.log(bpm / 120.0, 2) / 1.0) ** 2)
        if score > best_s:
            best, best_s = lag, score
    if best is None:
        return None
    a_, b_, c_ = ac[best - 1], ac[best], ac[best + 1]
    den = a_ - 2 * b_ + c_
    period = best + (0.5 * (a_ - c_) / den if den < 0 else 0.0)
    return period, 60.0 * ENV_RATE / period


def _track_beats(on, period, dur):
    """Beat times: best-fitting phase, then follow the onsets (snapping within +-12% of a beat)."""
    top = max(on) or 1.0
    thr = 0.2 * top
    sm = [(on[i - 1] if i else 0) * 0.5 + on[i] + (on[i + 1] if i + 1 < len(on) else 0) * 0.5 for i in range(len(on))]
    limit = min(len(on), int(60 * ENV_RATE))
    best_off, best_score = 0.0, -1.0
    off = 0.0
    while off < period:
        k, score = 0, 0.0
        while off + k * period < limit:
            score += sm[int(round(off + k * period))] if int(round(off + k * period)) < len(sm) else 0
            k += 1
        if score > best_score + 1e-9:
            best_off, best_score = off, score
        off += 1.0
    beats, pos = [], best_off
    total_frames = dur * ENV_RATE
    radius = max(1, int(period * 0.12))
    while pos < total_frames:
        c = int(round(pos))
        lo, hi = max(0, c - radius), min(len(on), c + radius + 1)
        peak = max(range(lo, hi), key=lambda i: on[i]) if hi > lo else None
        if peak is not None and on[peak] >= thr:
            pos = float(peak)
        beats.append(pos / ENV_RATE)
        pos += period
    return beats


def analyse_beats(path):
    """(bpm, beats, period_seconds) for the audio of `path`."""
    src = _need_file(path)
    info = probe(str(src))
    if not info["has_audio"]:
        raise VSError("%s has no audio stream to find beats in" % path)
    pcm = _read_pcm(src)
    dur = len(pcm) / float(PCM_RATE)
    on = _onsets(pcm)
    est = _estimate_tempo(on) if on else None
    if est is None:
        raise VSError("could not find a steady beat in %s (too short or no rhythm)" % path)
    period, bpm = est
    beats = _track_beats(on, period, dur)
    return bpm, beats, period / ENV_RATE, dur


def cmd_beats(a):
    bpm, beats, _, dur = analyse_beats(a.file)
    return {"ok": True, "bpm": round(bpm, 2), "beats": [round(b, 3) for b in beats], "duration": round(dur, 3)}


# --------------------------------------------------------------------------- beat-sync

SYNC_FPS = 30


def cmd_beat_sync(a):
    clips = [_need_file(c, "clip") for c in a.clips]
    music = _need_file(a.audio, "audio file")
    if not clips:
        raise VSError("give at least one clip")
    if a.every < 1:
        raise VSError("--every must be 1 or more")
    if a.fit not in ("cover", "contain"):
        raise VSError("--fit must be cover or contain")
    out = _out_path(a, clips + [music])
    infos = []
    for c in clips:
        i = probe(str(c))
        _need_video(i, c)
        infos.append(i)
    minfo = probe(str(music))
    if not minfo["has_audio"]:
        raise VSError("%s has no audio stream" % music)
    bpm, beats, period, music_len = analyse_beats(music)
    total = a.duration if a.duration else music_len
    if total <= 0:
        raise VSError("--duration must be positive")
    total = round(total * SYNC_FPS) / float(SYNC_FPS)
    t = beats[-1] + period if beats else 0.0
    while t < total:  # the grid continues if the video outlasts the detected beats
        beats.append(t)
        t += period
    cuts = []
    for i in range(a.every, len(beats), a.every):
        c = round(beats[i] * SYNC_FPS) / float(SYNC_FPS)
        if 0.2 < c < total - 0.2 and (not cuts or c - cuts[-1] > 0.1):
            cuts.append(c)
    bounds = [0.0] + cuts + [total]
    W, H = _even(infos[0]["width"]), _even(infos[0]["height"])
    pos = [0.0] * len(clips)
    pieces = []   # (clip index, start, end) in output order
    for si in range(len(bounds) - 1):
        ci = si % len(clips)
        need = bounds[si + 1] - bounds[si]
        cd = max(0.1, float(infos[ci]["video_duration"] or infos[ci]["duration"]))
        while need > 1e-6:
            s = pos[ci] % cd
            take = min(need, cd - s)
            pieces.append((ci, s, s + take))
            pos[ci] = s + take if s + take < cd - 1e-6 else 0.0
            need -= take
    uses = [sum(1 for p in pieces if p[0] == c) for c in range(len(clips))]
    parts, used = [], [0] * len(clips)
    for c, n in enumerate(uses):
        if n > 1:
            parts.append("[%d:v]split=%d%s" % (c, n, "".join("[c%d_%d]" % (c, j) for j in range(n))))
    if a.fit == "cover":
        fit = "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d" % (W, H, W, H)
    else:
        fit = "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black" % (W, H, W, H)
    for k, (c, s, e) in enumerate(pieces):
        lab = "[c%d_%d]" % (c, used[c]) if uses[c] > 1 else "[%d:v]" % c
        used[c] += 1
        parts.append("%strim=start=%.4f:end=%.4f,setpts=PTS-STARTPTS,fps=%d,%s,setsar=1,format=yuv420p[p%d]" % (lab, s, e, SYNC_FPS, fit, k))
    parts.append("%sconcat=n=%d:v=1:a=0[v]" % ("".join("[p%d]" % k for k in range(len(pieces))), len(pieces)))
    mi = len(clips)
    parts.append("[%d:a]atrim=0:%.4f,asetpts=PTS-STARTPTS,apad=whole_dur=%.4f[a]" % (mi, total, total))
    args = []
    for c in clips:
        args += ["-i", _abs(c)]
    args += ["-i", _abs(music), "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]", "-t", "%.4f" % total]
    args += _codec_args(out)
    return _encode(a, out, args, total, {"bpm": round(bpm, 2), "cuts": [round(c, 3) for c in cuts], "clips": len(clips)})


# --------------------------------------------------------------------------- enhance

def _picture_stats(src, info):
    """Luma p2/p98/mean (0..1) and mean R, G, B (0..1) over sampled frames."""
    dur = float(info["duration"] or 1)
    w, h = _analysis_size(info)
    frames = _read_frames(src, w, h, fps="%.4f" % min(2.0, 60.0 / max(dur, 1.0)), pix="rgb24")
    if not frames:
        raise VSError("could not sample any frames from %s" % src)
    np = _np()
    if np is not None:
        px = np.concatenate([np.frombuffer(f, dtype=np.uint8).reshape(-1, 3) for f in frames]).astype(np.float64)
        luma = px @ np.array([0.299, 0.587, 0.114])
        lo, hi = np.percentile(luma, [2, 98])
        mean = px.mean(axis=0)
        return lo / 255.0, hi / 255.0, float(luma.mean()) / 255.0, [float(m) / 255.0 for m in mean]
    hist = [0] * 256
    sums = [0, 0, 0]
    n = 0
    for f in frames:
        r, g, b = f[0::3], f[1::3], f[2::3]
        sums[0] += sum(r)
        sums[1] += sum(g)
        sums[2] += sum(b)
        for x, y, z in zip(r, g, b):
            hist[int(0.299 * x + 0.587 * y + 0.114 * z)] += 1
        n += len(r)

    def pct(p):
        goal, acc = p * n, 0
        for v, c in enumerate(hist):
            acc += c
            if acc >= goal:
                return v
        return 255
    luma_mean = sum(v * c for v, c in enumerate(hist)) / float(n)
    return pct(0.02) / 255.0, pct(0.98) / 255.0, luma_mean / 255.0, [s / float(n) / 255.0 for s in sums]


def _enhance_video(src, info):
    lo, hi, mean, rgb = _picture_stats(src, info)
    spread = max(hi - lo, 0.02)
    contrast = _clamp(0.94 / spread, 1.0, 2.2)
    brightness = _clamp(0.03 - 0.5 - (lo - 0.5) * contrast, -0.3, 0.3)
    after = _clamp((mean - 0.5) * contrast + 0.5 + brightness, 0.02, 0.98)
    gamma = _clamp(math.log(after) / math.log(0.45), 0.7, 1.6)
    avg = sum(rgb) / 3.0 or 1.0
    gains = [round(_clamp(1 + 0.5 * (avg / max(c, 0.01) - 1), 0.92, 1.08), 3) for c in rgb]
    saturation = 1.1
    vf = "colorchannelmixer=rr=%s:gg=%s:bb=%s,eq=contrast=%.3f:brightness=%.3f:gamma=%.3f:saturation=%.2f" % (
        gains[0], gains[1], gains[2], contrast, brightness, gamma, saturation)
    return vf, {"contrast": round(contrast, 3), "brightness": round(brightness, 3), "gamma": round(gamma, 3),
                "saturation": saturation, "white_balance_gains": gains,
                "luma_p2": round(lo, 3), "luma_p98": round(hi, 3), "luma_mean": round(mean, 3)}


AUDIO_CHAIN = ("highpass=f=80,afftdn=nr=12:nf=-40,acompressor=threshold=0.08:ratio=3:attack=20:release=250:makeup=2,"
               "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000")


def cmd_enhance(a):
    src = _need_file(a.file)
    out = _out_path(a, [src])
    info = probe(str(src))
    _need_video(info, src)
    do_video = not a.no_video
    do_audio = not a.no_audio and info["has_audio"]
    args = ["-i", _abs(src), "-map", "0:v:0"]
    if info["has_audio"]:
        args += ["-map", "0:a:0"]
    applied_v, applied_a = {}, {}
    codec = _codec_args(out)
    if do_video:
        vf, applied_v = _enhance_video(src, info)
        args += ["-vf", vf]
    else:
        codec = _swap_codec(codec, "-c:v", "copy", drop=["-crf", "-preset", "-pix_fmt", "-b:v", "-row-mt"])
    if do_audio:
        args += ["-af", AUDIO_CHAIN]
        applied_a = {"highpass_hz": 80, "denoise": "afftdn", "compressor": "gentle 3:1", "loudnorm_lufs": -14}
    elif info["has_audio"]:
        codec = _swap_codec(codec, "-c:a", "copy", drop=["-b:a"])
    args += codec
    return _encode(a, out, args, float(info["duration"] or 0), {"video": applied_v, "audio": applied_a})


def _swap_codec(args, flag, value, drop=()):
    """Replace the codec after `flag` with `value` and remove options that only apply to encoding."""
    res, i = [], 0
    while i < len(args):
        if args[i] == flag:
            res += [flag, value]
            i += 2
        elif args[i] in drop:
            i += 2
        else:
            res.append(args[i])
            i += 1
    return res


# --------------------------------------------------------------------------- registration

def register(subparsers):
    def add(name, fn, help_text, files=True):
        p = subparsers.add_parser(name, help=help_text)
        if files:
            p.add_argument("file", metavar="FILE")
        p.add_argument("-o", "--output", help="output file (required)")
        p.set_defaults(func=fn)
        return p

    p = add("reframe", cmd_reframe, "crop to another aspect ratio, following the subject")
    p.add_argument("--aspect", default="9:16", help="target aspect such as 9:16, 1:1, 4:5 (default 9:16)")
    p.add_argument("--track", default="auto", choices=["auto", "face", "motion", "center"], help="what to follow (default auto)")
    p.add_argument("--smooth", type=float, default=1.0, help="smoothing in seconds (default 1.0)")
    p.add_argument("--height", type=int, help="output height in pixels (default: the cropped size)")

    p = add("highlights", cmd_highlights, "cut the best moments into a short video")
    p.add_argument("--duration", type=float, default=30.0, help="target length in seconds (default 30)")
    p.add_argument("--min-clip", dest="min_clip", type=float, default=2.0, help="shortest clip (default 2)")
    p.add_argument("--max-clip", dest="max_clip", type=float, default=8.0, help="longest clip (default 8)")
    p.add_argument("--transition", type=float, default=0.25, help="fade length between clips (default 0.25)")

    p = subparsers.add_parser("beats", help="find the tempo and beat times of a song or video")
    p.add_argument("file", metavar="FILE")
    p.set_defaults(func=cmd_beats)

    p = subparsers.add_parser("beat-sync", help="cut clips together on the beats of a music track")
    p.add_argument("clips", nargs="+", metavar="CLIP")
    p.add_argument("--audio", required=True, help="music file")
    p.add_argument("--every", type=int, default=2, help="cut on every Nth beat (default 2)")
    p.add_argument("--duration", type=float, help="output length (default: the music length)")
    p.add_argument("--fit", default="cover", help="cover (default) or contain")
    p.add_argument("-o", "--output", help="output file (required)")
    p.set_defaults(func=cmd_beat_sync)

    p = add("enhance", cmd_enhance, "automatic picture and voice clean-up")
    p.add_argument("--no-video", action="store_true", help="leave the picture alone")
    p.add_argument("--no-audio", action="store_true", help="leave the sound alone")
