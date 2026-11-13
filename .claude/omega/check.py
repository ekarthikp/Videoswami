#!/usr/bin/env python3
"""Run a brief's "Done when" commands and print a short verdict.

  check.py ID --by worker        worker runs this before reporting done
  check.py ID --accept           orchestrator reruns it; on pass the brief is accepted and logged
  check.py ID --accept --seen    also confirms the "see:" items were looked at

Prints one line on pass. On fail prints the failing command and the tail of its
output. Full output is kept in .omega/logs/ID-last.log. Exit 0 = pass, 1 = fail.
"""
import argparse
import json
import subprocess
import sys
import time

import _common as c

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("id")
p.add_argument("--by", choices=["worker", "orchestrator"], default="orchestrator")
p.add_argument("--accept", action="store_true")
p.add_argument("--seen", action="store_true", help="manual 'see:' items were checked")
p.add_argument("--timeout", type=int, default=600)
p.add_argument("--tail", type=int, default=30)
a = p.parse_args()

path = c.find_brief(a.id)
b = c.parse_brief(path)
logs = c.state() / "logs"
full = []
failed = None
t0 = time.time()

for cmd in b["run"]:
    try:
        r = subprocess.run(cmd, shell=True, cwd=str(c.root()), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=a.timeout)
        out, code = r.stdout.decode("utf-8", "replace"), r.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") + "\n[timed out after %ss]" % a.timeout
        code = 124
    full.append("$ %s\n%s\n[exit %s]\n" % (cmd, out.rstrip(), code))
    if code != 0:
        failed = (cmd, code, out)
        break

secs = round(time.time() - t0, 1)
(logs / ("%s-last.log" % b["id"])).write_text("\n".join(full), encoding="utf-8")
with open(logs / "attempts.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"ts": c.now(), "brief": b["id"], "slug": b["slug"], "by": a.by,
                        "ok": failed is None, "cmd": failed[0] if failed else None,
                        "exit": failed[1] if failed else 0, "secs": secs}) + "\n")

if failed:
    cmd, code, out = failed
    tail = out.rstrip().splitlines()[-a.tail:]
    print("FAIL %s: `%s` exited %s (%ss)" % (b["id"], cmd, code, secs))
    print("--- last %d lines (full: .omega/logs/%s-last.log) ---" % (len(tail), b["id"]))
    print("\n".join(tail))
    if b["status"] != "accepted":
        c.set_status(path, "open")
    sys.exit(1)

for s in b["see"]:
    print("MANUAL: " + s)

verdict = "PASS %s: %d command(s) ok in %ss" % (b["id"], len(b["run"]), secs)
if a.accept:
    if b["see"] and not a.seen:
        print(verdict + " - NOT accepted: look at the MANUAL item(s) above, then rerun with --accept --seen")
        sys.exit(1)
    if not b["run"] and not a.seen:
        print("NOT accepted: this brief has no commands; confirm by eye and rerun with --accept --seen")
        sys.exit(1)
    if b["status"] != "accepted":
        c.set_status(path, "accepted")
        c.append_under(c.doc("progress.md"), "Done",
                       "- %s - Brief %s: %s" % (c.today(), b["id"], c.one_line(b["goal"], 140)))
    print(verdict + " - ACCEPTED and logged in docs/progress.md")
else:
    if b["status"] == "open":
        c.set_status(path, "worker-passed" if a.by == "worker" else "checked")
    print(verdict)
