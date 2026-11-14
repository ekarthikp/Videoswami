---
name: clipswami-cut
description: Cut one clip from exact timestamps out of a video file or link, with reframing and captions. Use when the user gives a start and end time.
argument-hint: <video path or link> <start> <end> [title and any options]
---

Cut this clip: $ARGUMENTS

The video may be a local path or a link (links are downloaded automatically).
Run `python3 -m clipswami cut "<video>" <start> <end>` with `--title "..."` if a title
was given, mapping any other instructions to flags (`--aspect`, `--fit`, `--captions`,
`--progress`, `--no-tighten`, `--snap`, `--res`). If the output shows `captions: false` in the manifest and captions were wanted, run
`python3 -m clipswami transcribe "<video>"` and cut again. Then run
`python3 -m clipswami review clips/<name>`, Read the review sheet, fix framing if needed,
and report the output file. Do not ask questions.
