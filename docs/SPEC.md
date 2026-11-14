# VideoSwami CLI contract

`python3 -m videoswami <command> ...` (also `python3 videoswami.pyz <command>` after `python3 build.py`).
Python 3.9+, **standard library only**. Needs `ffmpeg` and `ffprobe` on PATH (clear error with install hint if missing).
Works on Linux, macOS, Windows (no shell=True, pathlib, escape filter args properly, find a font per-OS).
Stdout carries machine-readable results only: every command prints ONE final JSON object on the last line
(`{"ok":true, "output":"...", ...}`), progress and logs go to stderr. Failure: exit code != 0 and
`{"ok":false,"error":"..."}` with the last ffmpeg stderr lines. Never overwrite an input file; `-o` is required
for anything that writes a video, parents are created, existing output is replaced only with `--overwrite`/`-y`.
Global flags on every command: `--dry-run` (print the ffmpeg command(s) in the JSON as `commands`, write nothing), `-y`, `--quiet`.

## Package layout (who owns what)

```
videoswami/__main__.py   argparse entry; registers commands from cli modules          (worker CORE)
videoswami/ff.py         run_ffmpeg(args, duration=None, progress=True), probe(path) -> dict, esc(), find_font(), VSError   (CORE)
videoswami/spec.py       load_spec(path|dict) -> normalised spec, validate (raises VSError naming the bad field)             (CORE)
videoswami/render.py     render_spec(spec, out, dry_run=False, overwrite=False) -> result dict                              (CORE)
videoswami/analyze.py    inspect, silences, scenes, loudness, contact sheet, frames                                         (worker TOOLS)
videoswami/quick.py      one-shot commands; each builds a spec dict and calls render.render_spec, or a direct ffmpeg call    (TOOLS)
build.py                 zipapp -> dist/videoswami.pyz                                                                       (CORE)
```
`probe(path)` returns `{path, duration, width, height, fps, rotation, has_video, has_audio, vcodec, acodec, sample_rate, channels, bitrate, size_bytes, nb_frames}`
(width/height already swapped for 90/270 rotation; fps a float).
`__main__` must import `videoswami.analyze` and `videoswami.quick` inside try/except ImportError and call their
`register(subparsers)` so CORE works before TOOLS exists. Each command function receives the argparse namespace and returns the result dict.

## Commands owned by CORE

- `probe FILE` -> the probe dict.
- `validate SPEC.json` -> `{ok, duration}` (computed output duration).
- `render SPEC.json -o OUT` -> `{ok, output, duration, width, height, fps, size_bytes}`.

### Edit spec (JSON). Paths are relative to the spec file. Times in seconds or "HH:MM:SS.mmm".

