"""Thin wrappers around ffmpeg / ffprobe plus small shared helpers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


class ClipError(RuntimeError):
    """A user-facing failure (bad input, missing binary, ffmpeg error)."""


def need(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise ClipError(f"{binary} not found on PATH - install ffmpeg to use Clipswami")
    return path


def run(cmd: list[str], cwd: str | Path | None = None, check: bool = True,
        binary: bool = False) -> subprocess.CompletedProcess:
    """Run a command, capturing output. Raises ClipError with ffmpeg's tail on failure."""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=not binary)
    if check and proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        tail = "\n".join(err.strip().splitlines()[-12:])
        raise ClipError(f"{Path(cmd[0]).name} failed ({proc.returncode}):\n{tail}")
    return proc


def probe(path: str | Path) -> dict:
    """Return the facts the pipeline needs about a media file."""
    path = Path(path)
    if not path.is_file():
        raise ClipError(f"no such file: {path}")
    out = run([need("ffprobe"), "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)]).stdout
    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    if video is None:
        raise ClipError(f"{path.name} has no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]

    width, height = int(video["width"]), int(video["height"])
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(side["rotation"])
    if abs(rotation) in (90, 270):
        width, height = height, width

    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ClipError(f"could not read the duration of {path.name}")
    return {
        "path": str(path.resolve()),
        "duration": duration,
        "width": width,
        "height": height,
        "fps": _rate(video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"),
        "vcodec": video.get("codec_name"),
        "has_audio": audio is not None,
        "acodec": audio.get("codec_name") if audio else None,
        "subtitle_streams": [
            {"index": i, "codec": s.get("codec_name"),
             "language": (s.get("tags") or {}).get("language")}
            for i, s in enumerate(subs)
        ],
    }


def _rate(text: str) -> float:
    try:
        num, _, den = text.partition("/")
        value = float(num) / float(den or 1)
        return value if 1 <= value <= 240 else 30.0
    except (ValueError, ZeroDivisionError):
        return 30.0


_UNIT = re.compile(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s?)?$")


def parse_time(value) -> float:
    """Accept 90, '90.5', '1:30', '01:02:03.5', '1m30s', '2h', '45s'."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if not text:
        raise ClipError("empty timestamp")
    if ":" in text:
        total = 0.0
        try:
            for part in text.split(":"):
                total = total * 60 + float(part)
        except ValueError:
            raise ClipError(f"bad timestamp: {value!r}") from None
        return total
    match = _UNIT.match(text)
    if not match or not any(match.groups()):
        raise ClipError(f"bad timestamp: {value!r}")
    h, m, s = (float(g) if g else 0.0 for g in match.groups())
    return h * 3600 + m * 60 + s


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}" if h else f"{int(m)}:{s:04.1f}"


def slugify(text: str, fallback: str = "clip") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")
    return slug or fallback
