#!/usr/bin/env python3
"""Record things in the docs files without reading or rewriting them.

  log.py progress "what this session changed, what is still open"
  log.py next "thing to do"
  log.py done "thing finished" [--clears "text of a Next item"]
  log.py decision "what was decided" --why "reason"
  log.py issue "short name" --what "symptom" --fix "what to do"
"""
import argparse

import _common as c


def clean(s):
    return " ".join(s.split()).replace("|", "/")


p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = p.add_subparsers(dest="cmd", required=True)
for name in ("progress", "next", "done"):
    s = sub.add_parser(name)
    s.add_argument("text")
    if name == "done":
        s.add_argument("--clears", default="")
d = sub.add_parser("decision")
d.add_argument("text")
d.add_argument("--why", required=True)
i = sub.add_parser("issue")
i.add_argument("text")
i.add_argument("--what", required=True)
i.add_argument("--fix", required=True)
a = p.parse_args()

prog = c.doc("progress.md")
if a.cmd == "progress":
    c.append_under(prog, "Log", "- %s - %s" % (c.today(), clean(a.text)))
    print("logged session line in docs/progress.md")
elif a.cmd == "next":
    c.append_under(prog, "Next", "- " + clean(a.text))
    print("added to Next")
elif a.cmd == "done":
    gone = c.remove_matching(prog, "Next", a.clears or a.text)
    c.append_under(prog, "Done", "- %s - %s" % (c.today(), clean(a.text)))
    print("added to Done" + (" (cleared Next: %s)" % c.one_line(gone, 60) if gone else ""))
elif a.cmd == "decision":
    f = c.doc("decisions.md")
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# Decisions\n\n| Date | Decision | Why |\n|------|----------|-----|\n", encoding="utf-8")
    txt = f.read_text(encoding="utf-8").rstrip("\n")
    f.write_text(txt + "\n| %s | %s | %s |\n" % (c.today(), clean(a.text), clean(a.why)), encoding="utf-8")
    print("decision recorded")
elif a.cmd == "issue":
    f = c.doc("known-issues.md")
    txt = f.read_text(encoding="utf-8") if f.exists() else "# Known issues\n\n## Issues\n"
    txt = txt.replace("_None recorded yet._\n", "").rstrip("\n")
    if "## Issues" not in txt:
        txt += "\n\n## Issues"
    txt += "\n\n### %s\n- **Seen:** %s\n- **What happens:** %s\n- **Fix or workaround:** %s\n" % (
        clean(a.text), c.today(), clean(a.what), clean(a.fix))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(txt, encoding="utf-8")
    print("issue recorded")
