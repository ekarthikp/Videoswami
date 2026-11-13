# Brief 001: core
Status: accepted
Created: 2026-11-13

## Goal
The videoswami package implements everything SPEC.md assigns to worker CORE (ff.py, spec.py, render.py, __main__.py, build.py) so that probe, validate and render work exactly as specified

## Read
- docs/SPEC.md
- tests/check.py

## Done when
- run: python3 tests/check.py core

## Notes
Only create/edit videoswami/__init__.py, __main__.py, ff.py, spec.py, render.py and build.py. Do not edit tests/ or docs/. Another worker is writing videoswami/analyze.py and videoswami/quick.py at the same time against the same SPEC; never touch those. ffmpeg 6.1 is installed with libx264, libx265, libvpx-vp9, drawtext, xfade, subtitles. Write drawtext text via textfile= temp files to avoid escaping bugs. Keep modules readable and commented; no TODOs. Production quality error messages.

## When finished
Run `python3 .claude/omega/check.py 001 --by worker` and report its last line plus the files you changed.
