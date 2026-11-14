"""Highlight selection: turn raw signals (+ optional transcript) into ranked clip candidates."""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right

from . import transcript as tr
from .signals import percentile, speech_threshold

_SENTENCE_END = re.compile(r"[.!?…]['\")\]]*$")
_HOOK_WORDS = {
    "secret", "secrets", "never", "always", "nobody", "everyone", "truth", "mistake",
    "mistakes", "why", "how", "best", "worst", "biggest", "crazy", "insane", "actually",
    "imagine", "stop", "wait", "listen", "here's", "heres", "story", "learned", "lesson",
    "changed", "problem", "trick", "hack", "money", "million", "billion", "free", "first",
    "last", "only", "important", "remember", "surprising", "wrong", "real", "reason",
    "because", "love", "hate", "fear", "win", "lost", "failed", "easy", "hard", "simple",
}
_OPENERS = ("so here", "here's", "the thing is", "let me tell", "what if", "did you know",
            "the truth", "the problem", "the reason", "number one", "first of all",
            "i think", "the biggest", "the secret", "most people", "imagine", "why", "how")

WEIGHTS_SIGNAL = {"energy": .22, "peaks": .13, "dynamics": .10, "speech": .12,
                  "hook": .13, "pace": .05, "boundaries": .15, "length": .10}
WEIGHTS_TEXT = {"energy": .13, "peaks": .07, "dynamics": .06, "speech": .08, "hook": .12,
                "pace": .04, "boundaries": .12, "length": .08, "text": .20, "arc": .10}

_LABELS = {
    "energy": "high energy", "peaks": "loud peaks", "dynamics": "dynamic delivery",
    "speech": "dense, little dead air", "hook": "strong opening", "pace": "fast visual pace",
    "boundaries": "clean in/out points", "length": "ideal length", "text": "hooky wording",
    "arc": "self-contained thought",
}
_STOP = set("""a an and are as at be but by for from had has have he her his i if in into is it
its me my no not of on or our she so that the their them then there they this to up us was we
were what when which who will with you your just like really very about all also can do does
did get got go going been being am im its dont thats than too out over more some such""".split())


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


class _Track:
    """Prefix sums over the loudness envelope so any window is scored in O(1)."""

    def __init__(self, energy: list[float], dt: float):
        self.dt = dt
        self.n = len(energy)
        if energy:
            floor, top = percentile(energy, 5), percentile(energy, 95)
            span = max(top - floor, 1e-6)
            loud = [_clip01((v - floor) / span) for v in energy]
        else:
            loud = []
        threshold = speech_threshold(energy)
        hot = percentile(loud, 90) if loud else 1.0
        self.sum = [0.0]
        self.sq = [0.0]
        self.active = [0]
        self.peak = [0]
        for i, value in enumerate(loud):
            self.sum.append(self.sum[-1] + value)
            self.sq.append(self.sq[-1] + value * value)
            is_active = threshold is None or energy[i] >= threshold
            self.active.append(self.active[-1] + (1 if is_active else 0))
            self.peak.append(self.peak[-1] + (1 if value >= hot and hot > 0 else 0))
        self.mean = self.sum[-1] / self.n if self.n else 0.0
        var = self.sq[-1] / self.n - self.mean ** 2 if self.n else 0.0
        self.std = math.sqrt(max(var, 0.0))

    def _idx(self, a: float, b: float) -> tuple[int, int]:
        i = max(0, min(self.n, int(round(a / self.dt))))
        j = max(i, min(self.n, int(round(b / self.dt))))
        return i, j

    def stats(self, a: float, b: float) -> tuple[float, float, float, float]:
        """(mean loudness, std, active fraction, peak fraction) for [a, b]."""
        i, j = self._idx(a, b)
        count = j - i
        if count <= 0:
            return 0.0, 0.0, 0.0, 0.0
        mean = (self.sum[j] - self.sum[i]) / count
        var = (self.sq[j] - self.sq[i]) / count - mean * mean
        return (mean, math.sqrt(max(var, 0.0)),
                (self.active[j] - self.active[i]) / count,
                (self.peak[j] - self.peak[i]) / count)


