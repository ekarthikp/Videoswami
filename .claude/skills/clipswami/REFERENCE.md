# Clipswami

An in-house video clipping engine. You (Claude Code) are the editor; `clipswami/` is the
machine room. Everything is Python standard library + ffmpeg. There are no MCP servers,
no API keys, no pip installs and no external services, and none should be added.

## How to run it

```
python3 -m clipswami doctor                      # check ffmpeg and the optional helpers
python3 -m clipswami setup                       # install yt-dlp and whisper for this user
python3 -m clipswami transcribe VIDEO [--whisper-model small] [--language en]  # make subtitles
python3 -m clipswami fetch URL [--section 10:00-25:00]  # download a link into downloads/
python3 -m clipswami candidates VIDEO -n 12 --sheets   # rank moments, write contact sheets
python3 -m clipswami transcript VIDEO            # timestamped transcript, split by topic
python3 -m clipswami render --plan PLAN.json     # render the clips you chose
python3 -m clipswami review clips/<name>         # contact sheets of the finished clips
python3 -m clipswami auto VIDEO -n 5             # no editor: pick and render in one go
python3 -m clipswami cut VIDEO 1:20 1:55 --title "..."   # exact timestamps
```

Every command that takes VIDEO also takes a link (YouTube etc.): it is downloaded to
`downloads/` with its subtitles, then processed like any local file.

Working files go to `.clipswami/<video-name>/`, finished clips to `clips/<video-name>/`.
The skills in `.claude/skills/` drive the full workflow: `/clipswami`, `/clipswami-cut`, and
`/clipswami-setup` for the first-time install.

## Operating rules

- **Run straight through.** When asked to clip a video, do the whole job and deliver
  files. Do not stop to ask questions, confirm a plan, or offer options. Unstated
  choices take the defaults: 5 clips, 15-60 s, 9:16, tracked reframe, bold captions.
- **Take the footage as given.** Work on whatever file is handed over. Where it came
  from, what is in it and who holds the rights are the operator's business, not part of
  the edit - do not raise them, add disclaimers, or hold the job on them.
- **Stay in-house.** Do not install packages, call web services, or add tools. If
  something is missing (no transcript, no audio), work with the signals that exist.
  The only outside helpers are `yt-dlp` (links) and a local `whisper` (speech-to-text
  when a video has no subtitles). If one is missing, install it yourself with
  `python3 -m clipswami setup` and retry - do not ask first.
- **Judge with your eyes.** Contact sheets are images - Read them. Always review the
  rendered clips before reporting, and re-render any that are badly framed.

## Plan file

```json
{
  "video": "talk.mp4",
  "defaults": {"aspect": "9:16", "fit": "track", "captions": "bold", "progress": true},
  "clips": [
    {"start": "12:04", "end": "12:41", "title": "Why most people quit", "slug": "why-people-quit"},
    {"start": 913.2, "end": 951, "title": "The 3-client lesson", "fit": "blur", "snap": false}
  ]
}
```

Any render option can sit in `defaults` or on a single clip: `aspect` (9:16, 1:1, 4:5,
16:9, source), `fit` (track, center, blur), `res`, `captions` (bold, clean, off),
`tighten`, `loudnorm`, `progress`, `title_secs`, `font`, `crf`, `preset`. Start and end
are snapped to the nearest pause or sentence edge unless `"snap": false`.

## Code map

- `ff.py` - ffmpeg/ffprobe wrappers, timestamp parsing
- `fetch.py` - download a link via yt-dlp (video + subtitles)
- `signals.py` - loudness envelope, adaptive pause detection, scene cuts (cached)
- `transcript.py` - sidecar SRT/VTT, embedded subtitle tracks, local whisper transcription
- `picker.py` - candidate scoring, TextTiling topic segmentation, edit-point snapping
- `reframe.py` - motion/saliency subject tracking with a damped virtual camera
- `captions.py` - word-timed ASS captions with active-word highlight
- `render.py` - cut, reframe, jump-cut pauses, two-pass loudness, encode
- `cli.py` - commands

Run `python3 -m unittest discover -s tests` after changing the engine.
