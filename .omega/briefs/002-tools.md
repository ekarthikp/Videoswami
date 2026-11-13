# Brief 002: tools
Status: accepted
Created: 2026-11-13

## Goal
videoswami/analyze.py and videoswami/quick.py implement every command SPEC.md assigns to worker TOOLS, each exposed through a register(subparsers) function

## Read
- docs/SPEC.md
- tests/check.py

## Done when
- run: python3 tests/check.py tools

## Notes
Only create/edit videoswami/analyze.py and videoswami/quick.py. Do not edit tests/ or docs/. Another worker is writing the CORE files (ff.py, spec.py, render.py, __main__.py) right now against the same SPEC: import from them (from .ff import run_ffmpeg, probe, VSError, esc, find_font; from .render import render_spec), never edit them. If the check fails only because CORE files are missing or incomplete, sleep 90 and rerun (up to 12 times) rather than writing them yourself; if a CORE function behaves differently from SPEC, report it instead of working around it. Prefer building a spec dict and calling render_spec so behaviour is uniform; use direct ffmpeg calls for trim --fast, gif, extract-audio, thumbnail, compress two-pass, stabilize, split and analysis. No TODOs; clear error messages.

## When finished
Run `python3 .claude/omega/check.py 002 --by worker` and report its last line plus the files you changed.
