"""Command line for Clipswami: python3 -m clipswami <command> ..."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__, fetch, ff, picker, render, signals
from . import transcript as tr


def _workdir(video: Path) -> Path:
    return Path.cwd() / ".clipswami" / ff.slugify(video.stem, "video")


def _outdir(video: Path, args) -> Path:
    return Path(args.out) if getattr(args, "out", None) else Path.cwd() / "clips" / ff.slugify(video.stem, "video")


def _video(args) -> Path:
    if fetch.is_url(args.video):                             # a link: download it first
        video = fetch.download(args.video, height=getattr(args, "height", 1080),
                               section=getattr(args, "section", None))
        args.video = str(video)
        return video
    video = Path(args.video).expanduser()
    if not video.is_file():
        raise ff.ClipError(f"no such file: {video}")
    return video


def _load(args, need_transcript: bool = True):
    video = _video(args)
    work = _workdir(video)
    analysis = signals.analyze(video, work, force=getattr(args, "force", False))
    cues, source = [], "none"
    if need_transcript:
        cues, source = tr.load(video, analysis["info"], work,
                               explicit=getattr(args, "transcript", None),
                               transcribe=getattr(args, "transcribe", "auto"),
                               model=getattr(args, "whisper_model", "base"),
                               language=getattr(args, "language", None))
    return video, work, analysis, cues, source


def _options(args) -> render.Options:
    return render.Options.from_dict({
        "aspect": args.aspect, "fit": args.fit, "res": args.res, "captions": args.captions,
        "tighten": not args.no_tighten, "loudnorm": not args.no_loudnorm,
        "title_secs": 0.0 if args.no_title else None, "font": args.font,
        "progress": True if args.progress else None,
        "crf": args.crf, "preset": args.preset,
    })


def _render_all(analysis, cues, clips, opts, outdir: Path, jobs: int) -> list[dict]:
    outdir.mkdir(parents=True, exist_ok=True)
    work = []
    for index, clip in enumerate(clips, 1):
        name = ff.slugify(clip.get("slug") or clip.get("title") or "", f"clip-{index:02d}")
        work.append((clip, opts.merged(clip), outdir / f"{index:02d}-{name}.mp4"))

    def one(job):
        clip, clip_opts, path = job
        try:
            result = render.render_clip(analysis, cues, clip, clip_opts, path)
            print(f"  rendered {path.name}  {result['duration']:.1f}s  {result['size']}"
                  + (f"  (-{result['trimmed_pause']:.1f}s dead air)" if result["trimmed_pause"] else ""),
                  flush=True)
            return {**result, **{k: clip[k] for k in ("score", "why") if k in clip}}
        except ff.ClipError as exc:
            print(f"  FAILED {path.name}: {exc}", file=sys.stderr, flush=True)
            return {"file": str(path), "error": str(exc)}

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = list(pool.map(one, work))
    manifest = outdir / "manifest.json"
    manifest.write_text(json.dumps({"source": analysis["info"]["path"], "clips": results}, indent=2))
    done = sum(1 for r in results if "error" not in r)
    print(f"{done}/{len(results)} clips -> {outdir}   manifest: {manifest}")
    return results


def _print_candidates(candidates: list[dict]) -> None:
    for c in candidates:
        print(f"  #{c['rank']:<2} {ff.fmt_time(c['start']):>9} -> {ff.fmt_time(c['end']):<9} "
              f"{c['duration']:5.1f}s  score {c['score']:5.1f}  {', '.join(c['why'])}")
        if c.get("transcript"):
            text = c["transcript"]
            print(f"      \"{text[:110]}{'...' if len(text) > 110 else ''}\"")


def _find(args):
    video, work, analysis, cues, source = _load(args)
    info = analysis["info"]
    print(f"{video.name}: {ff.fmt_time(info['duration'])}  {info['width']}x{info['height']}  "
          f"{len(analysis['scenes'])} cuts  {len(analysis['silences'])} pauses  transcript: {source}")
    candidates = picker.find_candidates(analysis, cues, count=args.count, min_len=args.min,
                                        max_len=args.max, target=args.target)
    if not candidates:
        raise ff.ClipError("no candidate windows found - try a smaller --min")
    return video, work, analysis, cues, source, candidates


# ---------------------------------------------------------------- commands

def cmd_doctor(args) -> int:
    ok = True
    for binary in ("ffmpeg", "ffprobe"):
        path = shutil.which(binary)
        print(f"{'ok ' if path else 'MISSING'}  {binary}  {path or ''}")
        ok = ok and bool(path)
    if ok:
        filters = ff.run([ff.need("ffmpeg"), "-hide_banner", "-filters"]).stdout
        encoders = ff.run([ff.need("ffmpeg"), "-hide_banner", "-encoders"]).stdout
        for label, present in (("libass (captions)", " ass " in filters),
                               ("libx264 encoder", "libx264" in encoders),
                               ("loudnorm filter", " loudnorm " in filters)):
            print(f"{'ok ' if present else 'MISSING'}  {label}")
            ok = ok and present
    print(f"{'ok ' if fetch.downloader() else 'none'}  yt-dlp (optional, only needed for links: "
          f"{fetch.INSTALL_HINT})")
    whisper = tr.whisper_command()
    print(f"{'ok ' if whisper else 'none'}  whisper (optional, makes captions when a video has no "
          f"subtitles: {whisper or tr.WHISPER_INSTALL})")
    print(f"clipswami {__version__}: {'ready' if ok else 'not ready'}")
    return 0 if ok else 1


def cmd_fetch(args) -> int:
    print(_video(args))
    return 0


def cmd_probe(args) -> int:
    print(json.dumps(ff.probe(_video(args)), indent=2))
    return 0


def cmd_analyze(args) -> int:
    video, work, analysis, cues, source = _load(args)
    info = analysis["info"]
    print(json.dumps({
        "video": info["path"], "duration": round(info["duration"], 2),
        "size": f"{info['width']}x{info['height']}", "fps": round(info["fps"], 2),
        "has_audio": info["has_audio"], "scene_cuts": len(analysis["scenes"]),
        "pauses": len(analysis["silences"]), "transcript": source,
        "transcript_cues": len(cues), "workdir": str(work),
    }, indent=2))
    return 0


def cmd_candidates(args) -> int:
    video, work, analysis, cues, source, candidates = _find(args)
    if args.sheets:
        for c in candidates:
            sheet = work / "sheets" / f"candidate-{c['rank']:02d}.jpg"
            try:
                render.contact_sheet(analysis["info"]["path"], c["start"], c["end"], sheet)
                c["sheet"] = str(sheet)
            except ff.ClipError:
                pass
    out = work / "candidates.json"
    out.write_text(json.dumps({"video": analysis["info"]["path"], "transcript": source,
                               "candidates": candidates}, indent=2))
    _print_candidates(candidates)
    print(f"candidates: {out}" + (f"   sheets: {work / 'sheets'}" if args.sheets else ""))
    return 0


def cmd_auto(args) -> int:
    video, work, analysis, cues, source, candidates = _find(args)
    _print_candidates(candidates)
    _render_all(analysis, cues, candidates, _options(args), _outdir(video, args), args.jobs)
    return 0


def cmd_render(args) -> int:
    plan = json.loads(Path(args.plan).read_text())
    clips = plan["clips"] if isinstance(plan, dict) else plan
    if not clips:
        raise ff.ClipError("the plan has no clips")
    if not args.video:
        if not isinstance(plan, dict) or not plan.get("video"):
            raise ff.ClipError("give a video path, or put \"video\" in the plan")
        args.video = plan["video"]
    video, work, analysis, cues, source = _load(args)
    opts = _options(args)
    if isinstance(plan, dict) and plan.get("defaults"):
        opts = opts.merged(plan["defaults"])
    for clip in clips:
        clip["start"], clip["end"] = ff.parse_time(clip["start"]), ff.parse_time(clip["end"])
        if clip.get("snap", not args.no_snap):
            clip["start"], clip["end"] = picker.snap(analysis, cues, clip["start"], clip["end"])
    _render_all(analysis, cues, clips, opts, _outdir(video, args), args.jobs)
    return 0


def cmd_cut(args) -> int:
    video, work, analysis, cues, source = _load(args)
    clip = {"start": ff.parse_time(args.start), "end": ff.parse_time(args.end), "title": args.title or ""}
    if clip["end"] <= clip["start"]:
        raise ff.ClipError("end must be after start")
    if args.snap:
        clip["start"], clip["end"] = picker.snap(analysis, cues, clip["start"], clip["end"])
    _render_all(analysis, cues, [clip], _options(args), _outdir(video, args), 1)
    return 0


def cmd_transcript(args) -> int:
    video, work, analysis, cues, source = _load(args)
    if not cues:
        print("no transcript available (no sidecar .srt/.vtt, no subtitle track, no local whisper)")
        return 0
    shifts = picker.topic_shifts(cues)
    lines = []
    for cue, shift in zip(cues, shifts):
        if shift >= 0.5 and lines:
            lines.append("")                                 # blank line = topic change
        lines.append(f"[{ff.fmt_time(cue['start'])} - {ff.fmt_time(cue['end'])}] {cue['text']}")
    out = work / "transcript.txt"
    out.write_text("\n".join(lines) + "\n")
    print(f"{len(cues)} cues from {source} -> {out}  (blank lines mark topic changes)")
    return 0


def cmd_transcribe(args) -> int:
    video = _video(args)
    work = _workdir(video)
    info = ff.probe(video)
    if not info["has_audio"]:
        raise ff.ClipError(f"{video.name} has no audio to transcribe")
    sidecar = video.with_suffix(".srt")
    if sidecar.is_file() and not args.force:
        print(f"transcript already exists: {sidecar}  (use --force to redo it)")
        return 0
    print(f"transcribing {video.name} with whisper ({args.whisper_model}) ...", flush=True)
    cues = tr.run_whisper(video, work, args.whisper_model, args.language, strict=True)
    if not cues:
        raise ff.ClipError("whisper heard no speech in this video")
    tr.write_srt(cues, sidecar)
    (work / "transcript.json").unlink(missing_ok=True)       # drop the stale cache
    print(f"{len(cues)} cues -> {sidecar}")
    return 0


def _pip_install(package: str) -> bool:
    base = [sys.executable, "-m", "pip", "install", "--user", "-U", package]
    for extra in ([], ["--break-system-packages"]):          # second try: distro-managed Python
        if subprocess.run(base + extra, capture_output=True, text=True).returncode == 0:
            return True
    return False


def cmd_setup(args) -> int:
    """Install the optional Python-side helpers; report what still needs a system install."""
    wanted = [("yt-dlp", fetch.downloader, "yt-dlp")]
    if not args.no_whisper:
        wanted.append(("whisper", tr.whisper_command, "whisper-ctranslate2"))
    for label, check, package in wanted:
        if check() and not args.upgrade:
            print(f"ok   {label} already installed")
            continue
        print(f"...  installing {package}", flush=True)
        done = _pip_install(package)
        print(f"{'ok ' if done and check() else 'FAILED'}  {label}"
              + ("" if done and check() else
                 f"  (installed but not on PATH - add ~/.local/bin to PATH)" if done else ""))
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("MISSING  ffmpeg - install it with your system package manager:\n"
              "         macOS:   brew install ffmpeg\n"
              "         Ubuntu:  sudo apt-get install -y ffmpeg\n"
              "         Windows: winget install Gyan.FFmpeg")
    return cmd_doctor(args)


def cmd_review(args) -> int:
    folder = Path(args.folder)
    clips = sorted(folder.glob("*.mp4"))
    if not clips:
        raise ff.ClipError(f"no rendered clips in {folder}")
    for clip in clips:
        info = ff.probe(clip)
        sheet = folder / "review" / (clip.stem + ".jpg")
        render.contact_sheet(str(clip), 0.0, info["duration"], sheet, frames=10, cell=216)
        print(f"  {clip.name}  {info['duration']:.1f}s  {info['width']}x{info['height']}  -> {sheet}")
    return 0


# ---------------------------------------------------------------- parser

def _add_source(p, optional: bool = False) -> None:
    p.add_argument("video", nargs="?" if optional else None,
                   help="path to the source video, or a link (YouTube etc.)")
    p.add_argument("--height", type=int, default=1080, help="max height when downloading a link")
    p.add_argument("--section", help="when downloading a link, only fetch this span, e.g. 10:00-25:00")
    p.add_argument("--transcript", help="SRT/VTT file to use (default: auto-detect)")
    p.add_argument("--transcribe", choices=("auto", "off"), default="auto",
                   help="use a local whisper install when no subtitles exist (default: auto)")
    p.add_argument("--whisper-model", default="base",
                   help="whisper model: tiny, base, small, medium, large-v3 (default base)")
    p.add_argument("--language", help="spoken language code for whisper, e.g. en (default: detect)")
    p.add_argument("--force", action="store_true", help="ignore cached analysis")


def _add_pick(p) -> None:
    p.add_argument("-n", "--count", type=int, default=5, help="how many clips (default 5)")
    p.add_argument("--min", type=float, default=15.0, help="shortest clip in seconds")
    p.add_argument("--max", type=float, default=60.0, help="longest clip in seconds")
    p.add_argument("--target", type=float, help="preferred clip length in seconds")


def _add_render(p) -> None:
    p.add_argument("--aspect", choices=(*render.ASPECTS, "source"), default="9:16")
    p.add_argument("--fit", choices=("track", "center", "blur"), default="track",
                   help="track = follow the action; blur = whole frame over a blurred fill")
    p.add_argument("--res", type=int, default=1080, help="short side in pixels (default 1080)")
    p.add_argument("--captions", choices=("bold", "clean", "off"), default="bold")
    p.add_argument("--no-tighten", action="store_true", help="keep pauses instead of jump-cutting them")
    p.add_argument("--no-loudnorm", action="store_true", help="leave audio levels untouched")
    p.add_argument("--no-title", action="store_true", help="do not show the title card")
    p.add_argument("--progress", action="store_true", help="add a progress bar along the bottom")
    p.add_argument("--font", default="DejaVu Sans")
    p.add_argument("--crf", type=int, default=20, help="x264 quality, lower is better (default 20)")
    p.add_argument("--preset", default="medium", help="x264 speed preset")
    p.add_argument("--out", help="output folder (default clips/<video-name>/)")
    p.add_argument("--jobs", type=int, default=2, help="clips rendered in parallel")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clipswami", description="In-house video clipping engine.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check that ffmpeg is usable").set_defaults(func=cmd_doctor)

    p = sub.add_parser("fetch", help="download a video (and its subtitles) from a link")
    p.add_argument("video", help="link to the video")
    p.add_argument("--height", type=int, default=1080, help="max height (default 1080)")
    p.add_argument("--section", help="only fetch this span, e.g. 10:00-25:00")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("probe", help="print facts about a video")
    p.add_argument("video")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("analyze", help="analyse loudness, pauses, cuts and transcript")
    _add_source(p)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("transcript", help="write a timestamped, topic-segmented transcript")
    _add_source(p)
    p.set_defaults(func=cmd_transcript)

    p = sub.add_parser("transcribe", help="make subtitles with a local whisper and save them next to the video")
    _add_source(p)
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("setup", help="install the optional helpers (yt-dlp, whisper)")
    p.add_argument("--no-whisper", action="store_true", help="skip the speech-to-text install")
    p.add_argument("--upgrade", action="store_true", help="upgrade helpers that are already installed")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("review", help="contact sheets of rendered clips, for a quality check")
    p.add_argument("folder", help="folder of rendered clips, e.g. clips/<video-name>")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("candidates", help="rank the best moments without rendering")
    _add_source(p)
    _add_pick(p)
    p.add_argument("--sheets", action="store_true", help="write a contact sheet per candidate")
    p.set_defaults(func=cmd_candidates, count=10)

    p = sub.add_parser("auto", help="find the best moments and render them")
    _add_source(p)
    _add_pick(p)
    _add_render(p)
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("render", help="render the clips listed in a plan JSON")
    _add_source(p, optional=True)
    p.add_argument("--plan", required=True, help="plan file: {video, defaults, clips:[{start,end,title}]}")
    p.add_argument("--no-snap", action="store_true",
                   help="use the plan's timestamps exactly instead of snapping to clean edit points")
    _add_render(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("cut", help="render one clip from explicit timestamps")
    _add_source(p)
    p.add_argument("start", help="e.g. 1:23, 83.5 or 1m23s")
    p.add_argument("end")
    p.add_argument("--title", help="title card text")
    p.add_argument("--snap", action="store_true", help="nudge the cut onto the nearest pause")
    _add_render(p)
    p.set_defaults(func=cmd_cut)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ff.ClipError as exc:
        print(f"clipswami: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
