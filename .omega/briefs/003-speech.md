# Brief 003: speech
Status: accepted
Created: 2026-11-13

## Goal
videoswami/speech.py implements everything the 'speech.py (worker SPEECH)' section of docs/SPEC.md specifies (transcript API, transcribe with all four backends, find, cut-speech, remove-fillers, edit-text)

## Read
- docs/SPEC.md
- tests/check2.py
- videoswami/ff.py
- videoswami/quick.py

## Done when
- run: python3 tests/check2.py speech

## Notes
Only create/edit videoswami/speech.py. Never edit tests/, docs/ or any other module (two other workers are editing smart.py and the finish/core files right now). Standard library only at import time; whisper backends imported/located lazily. The sandbox has no whisper model, so the real backends cannot be run here: write them carefully against each tool's documented interface, keep each in its own small function, and list in your report exactly which backend paths were NOT executed. Reuse quick.py's keep-ranges approach via render_spec (read how quick 'keep' builds its spec). No TODOs.

## When finished
Run `python3 .claude/omega/check.py 003 --by worker` and report its last line plus the files you changed.
