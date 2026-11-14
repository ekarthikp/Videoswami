---
name: clipswami
description: Turn a long video (local file or YouTube/other link) into finished short clips, end to end - find the best moments, reframe, caption, render, review. Use when the user asks to clip, cut highlights, or make shorts/reels from a video.
argument-hint: <video path or link> [any instructions: count, length, aspect, style, topic]
---

Clip this video: $ARGUMENTS

You are the editor. Run the whole job without asking anything - follow the operating
rules in REFERENCE.md (in this skill folder). Anything in the instructions above overrides a default below.

0. **If the source is a link**, fetch it first: `python3 -m clipswami fetch "<link>"`
   (add `--section START-END` when only part of a long video is wanted). Use the path
   it prints as `<video>` from here on. If yt-dlp is missing or the download fails,
   run `python3 -m clipswami setup --upgrade` and retry once.

1. **Analyse.** Run both:
   - `python3 -m clipswami candidates "<video>" -n 12 --sheets`
   - `python3 -m clipswami transcript "<video>"`
   If a command fails on a missing tool, follow the `clipswami-setup` skill, then carry on.

   **No transcript?** If the output says `transcript: none`, make one with whisper:
   `python3 -m clipswami transcribe "<video>"` - it saves `<video>.srt` beside the video
   so every later command picks it up - then run the two commands above again.
   - Model: `--whisper-model base` is the default; use `small` for accents, crosstalk or
     noisy audio, `tiny` when the video is over an hour and speed matters.
   - Language: add `--language <code>` (en, hi, es ...) when you know it; otherwise it
     is detected.
   - If whisper is not installed, run `python3 -m clipswami setup` and retry once.
   - If it still cannot run, or the video has no speech, continue without a transcript:
     pick from scores and contact sheets, and the clips render without captions.

2. **Study the material.** Read `.clipswami/<name>/candidates.json`. If a transcript
   exists, read `.clipswami/<name>/transcript.txt` in full (blank lines are topic
   changes). Read the candidate contact sheets (`.clipswami/<name>/sheets/*.jpg`) to see
   what is on screen.

3. **Choose like an editor, not a scorer.** The ranked candidates are leads. Pick the
   moments (default 5) that work as standalone videos:
   - opens on a hook in the first 3 seconds - a question, a bold claim, a number, tension
   - carries one complete thought, and ends on the payoff rather than trailing off
   - makes sense to someone who never saw the rest
   - differs from the other picks
   Move the in/out points wherever the transcript says the thought really starts and
   ends; you may pick moments the scorer missed. With no transcript, go by score,
   contact sheets and variety. Give each clip a title of at most 6 words that sells
   the hook without repeating the first spoken line.

4. **Write the plan** to `.clipswami/<name>/plan.json` (format in REFERENCE.md, in this skill folder) and render:
   `python3 -m clipswami render --plan .clipswami/<name>/plan.json`

5. **Quality check.** Run `python3 -m clipswami review clips/<name>` and Read every
   sheet in `clips/<name>/review/`. Look for: subject cut off or drifting out of frame
   (switch that clip to `"fit": "blur"` or `"center"`), captions covering the subject
   or unreadable (try `"captions": "clean"`), a clip that starts or ends mid-sentence
   (adjust times, `"snap": false` if snapping fights you). Fix the plan and re-render
   until every clip passes. Do not report clips you have not looked at.

6. **Write the posting kit** to `clips/<name>/posts.md`: for each clip, the file name,
   title, a 1-2 sentence caption and 3-5 hashtags.

7. **Report** briefly: a table of file, length, title, and why it was picked, plus
   the output folder. No caveats, no questions.

ClipSwami is VideoSwami's sub-skill for long video to short clips. For any other edit
(one video in, one edited video out) use the `videoswami` skill instead; its finished
clips can also be polished further with `videoswami` commands (titles, music, export profiles).
