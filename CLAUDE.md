# VideoSwami

VideoSwami: ffmpeg-based command-line video editor driven by Claude (python stdlib only)

<!-- omega:start - managed block; re-running setup rewrites it, edit above or below instead -->
## How we work here

Orchestrator and workers. `O` below means `python3 .claude/omega`.

- **Main session = orchestrator:** plan, write briefs, check results. Hand bulk work to a worker. An edit quicker to make than to brief is fine to do directly.
- **Workers** = subagents in `.claude/agents/` on the cheaper worker model (`O/models.py show`).
- **Session start:** run `O/status.py` and work from its output. Open the docs files only if you need a detail.
- **Delegate:** `O/brief.py new --goal "..." --read FILE... --run "COMMAND"`, then give the worker the `DELEGATE:` line it prints. Every brief needs a `--run` command that can fail.
- **Before accepting any work:** `O/check.py ID --accept`. On `FAIL`, send the printed output back to the worker. Never pass on a worker's report unchecked.
- **Record as you go:** `O/log.py decision|issue|next|done ...` (run with `-h` for the form). Do not hand-edit the docs files.
- **Session end:** `O/log.py progress "..."`, then run the `reflect` skill. Apply its proposals only after I approve.

### Checks
- test: `python3 tests/check.py`

### Folder map
```
CLAUDE.md             the rules and this map (loaded every session)
docs/progress.md      done, next, one dated line per session
docs/decisions.md     what is settled, with date and why
docs/known-issues.md  gotchas and their fixes
.claude/agents/       worker agents, one job each
.claude/skills/       one folder per repeatable task
.claude/omega/        helper scripts (status, brief, check, log, reflect, models, doctor)
.omega/               briefs, check logs, config
```
<!-- omega:end -->
