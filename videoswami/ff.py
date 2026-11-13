"""Thin, dependency-free helpers around the ffmpeg and ffprobe executables.

Everything that touches a subprocess lives here: running ffmpeg with progress
reporting, probing media files, escaping text for filter graphs and locating a
usable font on the current operating system.
"""
from __future__ import annotations

import collections
import functools
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path


class VSError(Exception):
    """A user-facing failure. The message is printed in the final JSON object."""


# --------------------------------------------------------------------------- logging

QUIET = False


def set_quiet(value):
    """Silence progress and informational logging on stderr (warnings stay)."""
    global QUIET
    QUIET = bool(value)


def log(message):
    """Informational message to stderr (stdout is reserved for the JSON result)."""
    if not QUIET:
        print(message, file=sys.stderr, flush=True)


def warn(message):
    """Warning to stderr; always shown."""
    print("warning: " + message, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- tools

def require_tools():
    """Fail early, with an install hint, when ffmpeg or ffprobe are not on PATH."""
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if not missing:
        return
    system = platform.system()
    if system == "Darwin":
        hint = "install with: brew install ffmpeg"
    elif system == "Windows":
        hint = "install with: winget install Gyan.FFmpeg (then restart the terminal)"
    else:
        hint = "install with your package manager, e.g. sudo apt install ffmpeg"
    raise VSError("%s not found on PATH; %s" % (" and ".join(missing), hint))


def cmdline(argv):
    """Render an argv list as a copy-pasteable command string for the current OS."""
    argv = [str(a) for a in argv]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    import shlex
    return " ".join(shlex.quote(a) for a in argv)


def _full_command(args, progress):
    args = [str(a) for a in args]
    if args and Path(args[0]).stem.lower() == "ffmpeg":
        args = args[1:]  # callers may or may not include the binary name
    head = ["ffmpeg", "-hide_banner", "-nostdin"]
    if progress:
        head += ["-progress", "pipe:1", "-nostats"]
    return head + args


def run_ffmpeg(args, duration=None, progress=True):
    """Run ffmpeg with `args` (the binary name is optional) and return its stderr text.

    `duration` (seconds of expected output) enables a percentage on stderr.
    stdout of this process is never touched. Raises VSError with the last ffmpeg
    error lines on a non-zero exit.
    """
    require_tools()
    cmd = _full_command(args, progress)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise VSError("could not start ffmpeg: %s" % exc)

    chunks = []
    tail = collections.deque(maxlen=60)

    def drain():
        for line in proc.stderr:
            chunks.append(line)
            if line.strip():
                tail.append(line.rstrip())

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    last_shown = -1
    try:
        for line in proc.stdout:
            if not progress or QUIET or not duration:
                continue
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    done = int(line.split("=", 1)[1]) / 1e6
                except ValueError:
                    continue
                pct = max(0, min(99, int(done / duration * 100)))
                step = pct if sys.stderr.isatty() else pct // 25 * 25
                if step != last_shown:
                    last_shown = step
                    end = "\r" if sys.stderr.isatty() else "\n"
                    print("  encoding %d%%" % pct, end=end, file=sys.stderr, flush=True)
        proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        raise
    reader.join(timeout=5)
    if progress and not QUIET and duration and last_shown >= 0 and sys.stderr.isatty():
        print("", file=sys.stderr)
    if proc.returncode != 0:
        lines = list(tail)[-15:]
        raise VSError("ffmpeg failed (exit %d):\n%s" % (proc.returncode, "\n".join(lines)))
    return "".join(chunks)


# --------------------------------------------------------------------------- probe

def _rate(text):
    try:
        num, _, den = str(text).partition("/")
        den = float(den) if den else 1.0
        return float(num) / den if den else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path):
    """Describe a media file. Width/height are already swapped for 90/270 rotation."""
    require_tools()
    p = Path(path)
    if not p.is_file():
        raise VSError("file not found: %s" % path)
    res = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        detail = (res.stderr.strip().splitlines() or ["unknown error"])[-1]
        raise VSError("cannot read %s as media: %s" % (path, detail))
    try:
        info = json.loads(res.stdout or "{}")
    except ValueError:
        raise VSError("ffprobe returned unreadable output for %s" % path)
    streams = info.get("streams", [])
    fmt = info.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    def num(value, cast=float):
        try:
            return cast(value)
        except (TypeError, ValueError):
            return None

    width = height = None
    fps = rotation = 0
    nb_frames = None
    video_duration = None
    if video:
        width, height = video.get("width"), video.get("height")
        fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
        rot = 0.0
        for side in video.get("side_data_list", []) or []:
            if "rotation" in side:
                rot = num(side["rotation"]) or 0.0
        if not rot:
            rot = num(video.get("tags", {}).get("rotate")) or 0.0
        rotation = int(round(rot)) % 360
        if rotation in (90, 270):
            width, height = height, width
        nb_frames = num(video.get("nb_frames"), int)
        video_duration = num(video.get("duration"))
        if not video_duration and nb_frames and fps:
            video_duration = nb_frames / fps
    duration = num(fmt.get("duration"))
    if duration is None:
        duration = video_duration or 0.0
    return {
        "path": str(p),
        "duration": round(duration, 6),
        "video_duration": round(video_duration if video_duration else duration, 6),
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "rotation": rotation,
        "has_video": video is not None,
        "has_audio": audio is not None,
        "vcodec": video.get("codec_name") if video else None,
        "acodec": audio.get("codec_name") if audio else None,
        "sample_rate": num(audio.get("sample_rate"), int) if audio else None,
        "channels": audio.get("channels") if audio else None,
        "bitrate": num(fmt.get("bit_rate"), int),
        "size_bytes": num(fmt.get("size"), int) or p.stat().st_size,
        "nb_frames": nb_frames,
    }


