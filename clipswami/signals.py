"""Signal analysis: loudness envelope, silences and scene cuts - all via ffmpeg."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ff

ANALYSIS_VERSION = 3
ENERGY_DT = 0.1          # seconds per loudness sample
_SAMPLE_RATE = 8000
_FLOOR_DB = -90.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def energy_envelope(path: str) -> list[float]:
    """RMS level in dB for every ENERGY_DT window of the mixed-down audio."""
    n = int(_SAMPLE_RATE * ENERGY_DT)
    out = ff.run([
        ff.need("ffmpeg"), "-hide_banner", "-nostats", "-v", "error", "-i", path,
        "-vn", "-sn", "-ac", "1", "-ar", str(_SAMPLE_RATE),
        "-af", f"asetnsamples=n={n}:p=1,astats=metadata=1:reset=1,"
               "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
        "-f", "null", "-",
    ]).stdout
    levels: list[float] = []
    for line in out.splitlines():
        if line.startswith("lavfi.astats.Overall.RMS_level="):
            raw = line.split("=", 1)[1].strip()
            try:
                value = float(raw)
            except ValueError:
                value = _FLOOR_DB
            if value != value or value < _FLOOR_DB:   # NaN or -inf
                value = _FLOOR_DB
            levels.append(value)
    return levels


def scene_cuts(path: str, threshold: float = 0.28) -> list[list[float]]:
    """[time, score] for every detected hard cut."""
    out = ff.run([
        ff.need("ffmpeg"), "-hide_banner", "-nostats", "-v", "error", "-i", path,
        "-an", "-sn",
        "-vf", f"fps=8,scale=160:-2,select='gt(scene,{threshold})',metadata=print:file=-",
        "-f", "null", "-",
    ]).stdout
    cuts: list[list[float]] = []
    when = None
    for line in out.splitlines():
        match = re.search(r"pts_time:([0-9.]+)", line)
        if match:
            when = float(match.group(1))
        elif line.startswith("lavfi.scene_score=") and when is not None:
            cuts.append([round(when, 3), round(float(line.split("=", 1)[1]), 3)])
            when = None
    return cuts


def speech_threshold(energy: list[float]) -> float | None:
    """Adaptive dB level separating speech/action from dead air.

    Returns None when the track has no usable dynamics (e.g. wall-to-wall music),
    in which case nothing is treated as silence.
    """
    if not energy:
        return None
    floor = percentile(energy, 5)
    loud = percentile(energy, 80)
    if loud - floor < 6.0:
        return None
    return max(-60.0, min(-25.0, floor + 0.3 * (loud - floor)))


def find_silences(energy: list[float], min_len: float = 0.3) -> list[list[float]]:
    """[start, end] spans where the envelope stays under the adaptive threshold."""
    threshold = speech_threshold(energy)
    if threshold is None:
        return []
    spans: list[list[float]] = []
    start = None
    for i, level in enumerate(energy):
        quiet = level < threshold
        if quiet and start is None:
            start = i
        elif not quiet and start is not None:
            if (i - start) * ENERGY_DT >= min_len:
                spans.append([round(start * ENERGY_DT, 2), round(i * ENERGY_DT, 2)])
            start = None
    if start is not None and (len(energy) - start) * ENERGY_DT >= min_len:
        spans.append([round(start * ENERGY_DT, 2), round(len(energy) * ENERGY_DT, 2)])
    return spans


def analyze(path: str | Path, workdir: Path, force: bool = False) -> dict:
    """Probe + analyse a video, caching the result in workdir/analysis.json."""
    path = Path(path)
    info = ff.probe(path)
    stat = path.stat()
    signature = [ANALYSIS_VERSION, stat.st_size, int(stat.st_mtime)]
    cache = workdir / "analysis.json"
    if cache.is_file() and not force:
        try:
            cached = json.loads(cache.read_text())
            if cached.get("signature") == signature:
                return cached
        except (ValueError, OSError):
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        cuts_job = pool.submit(scene_cuts, info["path"])
        energy = energy_envelope(info["path"]) if info["has_audio"] else []
        cuts = cuts_job.result()

    result = {
        "signature": signature,
        "info": info,
        "energy_dt": ENERGY_DT,
        "energy": [round(v, 1) for v in energy],
        "silences": find_silences(energy),
        "scenes": cuts,
    }
    workdir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result))
    return result