```
{ "output": { "width":1920, "height":1080, "fps":30,          // default: taken from the first video clip
              "vcodec":"h264"|"h265"|"vp9"|"prores", "crf":18, "preset":"medium",
              "acodec":"aac"|"opus", "audio_bitrate":"192k", "loudnorm":false,   // loudnorm = EBU R128 to -14 LUFS on the final mix
              "faststart":true, "pix_fmt":"yuv420p" },                            // container from the OUT extension (.mp4 .mov .mkv .webm)
  "background":"black",
  "timeline": [                                                // played one after another
    { "src":"a.mp4", "in":2, "out":8.5,                        // in/out optional
      "speed":1.0,                                             // 0.25..4 (audio follows with atempo chain)
      "volume":1.0, "mute":false, "reverse":false,
      "fit":"contain"|"cover"|"stretch"|"blur",               // how the source fills the output frame; blur = blurred copy behind a contained picture
      "crop":{"x":0,"y":0,"w":0.5,"h":1},                      // fractions of the source, applied before fit
      "rotate":0|90|180|270, "hflip":false, "vflip":false,
      "color":{"brightness":0,"contrast":1,"saturation":1,"gamma":1,"hue":0,"temperature":0},  // eq/hue; temperature -1..1 cool..warm
      "grayscale":false, "blur":0, "sharpen":0, "denoise":false, "vignette":false, "lut":"look.cube",
      "zoom":{"from":1.0,"to":1.2},                            // slow push in/out over the clip (Ken Burns), also for images
      "fade_in":0, "fade_out":0,                               // picture+sound fade from/to black
      "transition":{"type":"fade","duration":0.5} },           // transition INTO this clip from the previous one; type = any ffmpeg xfade name
                                                               // (fade, wipeleft, slideup, circleopen, dissolve, ...). Audio is acrossfaded. Shortens the total by duration.
    { "image":"title.png", "duration":3 },                     // still, same options except speed/reverse/volume
    { "color":"#101010", "duration":2 }                        // solid, silent
  ],
  "overlays": [                                                // drawn on top of the finished timeline, in order; start/end in OUTPUT time, end null = to the end
    { "type":"text", "text":"Hello", "start":1, "end":4, "x":"center", "y":"bottom", "margin":60,
      "size":64, "color":"white", "font":"/path/or/name", "bold":true, "box":false, "box_color":"black@0.5",
      "outline":2, "outline_color":"black", "shadow":true, "fade":0.3, "align":"center" },
      // x: left|center|right|<px>, y: top|center|bottom|<px>; size in px at output height 1080, scaled for other heights; multi-line text with \n
    { "type":"image", "src":"logo.png", "start":0, "end":null, "x":"right", "y":"top", "margin":40, "scale":0.15, "opacity":0.9, "fade":0 },  // scale = fraction of output width
    { "type":"video", "src":"cam.mp4", "in":0, "start":2, "end":10, "x":"right", "y":"bottom", "margin":40, "scale":0.3, "volume":0, "round":false } // picture-in-picture
  ],
  "subtitles": { "file":"subs.srt", "burn":true, "size":28, "color":"white" },   // burn=false -> soft subtitle track (mov_text / srt by container)
  "audio": [                                                   // extra tracks mixed under/over the timeline sound
    { "src":"music.mp3", "start":0, "in":0, "out":null, "volume":0.25, "fade_in":1, "fade_out":2, "loop":true, "duck":true }
      // loop: repeat to cover the video; trimmed to the video length; duck: sidechain-compress under the timeline sound
  ]
}
```
Rules: every timeline item is normalised to the same size/fps/SAR/pix_fmt and stereo 48 kHz before joining (items without audio get silence),
so mixed resolutions, frame rates, portrait/landscape and silent clips all join cleanly. Rotation metadata is respected.
A single ffmpeg invocation with `-filter_complex` (written to a temp `-filter_complex_script` file when long). Output duration must be
exact to within one frame of the computed duration. Unknown keys are an error naming the key (catch typos). Missing files are an error naming the file.

## Commands owned by TOOLS (analyze.py)

- `inspect FILE [--out DIR] [--frames 12]` -> lets an AI "watch" the video. Writes `DIR/contact.jpg` (grid of evenly spaced frames, each stamped
  with its timestamp, max 1600 px wide) and returns `{probe, contact_sheet, frame_times, scenes:[t...], silences:[{start,end}], loudness:{integrated,true_peak,lra}}`.
- `frames FILE --at 1.5,3,10 [--out DIR] [--width 960]` -> `{frames:[paths]}` exact frames as JPG.
- `silences FILE [--noise -35] [--min 0.5]` -> `{silences:[{start,end,duration}], speech:[{start,end}]}`.
- `scenes FILE [--threshold 0.3]` -> `{scenes:[t...]}` cut points.
- `loudness FILE` -> `{integrated, true_peak, lra}`.

## Commands owned by TOOLS (quick.py). All take `FILE... -o OUT` and return `{ok, output, duration, ...}`