# --------------------------------------------------------------------------- escaping

def esc(value):
    """Escape a value for use as a filter option inside a -filter_complex graph.

    ffmpeg parses filter graphs in two levels (option level, then graph level), so
    each level's special characters are escaped in turn. The result is safe to use
    unquoted: file paths, colours, expressions with commas, force_style strings.
    """
    text = str(value)
    for ch, rep in (("\\", "\\\\"), ("'", "\\'"), (":", "\\:")):
        text = text.replace(ch, rep)
    for ch, rep in (("\\", "\\\\"), ("'", "\\'"), (",", "\\,"), (";", "\\;"), ("[", "\\["), ("]", "\\]")):
        text = text.replace(ch, rep)
    return text


def esc_path(path):
    """esc() for a file path; Windows backslashes become forward slashes first."""
    text = str(path)
    if os.name == "nt":
        text = text.replace("\\", "/")
    return esc(text)


# --------------------------------------------------------------------------- fonts

def _font_dirs():
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        dirs = [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
                Path("/Library/Fonts"), home / "Library/Fonts"]
    elif system == "Windows":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))
        dirs = [windir / "Fonts", local / "Microsoft/Windows/Fonts"]
    else:
        dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts", home / ".local/share/fonts"]
    return [d for d in dirs if d.is_dir()]


@functools.lru_cache(maxsize=1)
def _font_index():
    """Map lower-case file stem -> path for every font file in the system font folders."""
    index = {}
    for d in _font_dirs():
        try:
            for f in d.rglob("*"):
                if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                    index.setdefault(f.stem.lower(), str(f))
        except OSError:
            continue
    return index


_DEFAULT_FONTS = ["dejavusans", "liberationsans-regular", "liberationsans", "arial", "helvetica", "helveticaneue",
                  "notosans-regular", "roboto-regular", "freesans", "segoeui"]
_BOLD_SUFFIXES = ["-bold", "bold", "-bd", "bd", " bold", "-b", "_bold"]


def _pick(stem, bold, index):
    if bold:
        for suffix in _BOLD_SUFFIXES:
            hit = index.get(stem + suffix)
            if hit:
                return hit
        # Regular-named files such as liberationsans-regular have a -bold sibling.
        if stem.endswith("-regular"):
            hit = index.get(stem[: -len("-regular")] + "-bold")
            if hit:
                return hit
    return index.get(stem)


def find_font(name=None, bold=False):
    """Return a font file path for drawtext, or None to fall back to fontconfig.

    `name` may be an existing file path, or a font file name / family stem such as
    "Arial" or "DejaVuSans". Raises VSError when an explicitly named font is missing.
    """
    if name:
        candidate = Path(str(name)).expanduser()
        if candidate.is_file():
            return str(candidate)
        index = _font_index()
        stem = candidate.stem.lower().replace(" ", "")
        for key in (stem, candidate.stem.lower()):
            hit = _pick(key, bold, index)
            if hit:
                return hit
        loose = [k for k in index if stem and stem in k.replace(" ", "")]
        if loose:
            loose.sort(key=len)
            hit = _pick(loose[0], bold, index)
            if hit:
                return hit
        raise VSError("font not found: %s (give a font file path or an installed font name)" % name)
    index = _font_index()
    for stem in _DEFAULT_FONTS:
        hit = _pick(stem, bold, index)
        if hit:
            return hit
    return None


@functools.lru_cache(maxsize=1)
def xfade_names():
    """Names accepted by ffmpeg's xfade filter, read from this ffmpeg build ("" set if unknown)."""
    try:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=xfade"], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    names, inside = set(), False
    for line in res.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("transition"):
            inside = True
            continue
        if inside:
            parts = stripped.split()
            if len(parts) >= 3 and parts[1].isdigit() and line.startswith("     "):
                names.add(parts[0])
            elif stripped and not line.startswith("     "):
                break
    return frozenset(names)
