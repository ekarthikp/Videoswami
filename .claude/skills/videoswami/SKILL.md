---
name: videoswami
description: Edit a video the user gives you from a plain-language request (cut by what is said, captions, reframe to 9:16, highlights, titles, music, compress) using the VideoSwami ffmpeg CLI.
---

# VideoSwami

The user hands you one or more videos and says what they want. You watch the
footage, read what is said, plan the edit, render it with the `videoswami` CLI,
check the result with your own eyes, and hand back the file. Do the edit; do
not explain ffmpeg.

## 0. Find the tool

Look, in order: `dist/videoswami.pyz` in this repo, files attached to the
conversation, the current folder, `~/.videoswami/videoswami.pyz`,
`~/.claude/skills/videoswami/videoswami.pyz`. Run it as
`python3 /path/videoswami.pyz <command> ...` (written `vs` below). Inside this
repo `python3 -m videoswami <command>` also works.

It needs Python 3.9+ and `ffmpeg`/`ffprobe` on PATH. If ffmpeg is missing, say
so and give the install line (`brew install ffmpeg`, `sudo apt install ffmpeg`,
`winget install ffmpeg`).

Every command prints one JSON object on the last stdout line (`ok`, `output`,
`duration`, or `error`). Add `--quiet`. `-o OUT` is required, `-y` overwrites,
`--dry-run` shows the ffmpeg commands. Inputs are never modified.
`vs <command> -h` lists exact flags. The full command list and the edit-spec
format are in `docs/SPEC.md`: read it before writing a spec.

## Sub-skill: ClipSwami

When the request is "turn this long video (or link) into several short clips",
hand over to the `clipswami` skill (`python3 -m clipswami ...`). It finds the
moments, reframes, captions and renders them. Use `clipswami-cut` for one clip
from exact timestamps and `clipswami-setup` for first-time install. Everything
else stays here.

## 1. Watch and listen before cutting

```
vs inspect VIDEO --out work/inspect --frames 12
```

Read the JSON (size, fps, duration, `scenes`, `silences`, `loudness`) and open
`contact_sheet` with your image reader. Closer look:
`vs frames VIDEO --at 12.5,40,61 --out work/frames`.

If the request touches what is said (captions, "cut the part about X",
tighten a talk):

```
vs transcribe VIDEO --txt     # writes <stem>.transcript.json and .txt, reused next time
```

Read the `.txt`. If it fails with "no backend", tell the user the one-time fix
`pip install faster-whisper`, or ask for an .srt / timestamps. Never guess
timestamps you have not seen or read.

## 2. Pick the smallest tool that does the job

One change: a one-shot command. Several changes: ONE edit spec, rendered once.

- Cut and join: `trim` `cut` `keep` `concat` `split` `speed` `reverse` `remove-silence`
- Speech: `find` `cut-speech --remove/--keep "phrase"` `remove-fillers` `edit-text --edited edited.txt`
- Framing and look: `resize` `crop` `rotate` `color` `stabilize` `denoise` `reframe --aspect 9:16` `enhance`
- Additions: `text` `watermark` `pip` `music` `subtitles` `fade`
  `captions --style pop|highlight|karaoke|box|classic|minimal`
  `title --template lower-third|title-card|corner-tag|end-card|chapter`
- Smart: `highlights --duration 30` `beat-sync CLIPS... --audio MUSIC` `slideshow`
- Output: `export --profile reels|shorts|tiktok|youtube|youtube-4k|square|portrait|whatsapp|x`
  `compress --target-mb N` `convert` `gif` `thumbnail` `extract-audio`

Text-based editing is the strongest tool for talking videos: copy the `.txt`
transcript to `edited.txt`, delete the words, sentences and retakes you want
gone (do not add or reorder words), then run `edit-text`.

The speech commands write a retimed `<OUT stem>.transcript.json`, so captions
added afterwards still line up. Captions and overlays in a spec use OUTPUT
time: cut first, caption the result.

## 3. Edit spec for anything bigger

Write `edit.json` next to the media, `vs validate edit.json`, then
`vs render edit.json -o out.mp4`. Top-level keys: `output` (or
`output.profile`), `timeline` (clips, images, colours, each with trim, speed,
fit, colour, `transition`, `keyframes`), `overlays` (text, image, video,
title; optional `animate`), `captions`, `subtitles`, `audio`. Unknown keys and
missing files are rejected by name. See `docs/SPEC.md` for every field.

## 4. Check the result, then deliver

After every render: confirm `ok` and the `duration` you planned, then
`vs frames OUT --at <start, each cut/overlay/caption, near the end>` and look.
Rerender if text is cut off, captions cover a face, or framing lost the
subject. Then give the user the file with one line on what changed and the
new length.

## Recipes

- Talking head to Reel: `transcribe --txt` -> `edit-text` -> `remove-fillers`
  -> `reframe --aspect 9:16` -> `captions --style highlight` ->
  `export --profile reels`.
- "Remove where I talk about X": `find`, then `cut-speech --remove "..."`.
- Long video to teaser: `highlights --duration 30`, or pick lines from the
  transcript and `cut-speech --keep`.
- Montage to music: `beat-sync clips... --audio song.mp3 --every 2`.
- Flat picture or quiet voice: `enhance`.
- Sharing limits: `compress --target-mb N`.

## Rules of thumb

- Work in `work/`; name outputs clearly next to the source; never touch the original.
- `reframe --track auto` uses faces when OpenCV is installed, else motion.
  Look at frames; fall back to `--track center` or `crop` if it follows the wrong thing.
- Long sources: test the plan on a short `trim` first.
- If a request is ambiguous in a way that changes the cut, ask one short
  question; otherwise make the call and say what you chose.