def topic_shifts(cues: list[dict], span: int = 4) -> list[float]:
    """TextTiling-style topic boundaries.

    Returns one 0..1 value per cue: how strongly the subject changes right before it.
    Vocabulary on each side of every gap is compared; a deep dip in similarity between
    two more-cohesive stretches marks the start of a new thought.
    """
    n = len(cues)
    if n < span * 2 + 2:
        return [0.0] * n
    bags = []
    for cue in cues:
        bag: dict[str, int] = {}
        for word in re.findall(r"[a-z']+", cue["text"].lower()):
            word = word.strip("'")
            if len(word) > 2 and word not in _STOP:
                bag[word] = bag.get(word, 0) + 1
        bags.append(bag)

    def merged(lo: int, hi: int) -> dict[str, int]:
        out: dict[str, int] = {}
        for bag in bags[max(lo, 0):min(hi, n)]:
            for word, count in bag.items():
                out[word] = out.get(word, 0) + count
        return out

    sims = [1.0] * n
    for i in range(1, n):
        left, right = merged(i - span, i), merged(i, i + span)
        dot = sum(v * right.get(k, 0) for k, v in left.items())
        norm = math.sqrt(sum(v * v for v in left.values()) * sum(v * v for v in right.values()))
        sims[i] = dot / norm if norm else 0.0

    depths = [0.0] * n
    for i in range(1, n):
        peak_l = sims[i]
        j = i - 1
        while j >= 1 and sims[j] >= peak_l:
            peak_l = sims[j]
            j -= 1
        peak_r = sims[i]
        j = i + 1
        while j < n and sims[j] >= peak_r:
            peak_r = sims[j]
            j += 1
        depths[i] = (peak_l - sims[i]) + (peak_r - sims[i])
    top = max(depths)
    if top <= 1e-9:
        return [0.0] * n
    mean = sum(depths) / n
    std = math.sqrt(sum((d - mean) ** 2 for d in depths) / n)
    cutoff = mean + 0.5 * std
    return [_clip01(d / top) if d >= cutoff else 0.0 for d in depths]


def snap(analysis: dict, cues: list[dict], start: float, end: float,
         reach: float = 0.9) -> tuple[float, float]:
    """Nudge rough in/out points onto the nearest clean edit point (pause or sentence edge)."""
    starts, ends = _boundaries(analysis, cues or [])
    duration = analysis["info"]["duration"]

    def nearest(points, t):
        best = None
        for when, quality in points:
            if quality >= 0.5 and abs(when - t) <= reach:
                if best is None or abs(when - t) < abs(best - t):
                    best = when
        return best

    new_start, new_end = nearest(starts, start), nearest(ends, end)
    start = max(0.0, new_start - 0.08) if new_start is not None else start
    end = min(duration, new_end + 0.18) if new_end is not None else end
    return round(start, 2), round(end, 2)


def _dedupe(points: dict[float, float], gap: float = 0.3) -> list[tuple[float, float]]:
    """Merge boundary points closer than `gap`, keeping the higher-quality one."""
    merged: list[tuple[float, float]] = []
    for t in sorted(points):
        q = points[t]
        if merged and t - merged[-1][0] < gap:
            if q > merged[-1][1]:
                merged[-1] = (t, q)
        else:
            merged.append((t, q))
    return merged


