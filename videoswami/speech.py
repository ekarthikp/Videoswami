"""Speech commands: transcription (four backends), phrase search and transcript-driven cutting.

Transcript format (shared with captions):
  {"language","duration","source","segments":[{"start","end","text","words":[{"w","start","end"}]}]}

Standard library only at import time; every whisper backend is imported or located lazily.
"""
import difflib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from .ff import VSError, probe, run_ffmpeg
from . import quick

BACKENDS = ("cmd", "faster-whisper", "whisper", "whisper-cpp")
DEFAULT_FILLERS = "um,uh,erm,ah,hmm"
INSTALL_HELP = ("no speech-to-text backend found. Install one of: (1) pip install faster-whisper (recommended); "
                "(2) pip install openai-whisper; (3) whisper.cpp: put whisper-cli on PATH and set "
                "VIDEOSWAMI_WHISPER_CPP_MODEL to a ggml model file. "
                "Or set VIDEOSWAMI_WHISPER_CMD to a command template using {audio} and {out}.")


# --------------------------------------------------------------------------- transcript API

def _norm(word):
    """Case-insensitive, punctuation-free form of a word ('' when nothing is left)."""
    return re.sub(r"[\W_]+", "", str(word).lower())


def _num(value, what):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise VSError("transcript: %s must be a number, got %r" % (what, value))


