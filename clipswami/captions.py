"""Burned-in captions: word-timed, chunked, highlighted - written as an ASS file."""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"[.!?…]$")
_WHITE = "&H00FFFFFF"
_HIGHLIGHT = "&H0000E5FF"        # ASS colours are AABBGGRR: this is a warm yellow
_ACCENT = "&H0078E650"           # green, for words worth stressing
_POWER = {
    "never", "always", "nobody", "everyone", "secret", "truth", "mistake", "biggest", "best",
    "worst", "crazy", "insane", "stop", "free", "money", "million", "billion", "first", "only",
    "wrong", "love", "hate", "fear", "win", "lost", "failed", "doubled", "tripled", "everything",
    "nothing", "impossible", "easy", "hard",
}


def is_power_word(word: str) -> bool:
    core = re.sub(r"[^a-z0-9%$]", "", word.lower())
    return bool(core) and (core in _POWER or core[0].isdigit() or core[0] == "$")


def _stamp(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def word_times(cues: list[dict]) -> list[tuple[str, float, float]]:
    """Spread each cue's duration over its words in proportion to their length."""
    words: list[tuple[str, float, float]] = []
    for cue in cues:
        parts = cue["text"].split()
        if not parts:
            continue
        weights = [len(p) + 1 for p in parts]
        total = float(sum(weights))
        span = cue["end"] - cue["start"]
        t = cue["start"]
        for part, weight in zip(parts, weights):
            dur = span * weight / total
            words.append((part, t, t + dur))
            t += dur
    return words


def chunk_words(words: list[tuple[str, float, float]], max_words: int,
                max_chars: int) -> list[list[tuple[str, float, float]]]:
    """Group words into short on-screen phrases, breaking at pauses and sentence ends."""
    chunks: list[list[tuple[str, float, float]]] = []
    current: list[tuple[str, float, float]] = []
    chars = 0
    for word in words:
        text, start, _ = word
        if current:
            prev_text, _, prev_end = current[-1]
            if (len(current) >= max_words or chars + len(text) + 1 > max_chars
                    or start - prev_end > 0.6 or _SENTENCE_END.search(prev_text)):
                chunks.append(current)
                current, chars = [], 0
        current.append(word)
        chars += len(text) + 1
    if current:
        chunks.append(current)
    return chunks


def build_ass(cues: list[dict], width: int, height: int, style: str = "bold",
              title: str | None = None, title_secs: float = 3.5, font: str = "DejaVu Sans",
              remap=None, total: float | None = None) -> str:
    """Return ASS text. `cues` are clip-relative; `remap` maps clip time -> output time."""
    remap = remap or (lambda t: t)
    portrait = height >= width
    unit = width if portrait else height
    if style == "clean":
        cap_size, max_words, max_chars = round(unit * 0.05), 7, 34 if portrait else 48
    else:
        cap_size, max_words, max_chars = round(unit * 0.078), 3, 16 if portrait else 26
    outline = max(2, round(cap_size * 0.075))
    margin_v = round(height * (0.2 if portrait else 0.09))
    margin_h = round(width * 0.07)
    title_size = round(unit * 0.056)

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font},{cap_size},{_WHITE},{_WHITE},&H00000000,&H78000000,-1,0,0,0,"
        f"100,100,0,0,1,{outline},{max(1, outline // 2)},2,{margin_h},{margin_h},{margin_v},1",
        f"Style: Title,{font},{title_size},{_WHITE},{_WHITE},&H50000000,&H50000000,-1,0,0,0,"
        f"100,100,0,0,3,{round(title_size * 0.35)},0,8,{margin_h},{margin_h},"
        f"{round(height * 0.085)},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def event(start: float, end: float, style_name: str, text: str, layer: int = 0) -> None:
        if end - start >= 0.04:
            lines.append(f"Dialogue: {layer},{_stamp(start)},{_stamp(end)},{style_name},,0,0,0,,{text}")

    if title:
        end = min(title_secs, total) if total else title_secs
        event(0.0, end, "Title", "{\\fad(150,300)}" + _escape(title), layer=1)

    if style != "off" and cues:
        words = [(w, remap(a), remap(b)) for w, a, b in word_times(cues)]
        words = [(w, a, max(b, a)) for w, a, b in words]
        chunks = chunk_words(words, max_words, max_chars)
        for index, chunk in enumerate(chunks):
            chunk_end = chunk[-1][2]
            if index + 1 < len(chunks):
                next_start = chunks[index + 1][0][1]
                if next_start - chunk_end < 0.35:           # bridge tiny gaps: no flicker
                    chunk_end = next_start
                chunk_end = min(chunk_end, next_start)
            if style == "clean":
                text = _escape(" ".join(w for w, _, _ in chunk))
                event(chunk[0][1], chunk_end, "Cap", text)
                continue
            shown = [_escape(re.sub(r"[.,;:]+$", "", w).upper()) or _escape(w) for w, _, _ in chunk]
            stress = [is_power_word(w) for w, _, _ in chunk]
            for i, (_, start, end) in enumerate(chunk):
                stop = chunk[i + 1][1] if i + 1 < len(chunk) else chunk_end
                pieces = []
                for j, w in enumerate(shown):
                    colour = _HIGHLIGHT if j == i else (_ACCENT if stress[j] else None)
                    pieces.append(f"{{\\c{colour}&}}{w}{{\\c{_WHITE}&}}" if colour else w)
                pop = "{\\fscx88\\fscy88\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
                event(start, stop, "Cap", pop + " ".join(pieces))
    return "\n".join(lines) + "\n"
