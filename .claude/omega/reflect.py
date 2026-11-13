#!/usr/bin/env python3
"""Evidence for the end-of-session review, taken from the check log.

  reflect.py              everything since the last reflect
  reflect.py --days 7     the last 7 days instead
  reflect.py --mark       remember now as "last reflected" (run after the review)

Shows which briefs failed their check, how often, on which command, and which
checks were slow. Facts only; deciding what to change is the reviewer's job.
"""
import argparse
import collections
import datetime
import json

import _common as c

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("--days", type=int)
p.add_argument("--mark", action="store_true")
a = p.parse_args()

cf = c.cfg()
if a.mark:
    cf["last_reflect"] = c.now()
    c.save_cfg(cf)
    print("marked reflect at " + cf["last_reflect"])
    raise SystemExit

since = cf.get("last_reflect", "")
if a.days:
    since = (datetime.datetime.now() - datetime.timedelta(days=a.days)).isoformat(timespec="seconds")

f = c.state() / "logs" / "attempts.jsonl"
rows = []
if f.exists():
    for l in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(l)
        except ValueError:
            continue
        if r.get("ts", "") >= since:
            rows.append(r)

print("REFLECT since %s: %d check runs" % (since or "the beginning", len(rows)))
if not rows:
    print("No check runs recorded. Nothing measured went wrong; rely on what you saw in the session.")
    raise SystemExit

by = collections.defaultdict(list)
for r in rows:
    by[r["brief"]].append(r)

goals = {x["id"]: x for x in (c.parse_brief(b) for b in c.briefs())}
trouble = []
for bid, rs in sorted(by.items()):
    fails = [r for r in rs if not r["ok"]]
    wfalse = 0
    for i, r in enumerate(rs):
        # worker said pass, then the orchestrator's rerun failed
        if r["by"] == "orchestrator" and not r["ok"] and i and rs[i - 1]["by"] == "worker" and rs[i - 1]["ok"]:
            wfalse += 1
    if fails or wfalse:
        trouble.append((len(fails), bid, rs, fails, wfalse))

print("BRIEFS CHECKED: %d, with failures: %d" % (len(by), len(trouble)))
for nf, bid, rs, fails, wfalse in sorted(trouble, reverse=True)[:10]:
    g = goals.get(bid, {})
    print("- %s [%s] %d fail / %d runs%s: %s" % (
        bid, g.get("status", "?"), nf, len(rs),
        ", %d passed for worker then failed on rerun" % wfalse if wfalse else "",
        c.one_line(g.get("goal", rs[0].get("slug", "")), 70)))
    cmds = collections.Counter(r["cmd"] for r in fails if r.get("cmd"))
    for cmd, k in cmds.most_common(2):
        print("    failing: `%s` x%d" % (c.one_line(cmd, 80), k))

slow = sorted(rows, key=lambda r: r.get("secs", 0), reverse=True)[:3]
if slow and slow[0].get("secs", 0) >= 30:
    print("SLOW CHECKS:")
    for r in slow:
        if r.get("secs", 0) >= 30:
            print("- %s %ss" % (r["brief"], r["secs"]))

stale = [b for b in goals.values() if b["status"] != "accepted"]
if stale:
    print("STILL OPEN: " + ", ".join("%s[%s]" % (b["id"], b["status"]) for b in stale[:12]))
total = sum(r.get("secs", 0) for r in rows)
print("Time spent in checks: %ds. After the review run: reflect.py --mark" % total)