def load_transcript(path):
    """Read and validate a transcript JSON file."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise VSError("transcript not found: %s" % path)
    try:
        t = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise VSError("transcript %s is not valid JSON: %s" % (path, exc))
    if not isinstance(t, dict) or not isinstance(t.get("segments"), list):
        raise VSError("transcript %s must be an object with a 'segments' list" % path)
    for i, seg in enumerate(t["segments"]):
        if not isinstance(seg, dict):
            raise VSError("transcript %s: segment %d is not an object" % (path, i))
        _num(seg.get("start"), "segment %d start" % i)
        _num(seg.get("end"), "segment %d end" % i)
        if not isinstance(seg.get("words"), list):
            raise VSError("transcript %s: segment %d needs a 'words' list" % (path, i))
        for j, w in enumerate(seg["words"]):
            if not isinstance(w, dict) or "w" not in w:
                raise VSError("transcript %s: segment %d word %d needs w/start/end" % (path, i, j))
            _num(w.get("start"), "segment %d word %d start" % (i, j))
            _num(w.get("end"), "segment %d word %d end" % (i, j))
    return t


def save_transcript(t, path):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(p)


def words(t):
    """Flat list of {w,start,end} in spoken order."""
    return [w for seg in t["segments"] for w in seg["words"]]


def _merge(ranges):
    out = []
    for s, e in sorted((float(s), float(e)) for s, e in ranges if e > s):
        if out and s <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def retime(t, removed_ranges):
    """Copy of `t` without the words inside `removed_ranges`, later words shifted earlier."""
    removed = _merge(removed_ranges)

    def shift(x):
        return x - sum(min(e, x) - s for s, e in removed if s < x)

    def gone(w):
        mid = (w["start"] + w["end"]) / 2.0
        return any(s <= mid < e for s, e in removed)

    segments = []
    for seg in t["segments"]:
        kept = [{"w": w["w"], "start": round(shift(w["start"]), 3), "end": round(shift(w["end"]), 3)}
                for w in seg["words"] if not gone(w)]
        if not kept:
            continue
        item = {k: v for k, v in seg.items() if k not in ("start", "end", "text", "words")}
        item.update({"start": kept[0]["start"], "end": kept[-1]["end"],
                     "text": " ".join(w["w"] for w in kept), "words": kept})
        segments.append(item)
    out = {k: v for k, v in t.items() if k != "segments"}
    if t.get("duration") is not None:
        out["duration"] = round(max(0.0, shift(float(t["duration"]))), 3)
    out["segments"] = segments
    return out


def _stamp(x):
    ms = int(round(max(0.0, x) * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def to_srt(t):
    """SubRip text: one cue per run of up to 10 words, broken at gaps longer than 0.8 s."""
    cues = []
    for seg in t["segments"]:
        cur = []
        for w in seg["words"]:
            if cur and (len(cur) >= 10 or w["start"] - cur[-1]["end"] > 0.8):
                cues.append(cur)
                cur = []
            cur.append(w)
        if cur:
            cues.append(cur)
    lines = []
    for i, cue in enumerate(cues, 1):
        lines += [str(i), "%s --> %s" % (_stamp(cue[0]["start"]), _stamp(cue[-1]["end"])),
                  " ".join(w["w"] for w in cue), ""]
    return "\n".join(lines)


def _plain_text(t):
    return "\n".join(seg["text"].strip() if seg.get("text") else " ".join(w["w"] for w in seg["words"])
                     for seg in t["segments"]) + "\n"


# --------------------------------------------------------------------------- normalising backend output

def _spread(text, start, end):
    toks = str(text).split()
    if not toks:
        return []
    step = (end - start) / len(toks)
    return [{"w": tok, "start": round(start + i * step, 3), "end": round(start + (i + 1) * step, 3)}
            for i, tok in enumerate(toks)]


def _from_openai(data, media, duration, language=None):
    """openai-whisper JSON -> our transcript. Segments without word timings get evenly spread words."""
    segments = []
    for seg in data.get("segments", []) or []:
        start, end = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        ws = []
        for w in seg.get("words") or []:
            token = str(w.get("word", w.get("w", ""))).strip()
            if token:
                ws.append({"w": token, "start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3)})
        if not ws:
            ws = _spread(seg.get("text", ""), start, end)
        if not ws:
            continue
        segments.append({"start": ws[0]["start"], "end": ws[-1]["end"], "text": " ".join(w["w"] for w in ws), "words": ws})
    return {"language": data.get("language") or language or "", "duration": round(float(duration), 3),
            "source": Path(media).name, "segments": segments}


def _extract_audio(media, dest):
    info = probe(str(media))
    if not info.get("has_audio"):
        raise VSError("%s has no audio track to transcribe" % media)
    run_ffmpeg(["-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)], progress=False)
    return info


# --------------------------------------------------------------------------- backends

def _have_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _whisper_cpp_binary():
    for name in ("whisper-cli", "main"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _available(name):
    if name == "cmd":
        return bool(os.environ.get("VIDEOSWAMI_WHISPER_CMD", "").strip())
    if name == "faster-whisper":
        return _have_module("faster_whisper")
    if name == "whisper":
        return _have_module("whisper") or shutil.which("whisper") is not None
    if name == "whisper-cpp":
        return _whisper_cpp_binary() is not None and bool(os.environ.get("VIDEOSWAMI_WHISPER_CPP_MODEL"))
    return False


def _run_tool(argv, what):
    try:
        res = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True)
    except OSError as exc:
        raise VSError("could not run %s: %s" % (what, exc))
    if res.returncode != 0:
        tail = " | ".join((res.stderr or res.stdout or "").strip().splitlines()[-3:])
        raise VSError("%s failed (exit %d): %s" % (what, res.returncode, tail))


def _read_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise VSError("%s did not write a readable JSON file at %s" % (what, path))


def _backend_cmd(audio, tmp, model, language):
    template = os.environ.get("VIDEOSWAMI_WHISPER_CMD", "").strip()
    if not template:
        raise VSError("VIDEOSWAMI_WHISPER_CMD is not set")
    out = os.path.join(tmp, "cmd-out.json")
    argv = [tok.replace("{audio}", str(audio)).replace("{out}", out)
            for tok in shlex.split(template, posix=(os.name != "nt"))]
    if not argv:
        raise VSError("VIDEOSWAMI_WHISPER_CMD is empty")
    _run_tool(argv, "VIDEOSWAMI_WHISPER_CMD")
    return _read_json(out, "VIDEOSWAMI_WHISPER_CMD")


def _backend_faster_whisper(audio, tmp, model, language):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise VSError("faster-whisper is not installed: pip install faster-whisper")
    engine = WhisperModel(model, device="auto", compute_type="default")
    segs, info = engine.transcribe(str(audio), word_timestamps=True, vad_filter=True, language=language)
    out = []
    for seg in segs:  # generator: decoding happens while iterating
        out.append({"start": seg.start, "end": seg.end, "text": seg.text,
                    "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (seg.words or [])]})
    return {"language": getattr(info, "language", None) or language, "segments": out}


def _backend_whisper(audio, tmp, model, language):
    if _have_module("whisper"):
        import whisper
        engine = whisper.load_model(model)
        kwargs = {"word_timestamps": True}
        if language:
            kwargs["language"] = language
        return engine.transcribe(str(audio), **kwargs)
    exe = shutil.which("whisper")
    if not exe:
        raise VSError("openai-whisper is not installed: pip install openai-whisper")
    argv = [exe, str(audio), "--model", model, "--word_timestamps", "True", "--output_format", "json",
            "--output_dir", tmp]
    if language:
        argv += ["--language", language]
    _run_tool(argv, "whisper")
    return _read_json(os.path.join(tmp, Path(audio).stem + ".json"), "whisper")


def _cpp_to_openai(data):
    """whisper.cpp -oj -ml 1 output (one token/word per entry, offsets in ms) -> openai-style words grouped in segments."""
    ws = []
    for item in data.get("transcription", []) or []:
        text = item.get("text", "")
        if not text.strip() or text.strip().startswith("[_"):
            continue
        off = item.get("offsets") or {}
        start, end = float(off.get("from", 0)) / 1000.0, float(off.get("to", 0)) / 1000.0
        glued = ws and not text[:1].isspace() or (ws and not _norm(text))
        if glued:  # sub-word piece or lone punctuation: belongs to the previous word
            ws[-1]["word"] += text.strip()
            ws[-1]["end"] = max(ws[-1]["end"], end)
        else:
            ws.append({"word": text.strip(), "start": start, "end": max(end, start)})
    segments, cur = [], []
    for w in ws:
        if cur and (w["start"] - cur[-1]["end"] > 0.8 or cur[-1]["word"][-1:] in ".?!"):
            segments.append(cur)
            cur = []
        cur.append(w)
    if cur:
        segments.append(cur)
    lang = (data.get("result") or {}).get("language")
    return {"language": lang, "segments": [{"start": s[0]["start"], "end": s[-1]["end"],
                                            "text": " ".join(w["word"] for w in s), "words": s} for s in segments]}


def _backend_whisper_cpp(audio, tmp, model, language):
    exe = _whisper_cpp_binary()
    if not exe:
        raise VSError("whisper.cpp binary not found: put whisper-cli (or main) on PATH")
    model_file = os.environ.get("VIDEOSWAMI_WHISPER_CPP_MODEL")
    if not model_file or not os.path.isfile(model_file):
        raise VSError("set VIDEOSWAMI_WHISPER_CPP_MODEL to a whisper.cpp ggml model file (got %r)" % model_file)
    prefix = os.path.join(tmp, "cpp")
    argv = [exe, "-m", model_file, "-f", str(audio), "-oj", "-ml", "1", "-of", prefix, "-l", language or "auto"]
    _run_tool(argv, "whisper.cpp")
    return _cpp_to_openai(_read_json(prefix + ".json", "whisper.cpp"))


_RUNNERS = {"cmd": _backend_cmd, "faster-whisper": _backend_faster_whisper, "whisper": _backend_whisper,
            "whisper-cpp": _backend_whisper_cpp}


def _choose_backend(requested):
    if requested in (None, "auto"):
        for name in BACKENDS:
            if _available(name):
                return name
        raise VSError(INSTALL_HELP)
    if requested not in _RUNNERS:
        raise VSError("unknown backend %r; choose auto, %s" % (requested, ", ".join(BACKENDS)))
    if not _available(requested):
        raise VSError("backend %s is not available here. %s" % (requested, INSTALL_HELP))
    return requested


def _transcribe(media, model, language, backend):
    """Run one backend on `media` and return (transcript, backend name)."""
    name = _choose_backend(backend)
    tmp = tempfile.mkdtemp(prefix="videoswami_stt_")
    try:
        audio = os.path.join(tmp, "audio.wav")
        info = _extract_audio(media, audio)
        raw = _RUNNERS[name](audio, tmp, model or "small", language)
        return _from_openai(raw, media, info["duration"], language), name
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _sidecar(media):
    p = Path(media)
    return p.with_name(p.stem + ".transcript.json")


def _fresh(sidecar, media):
    return sidecar.is_file() and sidecar.stat().st_mtime >= Path(media).stat().st_mtime


def ensure_transcript(media_path, transcript_path=None, model=None, language=None):
    """Load the given or sidecar transcript, or transcribe the media. Returns (transcript, path)."""
    if transcript_path:
        return load_transcript(transcript_path), str(transcript_path)
    media = quick._need_file(media_path)
    side = _sidecar(media)
    if _fresh(side, media):
        return load_transcript(side), str(side)
    t, _name = _transcribe(media, model, language, "auto")
    return t, save_transcript(t, side)


# --------------------------------------------------------------------------- commands: transcribe, find

def cmd_transcribe(a):
    media = quick._need_file(a.file)
    target = Path(a.output).expanduser() if a.output else _sidecar(media)
    cached = _fresh(target, media) and not a.force
    if cached:
        t = load_transcript(target)
        backend = None
    else:
        t, backend = _transcribe(media, a.model, a.language, a.backend)
        save_transcript(t, target)
    stem = media.with_suffix("")
    if a.srt:
        Path(str(stem) + ".srt").write_text(to_srt(t), encoding="utf-8")
    if a.txt:
        Path(str(stem) + ".txt").write_text(_plain_text(t), encoding="utf-8")
    res = {"ok": True, "transcript": str(target), "text": " ".join(w["w"] for w in words(t)),
           "word_count": len(words(t)), "language": t.get("language", ""), "backend": backend, "cached": cached}
    if a.srt:
        res["srt"] = str(stem) + ".srt"
    if a.txt:
        res["txt"] = str(stem) + ".txt"
    return res


def _tokens(ws):
    """[(index into ws, normalised word)] for the words that have letters or digits."""
    return [(i, _norm(w["w"])) for i, w in enumerate(ws) if _norm(w["w"])]


def _find(ws, phrase):
    """All consecutive-word matches of `phrase` as (first word index, last word index) pairs."""
    want = [n for n in (_norm(x) for x in phrase.split()) if n]
    if not want:
        raise VSError("empty phrase")
    toks = _tokens(ws)
    names = [n for _i, n in toks]
    hits = []
    for k in range(len(names) - len(want) + 1):
        if names[k:k + len(want)] == want:
            hits.append((toks[k][0], toks[k + len(want) - 1][0]))
    return hits


def _load_for(a, file_attr="file"):
    path = getattr(a, file_attr)
    if Path(path).suffix.lower() == ".json":
        return load_transcript(path), path
    return ensure_transcript(path, getattr(a, "transcript", None))


def cmd_find(a):
    t, _path = _load_for(a)
    ws = words(t)
    matches = [{"start": ws[i]["start"], "end": ws[j]["end"], "text": " ".join(w["w"] for w in ws[i:j + 1])}
               for i, j in _find(ws, a.text)]
    return {"ok": True, "matches": matches, "count": len(matches)}


# --------------------------------------------------------------------------- commands: editing

def _complement(removed, dur):
    keep, cursor = [], 0.0
    for s, e in removed:
        if s - cursor > 0.01:
            keep.append((cursor, s))
        cursor = max(cursor, e)
    if dur - cursor > 0.01:
        keep.append((cursor, dur))
    return keep


def _finish_edit(a, src, t, removed_items, keep_override=None):
    """Render the keep list and build the result. `removed_items` is [{start,end,text}] (padded ranges)."""
    dur = float(probe(str(src))["duration"])
    removed = _merge((max(0.0, r["start"]), min(dur, r["end"])) for r in removed_items)
    keep = keep_override if keep_override is not None else _complement(removed, dur)
    if not keep:
        raise VSError("this edit would remove the whole %.2f s video" % dur)
    out = quick._out_path(a, [src])
    spec = {"timeline": [quick._clip(src, **{"in": round(s, 3), "out": round(e, 3)}) for s, e in keep]}
    res = quick._spec_result(spec, out, a)
    res["removed"] = [{"start": round(r["start"], 3), "end": round(r["end"], 3), "text": r["text"]} for r in removed_items]
    res["removed_seconds"] = round(sum(e - s for s, e in removed), 3)
    tpath = out.with_name(out.stem + ".transcript.json")
    if not quick._dry(a):
        save_transcript(retime(t, removed), tpath)
    res["transcript"] = str(tpath)
    return res


def _padded(ws, i, j, pad):
    return {"start": max(0.0, ws[i]["start"] - pad), "end": ws[j]["end"] + pad,
            "text": " ".join(w["w"] for w in ws[i:j + 1])}


def cmd_cut_speech(a):
    src = quick._need_file(a.file)
    remove, keep = a.remove or [], a.keep or []
    if bool(remove) == bool(keep):
        raise VSError("cut-speech needs either --remove PHRASE (repeatable) or --keep PHRASE (repeatable), not both")
    t, _path = ensure_transcript(src, a.transcript)
    ws = words(t)
    pad = float(a.pad)
    items = []
    for phrase in remove or keep:
        hits = _find(ws, phrase)
        if not hits:
            raise VSError("phrase not found in the transcript: %r" % phrase)
        for i, j in (hits if a.all else hits[:1]):
            items.append(_padded(ws, i, j, pad))
    if remove:
        return _finish_edit(a, src, t, items)
    dur = float(probe(str(src))["duration"])
    kept = _merge((s["start"], min(dur, s["end"])) for s in items)
    gaps = [{"start": s, "end": e, "text": ""} for s, e in _complement(kept, dur)]
    res = _finish_edit(a, src, t, gaps, keep_override=kept)
    res["kept"] = [{"start": round(s, 3), "end": round(e, 3)} for s, e in kept]
    return res


def cmd_remove_fillers(a):
    src = quick._need_file(a.file)
    fillers = {_norm(x) for x in str(a.words).split(",") if _norm(x)}
    if not fillers and not a.stutters:
        raise VSError("--words is empty; list filler words such as um,uh,erm")
    t, _path = ensure_transcript(src, a.transcript)
    ws = words(t)
    pad = float(a.pad)
    items, prev = [], ""
    for i, w in enumerate(ws):
        n = _norm(w["w"])
        if n and (n in fillers or (a.stutters and n == prev)):
            items.append(_padded(ws, i, i, pad))
        if n:
            prev = n
    res = _finish_edit(a, src, t, items)
    res["removed_count"] = len(items)
    return res


def cmd_edit_text(a):
    src = quick._need_file(a.file)
    edited_path = quick._need_file(a.edited, "edited text file")
    t, _path = ensure_transcript(src, a.transcript)
    ws = words(t)
    pad = float(a.pad)
    toks = _tokens(ws)
    original = [n for _i, n in toks]
    raw = edited_path.read_text(encoding="utf-8").split()
    edited = [(x, _norm(x)) for x in raw if _norm(x)]
    matcher = difflib.SequenceMatcher(None, original, [n for _x, n in edited], autojunk=False)
    items, inserted = [], []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op in ("insert", "replace"):
            inserted += [x for x, _n in edited[j1:j2]]
        if op in ("delete", "replace"):
            items.append(_padded(ws, toks[i1][0], toks[i2 - 1][0], pad))
    if inserted:
        raise VSError("edited text contains words that are not in the transcript: %s "
                      "(edit-text can only delete words, not add or reorder them)" % ", ".join(repr(x) for x in inserted))
    return _finish_edit(a, src, t, items)


# --------------------------------------------------------------------------- registration

def register(subparsers):
    p = subparsers.add_parser("transcribe", help="speech to text with word timings (sidecar .transcript.json)")
    p.add_argument("file", metavar="FILE")
    p.add_argument("-o", "--output", help="transcript JSON path (default: <FILE stem>.transcript.json beside the media)")
    p.add_argument("--model", default="small", help="whisper model name (default small)")
    p.add_argument("--language", help="language code, e.g. en (default: detect)")
    p.add_argument("--backend", default="auto", choices=("auto",) + BACKENDS)
    p.add_argument("--force", action="store_true", help="transcribe again even if a fresh transcript exists")
    p.add_argument("--srt", action="store_true", help="also write <stem>.srt")
    p.add_argument("--txt", action="store_true", help="also write <stem>.txt")
    p.set_defaults(func=cmd_transcribe)

    p = subparsers.add_parser("find", help="find where a phrase is spoken")
    p.add_argument("file", metavar="FILE_OR_TRANSCRIPT")
    p.add_argument("--text", required=True, help="phrase to find")
    p.add_argument("--transcript", help="transcript JSON (when FILE is media)")
    p.set_defaults(func=cmd_find)

    p = subparsers.add_parser("cut-speech", help="remove (or keep only) spoken phrases")
    p.add_argument("file", metavar="FILE")
    p.add_argument("--remove", action="append", help="phrase to remove (repeatable)")
    p.add_argument("--keep", action="append", help="phrase to keep, dropping everything else (repeatable)")
    p.add_argument("--transcript", help="transcript JSON (default: sidecar, else transcribe)")
    p.add_argument("--pad", type=float, default=0.05, help="seconds of margin around each match (default 0.05)")
    p.add_argument("--all", action="store_true", help="use every match of each phrase, not just the first")
    p.add_argument("-o", "--output", help="output file (required)")
    p.set_defaults(func=cmd_cut_speech)

    p = subparsers.add_parser("remove-fillers", help="cut filler words (um, uh, ...) and optionally stutters")
    p.add_argument("file", metavar="FILE")
    p.add_argument("--words", default=DEFAULT_FILLERS, help="comma-separated filler words (default %s)" % DEFAULT_FILLERS)
    p.add_argument("--stutters", action="store_true", help="also remove an immediately repeated word")
    p.add_argument("--transcript", help="transcript JSON (default: sidecar, else transcribe)")
    p.add_argument("--pad", type=float, default=0.03, help="seconds of margin around each cut (default 0.03)")
    p.add_argument("-o", "--output", help="output file (required)")
    p.set_defaults(func=cmd_remove_fillers)

    p = subparsers.add_parser("edit-text", help="cut the video to match a transcript text with words deleted")
    p.add_argument("file", metavar="FILE")
    p.add_argument("--edited", required=True, help="text file: the transcript with words deleted")
    p.add_argument("--transcript", help="transcript JSON (default: sidecar, else transcribe)")
    p.add_argument("--pad", type=float, default=0.03, help="seconds of margin around each cut (default 0.03)")
    p.add_argument("-o", "--output", help="output file (required)")
    p.set_defaults(func=cmd_edit_text)
