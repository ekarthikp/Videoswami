---
name: clipswami-setup
description: First-time install for Clipswami - checks and installs ffmpeg, yt-dlp (for links) and whisper (for captions on videos without subtitles). Use when the user says set up, install, get started, or when a clipswami command fails on a missing tool.
---

Get this machine ready to clip. Do every step yourself and do not ask questions.

1. **Check.** Run `python3 -m clipswami doctor`. If everything reads `ok`, say so and stop.

2. **ffmpeg** (required). If ffmpeg or ffprobe is MISSING, install it with the system
   package manager, picking by platform:
   - macOS: `brew install ffmpeg`
   - Debian/Ubuntu: `sudo apt-get update && sudo apt-get install -y ffmpeg`
   - Fedora: `sudo dnf install -y ffmpeg`
   - Arch: `sudo pacman -S --noconfirm ffmpeg`
   - Windows: `winget install --id Gyan.FFmpeg -e`
   If `libass` or `libx264` reads MISSING, the installed ffmpeg is a cut-down build:
   replace it with the full build from the same package manager.

3. **Helpers.** Run `python3 -m clipswami setup`. It installs with pip, for this user only:
   - `yt-dlp` - downloads videos from links
   - `whisper-ctranslate2` - local speech-to-text, so videos with no subtitles still get
     captions. Add `--no-whisper` only if the user said they do not want captions.
   If a helper reports "installed but not on PATH", add `~/.local/bin` (macOS/Linux) or
   the Python `Scripts` folder (Windows) to PATH in the shell profile, then re-run.

4. **Warm up whisper.** The first transcription downloads the speech model (about 150 MB
   for `base`). Nothing to do now - just mention it so the first run is not a surprise.

5. **Verify.** Run `python3 -m clipswami doctor` again and report the final state in a
   short table: tool, status. If something still fails, give the exact error and the one
   command that fixes it.

To update the helpers later (for example when a download starts failing):
`python3 -m clipswami setup --upgrade`.
