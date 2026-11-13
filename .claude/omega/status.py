#!/usr/bin/env python3
"""Session-start digest. Read this instead of opening the docs files one by one.

  status.py            short digest
  status.py --more     longer history (10 log lines, 10 decisions)
"""
import argparse
import re

import _common as c

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("--more", action="store_true")
a = p.parse_args()
n = 10 if a.more else 3
cf = c.cfg()

print("OMEGA STATUS %s | orchestrator=%s worker=%s" % (
    c.today(), cf.get("orchestrator_model", "?"), cf.get("worker_model", "?")))
ch = cf.get("checks") or {}
if any(ch.values()):
    print("CHECKS: " + " | ".join("%s=`%s`" % (k, v) for k, v in ch.items() if v))

prog = c.doc("progress.md")
nxt = c.section_lines(prog, "Next")
print("NEXT (%d):" % len(nxt))
for l in nxt[:8]:
    print("  " + c.one_line(l))

open_b = [c.parse_brief(b) for b in c.briefs()]
open_b = [b for b in open_b if b["status"] != "accepted"]
print("OPEN BRIEFS (%d):" % len(open_b))
for b in open_b[:8]:
    print("  %s [%s] %s" % (b["id"], b["status"], c.one_line(b["goal"], 80)))

log = c.section_lines(prog, "Log")
log = [l for l in log if l.lstrip().startswith("- ")]
print("LAST SESSIONS (%d of %d):" % (min(n, len(log)), len(log)))
for l in log[-n:]:
    print("  " + c.one_line(l))

dec = c.doc("decisions.md")
rows = []
if dec.exists():
    for l in dec.read_text(encoding="utf-8").splitlines():
        if l.startswith("|") and not re.match(r"^\|\s*(Date|-+)", l):
            cells = [x.strip() for x in l.strip().strip("|").split("|")]
            if len(cells) >= 2:
                rows.append(cells)
print("DECISIONS (%d of %d):" % (min(max(n, 5), len(rows)), len(rows)))
for r in rows[-max(n, 5):]:
    print("  %s %s" % (r[0], c.one_line(r[1], 95)))

iss = c.doc("known-issues.md")
titles = []
if iss.exists():
    on = False
    for l in iss.read_text(encoding="utf-8").splitlines():
        if l.startswith("## "):
            on = l[3:].strip().lower() == "issues"
        elif on and l.startswith("### "):
            titles.append(l[4:].strip())
print("KNOWN ISSUES (%d):" % len(titles))
for t in titles[-12:]:
    print("  - " + c.one_line(t))
print("Details: docs/progress.md, docs/decisions.md, docs/known-issues.md (open only if needed)")