```
trim FILE --start T --end T [--fast]            keep one range (--fast = stream copy, keyframe-accurate only)
cut FILE --remove 5-9,20-22.5                   delete ranges, join the rest
keep FILE --ranges 0-5,12-20                    keep only these ranges, joined
concat A B C ... [--transition fade --duration 0.5]
speed FILE --factor 2
reverse FILE
resize FILE [--width W --height H | --aspect 9:16|1:1|16:9|4:5] [--fit cover|contain|blur]   (aspect keeps the short side)
crop FILE --x --y --w --h                       fractions
rotate FILE --deg 90|180|270  | --hflip | --vflip
color FILE [--brightness --contrast --saturation --gamma --temperature] [--grayscale] [--lut F]
text FILE --text "..." [--start --end --x --y --size --color --box]
watermark FILE --image logo.png [--x --y --scale --opacity --margin]   (margin default 40 px at 1080p, scaled)
pip FILE --overlay cam.mp4 [--x --y --scale --start --end]
music FILE --audio m.mp3 [--volume 0.25] [--duck] [--replace]     (--replace drops the original sound)
mute FILE          volume FILE --gain 1.5          normalize FILE       (loudness to -14 LUFS)
fade FILE [--in 1 --out 1]
subtitles FILE --srt subs.srt [--soft]
remove-silence FILE [--noise -35 --min 0.6 --pad 0.1]    jump-cut the silent parts; returns removed seconds
stabilize FILE                                  (vidstab two-pass; clear error if the filter is missing)
denoise FILE
compress FILE --target-mb 25 | --crf 28 [--height 720]   (target = two-pass bitrate so the file lands under the size)
gif FILE [--start --end --width 480 --fps 12]            (palettegen/paletteuse), -o x.gif
extract-audio FILE -o x.mp3|wav|m4a
thumbnail FILE [--at T] -o x.jpg                         (default: a frame 1/3 in)
convert FILE -o x.webm|mov|mkv|mp4
slideshow IMG... [--each 3 --transition fade --duration 0.5 --zoom] [--audio m.mp3]
split FILE --every 60 | --at 10,25 -o DIR                 writes part_001.mp4 ...; returns {outputs:[...]}
```

---

# Version 2 additions

New modules, each with `register(subparsers)` (already wired in `__main__`), same JSON/flags rules as above.
Still **standard library only at import time**. Optional accelerators (`numpy`, `cv2`, whisper backends) are imported lazily inside
functions with a pure-Python/ffmpeg fallback or a clear VSError that says what to install.

## Transcript format (shared by speech.py and captions)

```
{ "language":"en", "duration":12.3, "source":"talk.mp4",
  "segments":[ { "start":0.2, "end":1.3, "text":"Hello everyone",
                 "words":[ {"w":"Hello","start":0.2,"end":0.6}, {"w":"everyone","start":0.7,"end":1.3} ] } ] }
```
`w` has no leading/trailing spaces and keeps its punctuation. Matching text is case-insensitive and ignores punctuation.

## speech.py (worker SPEECH)

Python API used by other modules:
`load_transcript(path) -> dict` (validates, VSError on bad shape), `save_transcript(t, path)`,
`ensure_transcript(media_path, transcript_path=None, model=None, language=None) -> (dict, path)` (loads the given/sidecar transcript or transcribes),
`words(t) -> [ {w,start,end} ]` flat, `retime(t, removed_ranges) -> dict` (drops words inside removed ranges, shifts the rest),
`to_srt(t) -> str`.

