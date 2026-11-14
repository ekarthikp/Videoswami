# Brief 007: gallery
Status: accepted
Created: 2026-11-14

## Goal
tools/make_gallery.py generates every picture listed in the 'Visual guide (worker GALLERY)' section of docs/SPEC.md into docs/gallery/ and writes docs/VISUAL-GUIDE.md showing each picture with the command or spec that makes it

## Read
- docs/SPEC.md
- tests/check3.py
- README.md

## Done when
- run: python3 tests/check3.py gallery
- see: docs/gallery/contact.jpg looks like a clean style catalogue: demo footage reads as a presenter on a soft background, captions and titles are legible, nothing is cut off

## Notes
Only create/edit tools/make_gallery.py, docs/gallery/* and docs/VISUAL-GUIDE.md. Never edit videoswami/, clipswami/, tests/ or other docs. Drive the tool only through its CLI (python3 -m videoswami ... run from the repo root); run 'python3 -m videoswami <command> -h' to learn flags. Another worker is editing videoswami/spec.py and render.py right now: if a CLI call fails with an import or internal error, sleep 60 and retry (up to 8 times) rather than working around it. Work in a temp dir and delete it afterwards; the script must be re-runnable and finish in under 4 minutes. Open docs/gallery/contact.jpg and a few pictures with your image reader before reporting, and fix anything illegible or cut off. Keep JPGs small (quality about 80). VISUAL-GUIDE.md: one section per group, a table or grid of pictures with a caption and a fenced code block per look; plain language.

## When finished
Run `python3 .claude/omega/check.py 007 --by worker` and report its last line plus the files you changed.