def _boundaries(analysis: dict, cues: list[dict]) -> tuple[list, list]:
    """Candidate in-points and out-points, each with a 0..1 cleanliness score."""
    duration = analysis["info"]["duration"]
    starts: dict[float, float] = {0.0: 0.6}
    ends: dict[float, float] = {round(duration, 2): 0.6}

    for a, b in analysis["silences"]:
        quality = _clip01(0.45 + (b - a) / 1.6)          # longer pause = cleaner edit point
        if b < duration - 1:
            starts[round(b, 2)] = max(starts.get(round(b, 2), 0), quality)
        if a > 1:
            ends[round(a, 2)] = max(ends.get(round(a, 2), 0), quality)

    for t, score in analysis["scenes"]:
        quality = _clip01(0.4 + score * 0.5)
        starts.setdefault(round(t, 2), quality)
        ends.setdefault(round(t, 2), quality)

    prev = None
    for cue in cues:
        fresh = prev is None or _SENTENCE_END.search(prev["text"]) or cue["start"] - prev["end"] > 0.6
        s = round(cue["start"], 2)
        starts[s] = max(starts.get(s, 0), 1.0 if fresh else 0.35)
        e = round(cue["end"], 2)
        ends[e] = max(ends.get(e, 0), 1.0 if _SENTENCE_END.search(cue["text"]) else 0.3)
        prev = cue

    if len(starts) < 8 or len(ends) < 8:                 # featureless footage: fall back to a grid
        t = 0.0
        while t < duration:
            starts.setdefault(round(t, 2), 0.2)
            ends.setdefault(round(t, 2), 0.2)
            t += 2.0
    return _dedupe(starts), _dedupe(ends)


def _text_score(cues: list[dict], start: float, end: float) -> tuple[float, float]:
    """(overall wording score, opening-line hook score) for the transcript in a window."""
    inside = [c for c in cues if c["end"] > start + 0.2 and c["start"] < end - 0.2]
    if not inside:
        return 0.0, 0.0
    text = " ".join(c["text"] for c in inside)
    words = re.findall(r"[a-zA-Z']+|\d[\d,.]*", text.lower())
    if not words:
        return 0.0, 0.0
    per100 = 100.0 / len(words)
    hooks = sum(1 for w in words if w in _HOOK_WORDS) * per100
    numbers = sum(1 for w in words if w[0].isdigit()) * per100
    second_person = sum(1 for w in words if w in ("you", "your", "you're")) * per100
    punch = (text.count("?") * 1.5 + text.count("!")) * per100
    wpm = len(words) / max((end - start) / 60.0, 1e-6)
    density = _clip01(wpm / 150.0)
    overall = _clip01(0.25 * _clip01(hooks / 6) + 0.15 * _clip01(numbers / 3)
                      + 0.2 * _clip01(second_person / 5) + 0.15 * _clip01(punch / 3)
                      + 0.25 * density)

    opening = inside[0]["text"].lower()
    first_words = re.findall(r"[a-z']+", opening)[:8]
    opener = 0.0
    if "?" in opening:
        opener += 0.5
    if any(opening.startswith(p) for p in _OPENERS):
        opener += 0.4
    opener += 0.15 * sum(1 for w in first_words if w in _HOOK_WORDS)
    return overall, _clip01(opener)


