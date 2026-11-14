# Decisions

What is settled, with the date and why. Added with `log.py decision`.

| Date | Decision | Why |
|------|----------|-----|
| 2026-11-13 | Main session orchestrates; subagents on a cheaper model do bulk work | Keep the strong model for planning and checking, spend volume on the cheap one |
| 2026-11-13 | Every brief ends with a runnable "Done when:" check, rerun by the orchestrator | A worker's report is a claim; the check is the proof |
| 2026-11-14 | ClipSwami lives in this repo as a sub-skill (clipswami/ package + .claude/skills/clipswami*) | user asked for /clipswami under VideoSwami |