- `transcribe FILE [-o T.json] [--model small] [--language xx] [--backend auto|faster-whisper|whisper|whisper-cpp|cmd] [--force] [--srt] [--txt]`
  -> `{transcript, text, word_count, language, backend, cached}`. Default output is the sidecar `<FILE stem>.transcript.json` beside the media;
  an existing sidecar newer than the media is reused (`cached:true`) unless `--force`. `--srt`/`--txt` also write `<stem>.srt` / `<stem>.txt`.
  Backends, tried in this order for `auto`:
  1. `cmd`: env `VIDEOSWAMI_WHISPER_CMD`, a command template with `{audio}` (16 kHz mono wav we extract) and `{out}` (path where it must write
     JSON in openai-whisper format: `{"text","language","segments":[{"start","end","text","words":[{"word":" Hello","start","end"}]}]}`). Split with shlex, no shell.
  2. `faster-whisper` python module (word_timestamps=True, vad_filter=True).
  3. `whisper` python module / `whisper` CLI (openai-whisper, `--word_timestamps True --output_format json`).
  4. `whisper-cpp`: `whisper-cli` or `main` binary with `-oj -ml 1` and model from env `VIDEOSWAMI_WHISPER_CPP_MODEL`.
  Segments without word timings get words spread evenly across the segment. No backend -> VSError listing the three install options
  (`pip install faster-whisper` first).
- `find FILE_OR_TRANSCRIPT --text "phrase"` -> `{matches:[{start,end,text}]}` (consecutive-word match).
- `cut-speech FILE (--remove "phrase")... | (--keep "phrase")... [--transcript T] [--pad 0.05] [--all] -o OUT`
  removes (or keeps only) the spoken phrases, word-accurate: a match removes `[first.start - pad, last.end + pad]`. Without `--all` only the first
  match of each phrase is used. A phrase that is not found is an error naming it. -> `{..., removed:[{start,end,text}], removed_seconds, transcript}`
  where `transcript` is a retimed transcript written beside OUT (`<OUT stem>.transcript.json`).
- `remove-fillers FILE [--words um,uh,erm,ah,hmm] [--stutters] [--transcript T] [--pad 0.03] -o OUT` -> `{..., removed_count, removed_seconds, transcript}`.
  `--stutters` also removes an immediately repeated word ("the the").
- `edit-text FILE --edited edited.txt [--transcript T] [--pad 0.03] -o OUT`: text-based editing. `edited.txt` is the transcript text with words/sentences
  deleted (order unchanged). Align with difflib on normalised words; each deleted run removes `[first.start - pad, last.end + pad]`.
  Inserted words that are not in the transcript are an error naming them. -> `{..., removed:[...], removed_seconds, transcript}`.
All three editing commands build a `keep` range list and render through `render_spec` (same as quick `keep`).

## smart.py (worker SMART)

Analysis reads small raw frames / PCM from ffmpeg pipes (e.g. 96 px wide gray at 5 fps; 8 kHz mono s16) and computes in pure Python,
using numpy/cv2 only if importable. Must run in reasonable time on a 10 minute video (sample, do not decode full-res).

- `reframe FILE --aspect 9:16 [--track auto|face|motion|center] [--smooth 1.0] [--height H] -o OUT` -> crops to the aspect following the subject.
  `face` needs cv2 (haar cascade); `motion` = centroid of frame-difference energy (falls back to brightness/saliency centroid when nothing moves);
  `auto` = face if cv2 finds faces in most samples, else motion. Path is smoothed (no jitter, limited speed) and applied as a time-varying crop x/y
  expression. -> `{..., track:"motion", path:[{t,x,y}]}` (x,y = crop centre as fraction of the source).
- `highlights FILE --duration 30 [--min-clip 2] [--max-clip 8] [--transition 0.25] -o OUT` -> best moments, in chronological order, short fades between.
  Score per second = loudness (RMS) + motion + scene-change bonus; pick non-overlapping windows greedily until the duration is filled.
  -> `{..., segments:[{start,end,score}]}`; output duration within 15% of the request (or the whole file if shorter).
- `beats FILE` -> `{bpm, beats:[t...]}` (onset envelope -> autocorrelation tempo -> beat grid snapped to onsets).
- `beat-sync CLIP... --audio MUSIC [--every 2] [--duration D] [--fit cover] -o OUT` -> cuts between clips exactly on every Nth beat, cycling through the
  clips (each use continues where that clip left off, looping if needed), music as the only sound. Duration defaults to the music length.
  -> `{..., bpm, cuts:[t...]}`.
