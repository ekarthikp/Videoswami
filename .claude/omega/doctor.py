#!/usr/bin/env python3
"""Check that the setup is complete and consistent. Prints one line per check.

  doctor.py
"""
import os
import re

import _common as c

r = c.root()
cf = c.cfg()
bad = 0


def line(ok, msg):
    global bad
    if ok is False:
        bad += 1
    print("%s %s" % ({True: "ok  ", False: "FAIL", None: "note"}[ok], msg))


for rel in ("CLAUDE.md", "docs/progress.md", "docs/decisions.md", "docs/known-issues.md",
            ".claude/agents/builder.md", ".claude/agents/reviewer.md",
            ".claude/skills/reflect/SKILL.md", ".omega/config.json"):
    line((r / rel).exists(), rel)

cm = r / "CLAUDE.md"
if cm.exists():
    txt = cm.read_text(encoding="utf-8")
    line("omega:start" in txt and "omega:end" in txt, "CLAUDE.md has the omega section")
    line(None, "CLAUDE.md is %d lines (it is loaded every session; shorter is cheaper)" % len(txt.splitlines()))

for f in sorted((r / ".claude" / "agents").glob("*.md")):
    txt = f.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", txt, re.S)
    if not m:
        line(False, "%s has no frontmatter" % f.name)
        continue
    head = m.group(1)
    ok = bool(re.search(r"^name:\s*\S", head, re.M) and re.search(r"^description:\s*\S", head, re.M))
    mm = re.search(r"^model:\s*(.+)$", head, re.M)
    line(ok, "%s name+description, model=%s" % (f.name, mm.group(1).strip() if mm else "(inherits)"))

ch = cf.get("checks") or {}
line(bool(ch.get("test")) or None, "test command: %s" % (ch.get("test") or "none set - briefs will need their own --run"))

for k in ("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SUBAGENT_MODEL",
          "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_MODEL",
          "CLAUDE_CODE_MAX_CONTEXT_TOKENS"):
    v = os.environ.get(k)
    line(None, "%s=%s" % (k, v if v else "(unset)"))
line(None, "ANTHROPIC_BASE_URL is %s" % ("set (requests go to a gateway)" if os.environ.get("ANTHROPIC_BASE_URL")
                                         else "unset (direct to Anthropic)"))
print("%d problem(s)" % bad)
raise SystemExit(1 if bad else 0)
