"""Transcript handling: sidecar SRT/VTT, embedded subtitle tracks, optional local whisper."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import ff

_STAMP = re.compile(
    r"((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})")
_TAG = re.compile(r"<[^>]+>|\{[^}]*\}")
_TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}


def _stamp(text: str) -> float:
    return ff.parse_time(text.replace(",", "."))


def parse_subtitles(text: str) -> list[dict]:
    """Parse SRT or WebVTT text into [{start, end, text}], merging rolling duplicates."""
    cues: list[dict] = []
    current = None
    for raw in text.replace("﻿", "").splitlines():
        line = raw.strip()
        match = _STAMP.search(line)
        if match:
            current = {"start": _stamp(match.group(1)), "end": _stamp(match.group(2)), "lines": []}
            cues.append(current)
        elif not line:
            current = None
        elif current is not None:
            clean = _TAG.sub("", line).strip()
            if clean:
                current["lines"].append(clean)

    result: list[dict] = []
    last_line = ""
    for cue in cues:
        # YouTube-style rolling captions repeat the previous line; drop the repeats.
        lines = [ln for ln in cue["lines"] if ln != last_line]
        if cue["lines"]:
            last_line = cue["lines"][-1]
        text_value = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if text_value and cue["end"] > cue["start"]:
            result.append({"start": round(cue["start"], 3), "end": round(cue["end"], 3),
                           "text": text_value})
    result.sort(key=lambda c: c["start"])
    return result


def find_sidecar(video: Path) -> Path | None:
    stem = video.with_suffix("")
    for suffix in (".srt", ".vtt", ".en.srt", ".en.vtt"):
        candidate = Path(str(stem) + suffix)
        if candidate.is_file():
            return candidate
    for candidate in sorted(video.parent.glob(video.stem + ".*")):
        if candidate.suffix.lower() in (".srt", ".vtt"):
            return candidate
    return None


def _from_embedded(video: Path, info: dict, workdir: Path) -> list[dict]:
    for stream in info.get("subtitle_streams", []):
        if stream.get("codec") in _TEXT_SUB_CODECS:
            out = workdir / "embedded.srt"
            proc = ff.run([ff.need("ffmpeg"), "-y", "-v", "error", "-i", str(video),
                           "-map", f"0:s:{stream['index']}", str(out)], check=False)
            if proc.returncode == 0 and out.is_file():
                cues = parse_subtitles(out.read_text(errors="replace"))
                if cues:
                    return cues
    return []


WHISPER_COMMANDS = ("whisper-ctranslate2", "whisper")   # fast CPU build first, then OpenAI's
WHISPER_INSTALL = "python3 -m pip install --user -U whisper-ctranslate2"


def whisper_command() -> str | None:
    """Path of a local whisper CLI, or None. Both supported CLIs share the same flags."""
    for name in WHISPER_COMMANDS:
        path = shutil.which(name)
        if path:
            return path
    return None


def write_srt(cues: list[dict], path: Path) -> None:
    def stamp(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        sec, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
    blocks = [f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}\n"
              for i, c in enumerate(cues, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")


def run_whisper(video: Path, workdir: Path, model: str = "base",
                language: str | None = None, strict: bool = False) -> list[dict]:
    """Transcribe speech with a local whisper CLI. Returns [] if none is installed.

    The audio is first reduced to 16 kHz mono, which is all whisper uses, so even
    long videos are handed over as a small file.
    """
    binary = whisper_command()
    if not binary:
        if strict:
            raise ff.ClipError(f"no whisper command found - run: {WHISPER_INSTALL}")
        return []
    out_dir = workdir / "whisper"
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = out_dir / "audio.wav"
    ff.run([ff.need("ffmpeg"), "-y", "-v", "error", "-i", str(video), "-vn", "-sn",
            "-ac", "1", "-ar", "16000", str(audio)])
    cmd = [binary, str(audio), "--model", model, "--output_format", "srt",
           "--output_dir", str(out_dir), "--verbose", "False"]
    if language:
        cmd += ["--language", language]
    proc = ff.run(cmd, check=False)
    produced = out_dir / "audio.srt"
    audio.unlink(missing_ok=True)
    if proc.returncode == 0 and produced.is_file():
        return parse_subtitles(produced.read_text(errors="replace"))
    if strict:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
        raise ff.ClipError(f"whisper failed:\n{tail}")
    return []


def load(video: Path, info: dict, workdir: Path, explicit: str | None = None,
         transcribe: str = "auto", model: str = "base",
         language: str | None = None) -> tuple[list[dict], str]:
    """Return (cues, source). Never fails for lack of a transcript - returns ([], 'none')."""
    workdir.mkdir(parents=True, exist_ok=True)
    cache = workdir / "transcript.json"
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ff.ClipError(f"transcript not found: {path}")
        cues = parse_subtitles(path.read_text(errors="replace"))
        return cues, f"file:{path.name}"

    if cache.is_file():
        try:
            cached = json.loads(cache.read_text())
            if cached.get("mtime") == int(video.stat().st_mtime):
                return cached["cues"], cached["source"]
        except (ValueError, OSError, KeyError):
            pass

    cues, source = [], "none"
    sidecar = find_sidecar(video)
    if sidecar:
        cues, source = parse_subtitles(sidecar.read_text(errors="replace")), f"sidecar:{sidecar.name}"
    if not cues:
        cues = _from_embedded(video, info, workdir)
        source = "embedded" if cues else source
    if not cues and transcribe != "off" and info.get("has_audio"):
        cues = run_whisper(video, workdir, model, language)
        source = "whisper" if cues else source
    if not cues:
        source = "none"
    cache.write_text(json.dumps({"mtime": int(video.stat().st_mtime), "source": source, "cues": cues}))
    return cues, source


def window(cues: list[dict], start: float, end: float) -> list[dict]:
    """Cues overlapping [start, end], clipped and shifted to clip-relative time."""
    out = []
    for cue in cues:
        if cue["end"] <= start or cue["start"] >= end:
            continue
        out.append({"start": max(cue["start"], start) - start,
                    "end": min(cue["end"], end) - start,
                    "text": cue["text"]})
    return out


def excerpt(cues: list[dict], start: float, end: float, limit: int = 700) -> str:
    text = " ".join(c["text"] for c in cues if c["end"] > start and c["start"] < end)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."
