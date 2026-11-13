"""Analysis commands: inspect, frames, silences, scenes, loudness.

These commands only read media. They let an AI "watch" a video by producing contact sheets,
exact frames and machine-readable facts about silence, cuts and loudness.
"""
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .ff import VSError, probe

try:  # find_font lives in CORE; the contact sheet degrades to unstamped frames without it
    from .ff import find_font
except ImportError:  # pragma: no cover
    find_font = None


# --------------------------------------------------------------------------- helpers

def _need_file(path):
    p = Path(path)
    if not p.is_file():
        raise VSError("input file not found: %s" % path)
    return p


def _binary(name):
    exe = shutil.which(name)
    if not exe:
        raise VSError("%s was not found on PATH. Install ffmpeg (https://ffmpeg.org/download.html; "
                      "apt install ffmpeg / brew install ffmpeg / winget install ffmpeg)." % name)
    return exe


def _ffmpeg_cmd(*args):
    return [_binary("ffmpeg"), "-hide_banner", "-nostdin", *[str(a) for a in args]]


def _capture(cmd):
    """Run an analysis command and return its stderr (ffmpeg prints filter results there)."""
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = "\n".join(r.stderr.strip().splitlines()[-6:])
        raise VSError("ffmpeg failed (exit %d): %s" % (r.returncode, tail))
    return r.stderr


def _run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = "\n".join(r.stderr.strip().splitlines()[-6:])
        raise VSError("ffmpeg failed (exit %d): %s" % (r.returncode, tail))


def _filter_path(p):
    """Escape a file path for use as a value inside an ffmpeg filter argument."""
    s = str(p).replace("\\", "/")
    return s.replace(":", "\\:").replace("'", "\\'").replace(",", "\\,").replace("[", "\\[").replace("]", "\\]")


def _num(v, name):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise VSError("%s must be a number, got %r" % (name, v))


def _stem(path):
    return Path(path).stem or "video"


def _dry(args):
    return bool(getattr(args, "dry_run", False))


def _log(args, msg):
    import sys
    if not getattr(args, "quiet", False):
        print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- core analysis

