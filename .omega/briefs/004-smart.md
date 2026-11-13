# Brief 004: smart
Status: accepted
Created: 2026-11-13

## Goal
videoswami/smart.py implements everything the 'smart.py (worker SMART)' section of docs/SPEC.md specifies (reframe, highlights, beats, beat-sync, enhance)

## Read
- docs/SPEC.md
- tests/check2.py
- videoswami/ff.py
- videoswami/render.py
- videoswami/analyze.py

## Done when
- run: python3 tests/check2.py smart

## Notes
Only create/edit videoswami/smart.py. Never edit tests/, docs/ or any other module (two other workers are editing speech.py and spec.py/render.py right now; if render_spec briefly breaks while they work, wait 60s and rerun). Standard library only at import time; numpy/cv2 optional inside functions with a pure-Python fallback that must itself pass the check (test it by forcing the fallback, e.g. an env var VIDEOSWAMI_NO_NUMPY=1 that your code honours). For reframe, drive the crop with a piecewise-linear expression of t (or sendcmd) in a direct ffmpeg call. No TODOs.

## When finished
Run `python3 .claude/omega/check.py 004 --by worker` and report its last line plus the files you changed.