- `enhance FILE [--no-video] [--no-audio] -o OUT` -> auto picture (levels from sampled luma percentiles -> contrast/brightness/gamma, mild saturation,
  white balance from average chroma) and voice cleanup (highpass 80 Hz, afftdn, gentle compressor, loudnorm -14 LUFS). -> `{..., video:{...applied}, audio:{...applied}}`.

## finish.py + captions.py + templates.py, and CORE changes in spec.py/render.py (worker FINISH)

- `captions FILE [--transcript T] [--style pop|highlight|karaoke|box|classic|minimal] [--position bottom|center|top] [--max-words 4]
  [--size 80] [--color white] [--accent "#FFE600"] [--font F] [--uppercase] [--ass-only] -o OUT`
  Animated word-by-word captions generated as an ASS file and burned in (`ass` filter). Words are grouped into short lines (max-words, break on
  gaps > 0.6 s and sentence ends); a group is on screen from its first word's start to its last word's end (+ up to 0.3 s, never into the next group).
  Styles: `classic` whole line static; `minimal` small lower line; `box` line on a rounded dark box; `highlight` whole line visible, the spoken word in
  the accent colour; `karaoke` words fill with the accent as spoken (\kf); `pop` only the current word(s) appear with a quick scale-in (\t \fscx\fscy).
  size is px at 1080 high. `--ass-only` writes just the .ass to OUT. Without `--transcript` call `speech.ensure_transcript`.
  -> `{..., style, groups, ass}`.
- spec top level: `"captions": { "transcript":"t.json", "style":"pop", ...same option names with underscores... }` (times are OUTPUT time).
- `templates` -> `{templates:[{name,description,fields}]}`; `title FILE --template NAME --text "..." [--sub "..."] [--start 0 --end 4] [--accent "#FFE600"] [--color white] -o OUT`.
  Templates (ASS drawings + animation tags, no external assets): `lower-third` (accent bar + name + subtitle sliding in from the left, bottom-left),
  `title-card` (big centred title scaling/fading in with a sub line), `corner-tag` (small label top-left), `end-card` (centred text on a darkened picture),
  `chapter` (number/label wipe across the top). In a spec: overlay `{ "type":"title", "template":"lower-third", "text":"...", "sub":"...", "start":1, "end":5, "accent":"#FFE600" }`.
- Overlay animation for text/image/video overlays: `"animate": { "in":"fade|slide-up|slide-down|slide-left|slide-right|pop", "out": same, "duration":0.4 }`.
- Keyframed camera moves on any timeline item: `"keyframes":[ {"t":0,"zoom":1,"x":0.5,"y":0.5}, {"t":2,"zoom":2,"x":0.25,"y":0.5,"ease":"linear|in|out|inout"} ]`
  t = seconds from the start of the item (after speed), zoom >= 1, x,y = focus point as fraction of the fitted frame (kept inside the picture).
  Values hold before the first and after the last keyframe. Cannot be combined with `zoom` (error).
- Export profiles: `output.profile` = `reels|shorts|tiktok` (1080x1920 30 fps, loudnorm), `youtube` (1920x1080), `youtube-4k` (3840x2160),
  `square` (1080x1080), `portrait` (1080x1350), `whatsapp` (1280x720 max, crf 28, 128k audio), `x` (1280x720). Explicit output keys override the profile.
  Commands: `profiles` -> `{profiles:{name:{...}}}`; `export FILE --profile reels [--fit blur|cover|contain] -o OUT`.

---

# Version 3 additions

## reference.py (worker REFERENCE): match the look of a reference video

"Visual reference": the user points at a video (or image) they like; VideoSwami measures its look and applies it to their footage.
Measurement samples small frames through ffmpeg pipes (same approach as smart.py); numpy optional with a pure-Python fallback
(honour `VIDEOSWAMI_NO_NUMPY=1`).