def find_silences(path, noise=-35.0, min_len=0.5, info=None):
    """Return ([{start,end,duration}], [{start,end}]) for the audio of `path`."""
    info = info or probe(str(path))
    dur = float(info.get("duration") or 0)
    if not info.get("has_audio"):
        return [], ([{"start": 0.0, "end": round(dur, 3)}] if dur > 0 else [])
    cmd = _ffmpeg_cmd("-i", path, "-vn", "-af", "silencedetect=noise=%sdB:d=%s" % (noise, min_len), "-f", "null", "-")
    err = _capture(cmd)
    silences, start = [], None
    for line in err.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*(-?[\d.]+)", line)
        if m and start is not None:
            end = min(float(m.group(1)), dur) if dur else float(m.group(1))
            silences.append({"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)})
            start = None
    if start is not None:  # silence runs to the end of the file
        silences.append({"start": round(start, 3), "end": round(dur, 3), "duration": round(dur - start, 3)})
    speech, cursor = [], 0.0
    for s in silences:
        if s["start"] - cursor > 0.01:
            speech.append({"start": round(cursor, 3), "end": s["start"]})
        cursor = s["end"]
    if dur - cursor > 0.01:
        speech.append({"start": round(cursor, 3), "end": round(dur, 3)})
    return silences, speech


def find_scenes(path, threshold=0.3, info=None):
    """Return the timestamps (seconds) where the picture changes by more than `threshold`."""
    info = info or probe(str(path))
    if not info.get("has_video"):
        raise VSError("%s has no video stream, so there are no scenes to detect" % path)
    # ffmpeg's scene score only looks at one plane, and a hard cut between two colours of equal
    # brightness (red -> green) scores ~0 on luma. Score the R, G and B planes separately and merge.
    t = threshold
    graph = ("[0:v:0]scale=320:-2,format=gbrp,extractplanes=g+b+r[g][b][r];"
             "[g]select='gt(scene,%s)',showinfo[og];[b]select='gt(scene,%s)',showinfo[ob];"
             "[r]select='gt(scene,%s)',showinfo[or]" % (t, t, t))
    err = _capture(_ffmpeg_cmd("-i", path, "-an", "-filter_complex", graph, "-map", "[og]", "-map", "[ob]",
                               "-map", "[or]", "-f", "null", "-"))
    found = []
    for line in err.splitlines():
        if "showinfo" in line and "pts_time:" in line:
            m = re.search(r"pts_time:\s*(-?[\d.]+)", line)
            if m:
                found.append(float(m.group(1)))
    times = []
    for v in sorted(found):
        if v > 0 and (not times or v - times[-1] > 0.05):
            times.append(round(v, 3))
    return times


def measure_loudness(path, info=None):
    """EBU R128 summary: integrated LUFS, true peak dBTP, loudness range LU (None without audio)."""
    info = info or probe(str(path))
    if not info.get("has_audio"):
        return {"integrated": None, "true_peak": None, "lra": None}
    err = _capture(_ffmpeg_cmd("-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"))
    summary = err.split("Summary:")[-1]

    def grab(pattern):
        m = re.search(pattern, summary)
        return float(m.group(1)) if m else None

    return {"integrated": grab(r"\bI:\s*(-?[\d.]+|-?inf)\s*LUFS"),
            "true_peak": grab(r"Peak:\s*(-?[\d.]+|-?inf)\s*dBFS"),
            "lra": grab(r"\bLRA:\s*(-?[\d.]+)\s*LU")}


def extract_frame(src, t, out, width=None, stamp=None, font=None, textfile=None):
    """Write one JPG of `src` at time `t`. `stamp` (text) is burned in when a font is available."""
    filters = []
    if width:
        filters.append("scale=%d:-2" % width)
    if stamp and textfile is not None:
        Path(textfile).write_text(stamp, encoding="utf-8")
        size = max(12, int((width or 640) / 22))
        dt = "drawtext=textfile='%s':x=%d:y=h-th-%d:fontsize=%d:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=%d" % (
            _filter_path(textfile), size // 2, size // 2, size, max(3, size // 4))
        if font:
            dt += ":fontfile='%s'" % _filter_path(font)
        filters.append(dt)
    cmd = _ffmpeg_cmd("-y", "-v", "error", "-ss", "%.3f" % t, "-i", src, "-frames:v", "1")
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += ["-q:v", "2", str(out)]
    return cmd


def _clock(t):
    m, s = divmod(t, 60)
    return "%02d:%04.1f" % (int(m), s)


def _safe_time(t, dur):
    """Keep a requested time inside the media so the last frame can still be decoded."""
    if dur and t > dur - 0.05:
        return max(0.0, dur - 0.05)
    return max(0.0, t)


# --------------------------------------------------------------------------- commands

def cmd_silences(args):
    src = _need_file(args.file)
    info = probe(str(src))
    sil, speech = find_silences(src, _num(args.noise, "--noise"), _num(args.min, "--min"), info)
    return {"ok": True, "silences": sil, "speech": speech}


def cmd_scenes(args):
    src = _need_file(args.file)
    return {"ok": True, "scenes": find_scenes(src, _num(args.threshold, "--threshold"))}


def cmd_loudness(args):
    src = _need_file(args.file)
    res = measure_loudness(src)
    res["ok"] = True
    return res


def cmd_frames(args):
    src = _need_file(args.file)
    info = probe(str(src))
    if not info.get("has_video"):
        raise VSError("%s has no video stream" % args.file)
    try:
        times = [float(x) for x in str(args.at).split(",") if x.strip() != ""]
    except ValueError:
        raise VSError("--at must be comma separated seconds like 1.5,3,10 (got %r)" % args.at)
    if not times:
        raise VSError("--at needs at least one time, e.g. --at 1.5,3,10")
    width = int(args.width)
    out_dir = Path(args.out) if args.out else Path.cwd() / ("%s_frames" % _stem(src))
    dur = float(info.get("duration") or 0)
    plan = []
    for i, t in enumerate(times, 1):
        if t < 0 or (dur and t > dur + 0.001):
            raise VSError("time %s is outside the video (0 to %.3f s)" % (t, dur))
        plan.append((out_dir / ("frame_%03d_%.3fs.jpg" % (i, t)), _safe_time(t, dur)))
    cmds = [extract_frame(src, t, o, width=width) for o, t in plan]
    if _dry(args):
        return {"ok": True, "dry_run": True, "frames": [str(o) for o, _ in plan], "commands": cmds}
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in cmds:
        _run(c)
    return {"ok": True, "frames": [str(o) for o, _ in plan]}


def cmd_inspect(args):
    src = _need_file(args.file)
    info = probe(str(src))
    if not info.get("has_video"):
        raise VSError("%s has no video stream, so there is nothing to put on a contact sheet" % args.file)
    n = int(args.frames)
    if n < 1:
        raise VSError("--frames must be at least 1")
    dur = float(info.get("duration") or 0)
    if dur <= 0:
        raise VSError("%s has no usable duration" % args.file)
    out_dir = Path(args.out) if args.out else Path.cwd() / ("%s_inspect" % _stem(src))
    times = [round(_safe_time(dur * (i + 0.5) / n, dur), 3) for i in range(n)]
    cols = min(n, 4) if n <= 12 else 5
    rows = int(math.ceil(n / float(cols)))
    cell_w = (1600 // cols) // 2 * 2
    sheet = out_dir / "contact.jpg"

    font = None
    if find_font is not None:
        try:
            font = find_font()
        except Exception:
            font = None

    tmp = Path(tempfile.mkdtemp(prefix="videoswami_inspect_"))
    try:
        frame_cmds = [extract_frame(src, t, tmp / ("f_%03d.jpg" % (i + 1)), width=cell_w, stamp=_clock(t),
                                    font=font, textfile=tmp / ("t_%03d.txt" % (i + 1)))
                      for i, t in enumerate(times)]
        tile_cmd = _ffmpeg_cmd("-y", "-v", "error", "-framerate", "1", "-i", tmp / "f_%03d.jpg", "-frames:v", "1",
                               "-vf", "tile=%dx%d:padding=2:color=black" % (cols, rows), "-q:v", "3", sheet)
        if _dry(args):
            return {"ok": True, "dry_run": True, "contact_sheet": str(sheet), "frame_times": times,
                    "commands": frame_cmds + [tile_cmd]}
        out_dir.mkdir(parents=True, exist_ok=True)
        _log(args, "extracting %d frames" % n)
        for c in frame_cmds:
            try:
                _run(c)
            except VSError:
                if "drawtext" not in " ".join(map(str, c)):
                    raise
                # no usable font/drawtext: fall back to an unstamped frame
                plain = [x for x in c]
                i = plain.index("-vf")
                plain[i + 1] = ",".join(f for f in plain[i + 1].split(",") if f.startswith("scale="))
                _run(plain)
        _run(tile_cmd)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)

    _log(args, "analysing silences, scenes and loudness")
    silences, _speech = find_silences(src, -35.0, 0.5, info)
    return {"ok": True, "probe": info, "contact_sheet": str(sheet), "frame_times": times,
            "scenes": find_scenes(src, 0.3, info),
            "silences": [{"start": s["start"], "end": s["end"]} for s in silences],
            "loudness": measure_loudness(src, info)}


# --------------------------------------------------------------------------- registration

def _flags(p):
    """Global flags shared by every command (added only when __main__ has not already added them)."""
    have = set(p._option_string_actions)
    if "--dry-run" not in have:
        p.add_argument("--dry-run", action="store_true", help="print the ffmpeg commands instead of running them")
    if "-y" not in have and "--overwrite" not in have:
        p.add_argument("-y", "--overwrite", action="store_true", help="replace existing output")
    if "--quiet" not in have:
        p.add_argument("--quiet", action="store_true", help="no progress on stderr")


def register(subparsers, add_global_flags=True):
    def add(name, fn, help_text):
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("file", metavar="FILE")
        p.set_defaults(func=fn)
        if add_global_flags:
            _flags(p)
        return p

    p = add("inspect", cmd_inspect, "contact sheet + scenes, silences and loudness: lets an AI watch a video")
    p.add_argument("--out", help="directory for contact.jpg (default ./<name>_inspect)")
    p.add_argument("--frames", type=int, default=12, help="frames on the contact sheet (default 12)")

    p = add("frames", cmd_frames, "write exact frames as JPG")
    p.add_argument("--at", required=True, help="comma separated times in seconds, e.g. 1.5,3,10")
    p.add_argument("--out", help="output directory (default ./<name>_frames)")
    p.add_argument("--width", type=int, default=960, help="frame width in px (default 960)")

    p = add("silences", cmd_silences, "list silent and speech ranges")
    p.add_argument("--noise", type=float, default=-35.0, help="silence threshold in dB (default -35)")
    p.add_argument("--min", type=float, default=0.5, help="shortest silence in seconds (default 0.5)")

    p = add("scenes", cmd_scenes, "list scene cut times")
    p.add_argument("--threshold", type=float, default=0.3, help="scene change sensitivity 0..1 (default 0.3)")

    add("loudness", cmd_loudness, "EBU R128 integrated loudness, true peak and range")