def find_candidates(analysis: dict, cues: list[dict] | None = None, count: int = 10,
                    min_len: float = 15.0, max_len: float = 60.0,
                    target: float | None = None) -> list[dict]:
    """Rank the best clip windows in the video. Returns up to `count` non-overlapping picks."""
    cues = cues or []
    duration = analysis["info"]["duration"]
    max_len = min(max_len, duration)
    min_len = min(min_len, max_len)
    target = target if target else (min_len + max_len) / 2
    track = _Track(analysis["energy"], analysis["energy_dt"])
    cut_times = [c[0] for c in analysis["scenes"]]
    weights = WEIGHTS_TEXT if cues else WEIGHTS_SIGNAL
    starts, ends = _boundaries(analysis, cues)
    end_times = [e[0] for e in ends]
    has_audio = track.n > 0
    open_t: list[float] = []             # cue starts, and how strongly a new topic opens there
    open_v: list[float] = []
    close_t: list[float] = []            # cue ends, and how strongly the topic changes after
    close_v: list[float] = []
    if cues:
        shifts = topic_shifts(cues)
        shifts[0] = 1.0
        for i, cue in enumerate(cues):
            open_t.append(cue["start"])
            open_v.append(shifts[i])
            close_t.append(cue["end"])
            close_v.append(shifts[i + 1] if i + 1 < len(cues) else 1.0)
        close_order = sorted(range(len(close_t)), key=close_t.__getitem__)
        close_t = [close_t[i] for i in close_order]
        close_v = [close_v[i] for i in close_order]

    def near(times: list[float], values: list[float], t: float) -> float:
        i = bisect_left(times, t)
        best = 0.0
        for j in (i - 1, i):
            if 0 <= j < len(times) and abs(times[j] - t) <= 0.35:
                best = max(best, values[j])
        return best

    best_per_start: list[dict] = []
    for s, s_quality in starts:
        lo = bisect_left(end_times, s + min_len)
        hi = bisect_right(end_times, s + max_len + 1e-6)
        best = None
        for e, e_quality in ends[lo:hi]:
            length = e - s
            mean, std, active, peaks = track.stats(s, e)
            head_mean = track.stats(s, min(e, s + 3.0))[0]
            cuts = bisect_right(cut_times, e) - bisect_left(cut_times, s)
            if has_audio:
                spread = track.std + 1e-6
                parts = {
                    "energy": _sigmoid(2.2 * (mean - track.mean) / spread),
                    "peaks": _clip01(peaks * 3.0),
                    "dynamics": _clip01(0.7 * std / spread),
                    "speech": _clip01(active / 0.8),
                    "hook": _sigmoid(2.0 * (head_mean - track.mean) / spread),
                }
            else:                                           # silent footage: lean on visuals
                parts = {"energy": 0.5, "peaks": 0.0, "dynamics": 0.0, "speech": 0.5, "hook": 0.5}
            parts["pace"] = _clip01(cuts / max(length / 60.0, 1e-6) / 12.0)
            parts["boundaries"] = (s_quality + e_quality) / 2
            parts["length"] = math.exp(-((length - target) / (0.5 * target)) ** 2)
            if cues:
                text, opener = _text_score(cues, s, e)
                parts["text"] = text
                parts["arc"] = 0.5 * near(open_t, open_v, s) + 0.5 * near(close_t, close_v, e)
                parts["hook"] = _clip01(0.55 * parts["hook"] + 0.45 * opener + 0.1)
            score = sum(weights[k] * parts[k] for k in weights)
            if best is None or score > best["score"]:
                best = {"start": s, "end": e, "score": score, "parts": parts}
        if best:
            best_per_start.append(best)

    best_per_start.sort(key=lambda c: c["score"], reverse=True)
    chosen: list[dict] = []
    for cand in best_per_start:
        length = cand["end"] - cand["start"]
        clash = False
        for other in chosen:
            overlap = min(cand["end"], other["end"]) - max(cand["start"], other["start"])
            if overlap > 0.35 * min(length, other["end"] - other["start"]):
                clash = True
                break
        if not clash:
            chosen.append(cand)
            if len(chosen) >= count:
                break

    results = []
    for rank, cand in enumerate(chosen, 1):
        start = max(0.0, cand["start"] - 0.08)              # breathe a little around the edit
        end = min(duration, cand["end"] + 0.18)
        ranked = sorted(weights, key=lambda k: weights[k] * cand["parts"][k], reverse=True)
        item = {
            "rank": rank,
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": round(end - start, 2),
            "score": round(cand["score"] * 100, 1),
            "why": [_LABELS[k] for k in ranked[:3]],
            "breakdown": {k: round(v, 2) for k, v in cand["parts"].items()},
        }
        if cues:
            item["transcript"] = tr.excerpt(cues, start, end)
        results.append(item)
    return results