- `reference REF [--out DIR] [--frames 12]` -> the style profile, also written to `DIR/style.json` (default DIR: `<REF stem>.style/` beside REF), plus
  `DIR/contact.jpg` (same contact sheet as `inspect`) so the AI can also LOOK at the reference:
  ```
  { "ok":true, "style":"…/style.json", "contact_sheet":"…/contact.jpg",
    "profile": {
      "source":"ref.mp4", "kind":"video"|"image",
      "frame":  { "width":1080, "height":1920, "aspect":"9:16", "fps":30 },          // aspect reduced to the nearest of 9:16 4:5 1:1 4:3 16:9 21:9, else "W:H"
      "color":  { "luma_mean":0..255, "luma_p5":…, "luma_p95":…, "contrast":p95-p5, "saturation":0..1 (mean HSV S),
                  "temperature":-1..1 ((R-B)/(R+B) of the mean colour, + = warm), "mean_rgb":[r,g,b] },
      "pacing": { "cuts":n, "avg_shot":seconds, "median_shot":seconds, "shots_per_minute":x },   // video only; uses the same cut detector as `scenes`
      "audio":  { "integrated":LUFS|null, "true_peak":…, "lra":… },                 // null when the reference has no sound
      "captions": { "likely":bool, "position":"bottom"|"center"|"top"|null }       // heuristic: a band with persistent high-contrast small detail; best effort
    } }
  ```
- `match FILE --reference REF_OR_STYLE_JSON [--only color,loudness,frame] [--strength 1.0] [--fit cover|blur|contain] -o OUT`
  Applies what can be transferred, by default all three:
  - `color`: brightness/contrast/gamma/saturation/temperature computed from the two profiles so FILE's stats land on the reference's
    (iterate once: measure the result on sampled frames and correct, so the match is close, not just directionally right). `--strength` 0..1 blends.
  - `loudness`: loudnorm to the reference's integrated loudness (skipped when either has no audio).
  - `frame`: output aspect and fps of the reference (size = reference size capped at 1920 on the long side), using `--fit` (default cover).
  -> `{..., applied:{color:{...}|null, loudness:{target}|null, frame:{width,height,fps}|null}, before:{color}, after:{color}, reference:{color},
       pacing_hint:"reference averages 1.2 s per shot (50 cuts/min); this clip has 0 cuts"}`. Pacing is reported, never auto-applied.
- Edit spec: top-level `"look": { "reference":"ref.mp4"|"style.json", "strength":1.0 }` applies the same colour match to the whole finished
  timeline (before overlays and captions). Implemented in spec.py/render.py by calling into reference.py.
Python API: `profile(path) -> dict`, `color_filters(src_color, ref_color, strength) -> str` (an ffmpeg filter chain).

## Visual guide (worker GALLERY)

`python3 tools/make_gallery.py [--out docs/gallery]` renders a small picture of every look VideoSwami offers, using the CLI itself on a
generated demo clip, and writes `docs/VISUAL-GUIDE.md` showing each picture with the exact command or spec snippet that produces it.
Sections and files (all JPG, 640 px wide unless vertical; vertical ones 360 px wide):
`captions-<style>.jpg` x6, `title-<template>.jpg` x5, `fit-<contain|cover|stretch|blur>.jpg` x4 (16:9 source into 9:16),
`transition-<fade|wipeleft|slideup|circleopen|dissolve>.jpg` x5 (frame at the middle of the transition), `animate-<fade|slide-up|pop>.jpg` x3 (mid-animation),
`look-<grayscale|vignette|warm|cool|blur|sharpen>.jpg` x6, `profile-<name>.jpg` x9 (one frame per export profile, showing its shape),
plus `contact.jpg` (one sheet of everything). The demo clip must look like footage, not a test card: a soft two-colour gradient background with a
simple drawn presenter shape and a few moving elements, generated with ffmpeg lavfi only (no downloads), with a transcript fixture for captions.
