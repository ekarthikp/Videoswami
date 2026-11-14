"""Fetch a source video from a link (YouTube and anything else yt-dlp understands).

Site extraction changes week to week, so this one job is delegated to yt-dlp rather
than re-implemented. It is only needed when the input is a link, never for local files.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path

from . import ff

_URL = re.compile(r"^(https?://|www\.)\S+$", re.I)
INSTALL_HINT = "python3 -m pip install --user -U yt-dlp"


def is_url(text: str) -> bool:
    return bool(_URL.match(str(text).strip()))


def downloader() -> list[str] | None:
    """The command prefix for yt-dlp, or None when it is not installed."""
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return None


def build_command(url: str, dest: Path, height: int = 1080, section: str | None = None,
                  prefix: list[str] | None = None) -> list[str]:
    cmd = list(prefix or ["yt-dlp"]) + [
        "--no-playlist", "--no-progress", "--restrict-filenames", "--no-overwrites",
        "-f", f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/bv*[height<={height}]+ba/"
              f"b[height<={height}]/b",
        "--merge-output-format", "mp4",
        "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt/srt/best",
        "-o", str(dest / "%(title).80s-%(id)s.%(ext)s"),
        "--print", "after_move:filepath", "--no-simulate",
    ]
    if section:                                              # only pull part of a long video
        start, _, end = section.partition("-")
        span = f"*{ff.parse_time(start):.0f}-{ff.parse_time(end):.0f}" if end else f"*{ff.parse_time(start):.0f}-inf"
        cmd += ["--download-sections", span, "--force-keyframes-at-cuts"]
    return cmd + [url if "://" in url else "https://" + url]


def download(url: str, dest: Path | None = None, height: int = 1080,
             section: str | None = None) -> Path:
    """Download `url` (with subtitles when they exist) and return the local video path."""
    prefix = downloader()
    if prefix is None:
        raise ff.ClipError(f"links need yt-dlp, which is not installed - run: {INSTALL_HINT}")
    dest = dest or Path.cwd() / "downloads"
    dest.mkdir(parents=True, exist_ok=True)
    print(f"fetching {url} ...", flush=True)
    proc = ff.run(build_command(url, dest, height, section, prefix), check=False)
    paths = [Path(line.strip()) for line in (proc.stdout or "").splitlines() if line.strip()]
    found = next((p for p in reversed(paths) if p.is_file()), None)
    if found is None:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
        raise ff.ClipError(f"download failed:\n{tail}\nIf yt-dlp is out of date, run: {INSTALL_HINT}")
    print(f"saved {found}", flush=True)
    return found
