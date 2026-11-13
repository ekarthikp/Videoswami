# Brief 005: finish
Status: accepted
Created: 2026-11-13

## Goal
The 'finish.py + captions.py + templates.py, and CORE changes' section of docs/SPEC.md is fully implemented: animated captions, title templates, overlay animation, keyframed camera moves, export profiles, as commands and as edit-spec features

## Read
- docs/SPEC.md
- tests/check2.py
- tests/check.py
- videoswami/spec.py
- videoswami/render.py
- videoswami/ff.py

## Done when
- run: python3 tests/check2.py finish

## Notes
You may create videoswami/finish.py, captions.py, templates.py and edit videoswami/spec.py and videoswami/render.py. Never edit tests/, docs/, __main__.py, quick.py, analyze.py, speech.py or smart.py (other workers own speech.py and smart.py right now). Keep spec.py/render.py importable at every save: other workers call render_spec while you work, so make each edit a complete, working change. Only import speech lazily inside the captions command when --transcript is not given. ASS: PlayResX/Y = output size, colours are &HAABBGGRR, escape braces/newlines in text, find a font via ff.find_font and pass fontsdir when needed. The check ends by rerunning the v1 core check: existing behaviour must not change. No TODOs.

## When finished
Run `python3 .claude/omega/check.py 005 --by worker` and report its last line plus the files you changed.
