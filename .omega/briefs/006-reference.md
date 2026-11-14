# Brief 006: reference
Status: accepted
Created: 2026-11-14

## Goal
videoswami/reference.py implements the 'reference.py (worker REFERENCE)' section of docs/SPEC.md (reference and match commands, the Python API), and the edit spec accepts a top-level look.reference that grades the timeline

## Read
- docs/SPEC.md
- tests/check3.py
- videoswami/ff.py
- videoswami/analyze.py
- videoswami/smart.py
- videoswami/spec.py
- videoswami/render.py

## Done when
- run: python3 tests/check3.py reference

## Notes
Create videoswami/reference.py with register(subparsers) (already wired in __main__.py). You may edit videoswami/spec.py and videoswami/render.py only for the look key. Never edit tests/, docs/, tools/, clipswami/ or other modules. Another worker is running the CLI to render gallery pictures right now: keep spec.py and render.py importable and working at every save. Reuse existing helpers (analyze contact sheet and scene detection, smart frame sampling, quick/render for output) instead of copying them. Standard library only at import; numpy optional inside functions and VIDEOSWAMI_NO_NUMPY=1 must force the pure-Python path. The colour match must measure its own result and correct once, as SPEC says. No TODOs.

## When finished
Run `python3 .claude/omega/check.py 006 --by worker` and report its last line plus the files you changed.
