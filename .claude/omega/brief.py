#!/usr/bin/env python3
"""Create and list worker briefs.

  brief.py new --goal "..." --read FILE [FILE...] --run "CMD" [--run "CMD"] [--see "TEXT"]
  brief.py list [--all]
  brief.py show ID
"""
import argparse
import re
import sys

import _common as c


def cmd_new(a):
    if not a.run and not a.see:
        c.die('a brief needs a check: add --run "COMMAND" (preferred) or --see "what to look at"')
    nums = [int(b.name[:3]) for b in c.briefs()]
    num = "%03d" % (max(nums) + 1 if nums else 1)
    slug = a.slug or re.sub(r"[^a-z0-9]+", "-", a.goal.lower()).strip("-")[:40].strip("-") or "task"
    path = c.state() / "briefs" / ("%s-%s.md" % (num, slug))
    missing = [f for f in a.read if not (c.root() / f).exists()]
    L = ["# Brief %s: %s" % (num, slug), "Status: open", "Created: " + c.today(), "",
         "## Goal", a.goal.strip(), "", "## Read"]
    L += ["- " + f for f in a.read] or ["- (nothing named; find what you need)"]
    L += ["", "## Done when"]
    L += ["- run: " + r for r in a.run]
    L += ["- see: " + s for s in a.see]
    if a.notes:
        L += ["", "## Notes", a.notes.strip()]
    L += ["", "## When finished",
          "Run `%s .claude/omega/check.py %s --by worker` and report its last line plus the files you changed."
          % (c.py(), num)]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    rel = path.relative_to(c.root()).as_posix()
    print("BRIEF %s %s" % (num, rel))
    if missing:
        print("WARN missing files: " + ", ".join(missing))
    if not a.run:
        print("WARN no --run command: this brief can only be checked by eye")
    print("DELEGATE: Read %s and do exactly what it says." % rel)


def cmd_list(a):
    rows = [c.parse_brief(b) for b in c.briefs()]
    if not a.all:
        rows = [r for r in rows if r["status"] != "accepted"]
    if not rows:
        print("no %sbriefs" % ("" if a.all else "open "))
    for r in rows:
        print("%s [%s] %s" % (r["id"], r["status"], c.one_line(r["goal"], 90)))


def cmd_show(a):
    sys.stdout.write(c.find_brief(a.id).read_text(encoding="utf-8"))


p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = p.add_subparsers(dest="cmd", required=True)
n = sub.add_parser("new")
n.add_argument("--goal", required=True)
n.add_argument("--read", nargs="*", default=[])
n.add_argument("--run", action="append", default=[], help="command that must exit 0 (repeatable)")
n.add_argument("--see", action="append", default=[], help="something to check by eye (repeatable)")
n.add_argument("--notes", default="")
n.add_argument("--slug", default="")
n.set_defaults(f=cmd_new)
l = sub.add_parser("list")
l.add_argument("--all", action="store_true")
l.set_defaults(f=cmd_list)
s = sub.add_parser("show")
s.add_argument("id")
s.set_defaults(f=cmd_show)
a = p.parse_args()
a.f(a)
