#!/usr/bin/env python3
"""Show or change which models the orchestrator and workers use.

  models.py show
  models.py set --worker NAME [--orchestrator NAME]
      Rewrites `model:` in .claude/agents/*.md. NAME is an alias (haiku, sonnet,
      opus, inherit) or any model id your provider or gateway accepts.
  models.py env [--base-url URL] [--worker-id ID] [--orchestrator-id ID]
                [--context TOKENS] [--compact PCT] [--all-subagents]
                [--shell bash|powershell|json]
      Prints the environment lines to set before launching Claude Code.
      With --worker-id / --orchestrator-id the agent files can keep the plain
      aliases (haiku / opus) and the environment maps them to your models, so
      the project files stay the same on every provider.
"""
import argparse
import json
import re

import _common as c


def agent_files():
    return sorted((c.root() / ".claude" / "agents").glob("*.md"))


def agent_model(path):
    txt = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", txt, re.S)
    if not m:
        return None
    mm = re.search(r"^model:\s*(.+)$", m.group(1), re.M)
    return mm.group(1).strip() if mm else None


def cmd_show(a):
    cf = c.cfg()
    print("orchestrator=%s worker=%s" % (cf.get("orchestrator_model", "?"), cf.get("worker_model", "?")))
    for f in agent_files():
        print("  %s: model=%s" % (f.name, agent_model(f) or "(none: inherits the session model)"))


def cmd_set(a):
    cf = c.cfg()
    old = cf.get("worker_model")
    if a.worker:
        n = 0
        for f in agent_files():
            txt = f.read_text(encoding="utf-8")
            m = re.match(r"^---\n(.*?)\n---", txt, re.S)
            if not m:
                continue
            cur = agent_model(f)
            if cur is not None and not a.all and old and cur != old:
                print("  kept %s (model=%s, not the worker model)" % (f.name, cur))
                continue
            head = m.group(1)
            if cur is None:
                head += "\nmodel: " + a.worker
            else:
                head = re.sub(r"^model:.*$", "model: " + a.worker, head, flags=re.M)
            f.write_text("---\n" + head + "\n---" + txt[m.end():], encoding="utf-8")
            n += 1
        cf["worker_model"] = a.worker
        print("worker model -> %s in %d agent file(s)" % (a.worker, n))
    if a.orchestrator:
        cf["orchestrator_model"] = a.orchestrator
        print("orchestrator model -> %s (select it yourself with /model %s)" % (a.orchestrator, a.orchestrator))
    c.save_cfg(cf)


def cmd_env(a):
    cf = c.cfg()
    env = []
    if a.base_url:
        env.append(("ANTHROPIC_BASE_URL", a.base_url, "send requests to your gateway"))
        env.append(("ANTHROPIC_AUTH_TOKEN", "<your gateway key>", "sent as the Authorization bearer token"))
    if a.orchestrator_id:
        env.append(("ANTHROPIC_DEFAULT_OPUS_MODEL", a.orchestrator_id, "what the `opus` alias resolves to"))
        env.append(("ANTHROPIC_DEFAULT_SONNET_MODEL", a.orchestrator_id, "what the `sonnet` alias resolves to"))
        env.append(("ANTHROPIC_MODEL", "opus", "start sessions on the orchestrator model"))
    if a.worker_id:
        env.append(("ANTHROPIC_DEFAULT_HAIKU_MODEL", a.worker_id,
                    "what the `haiku` alias resolves to (also used for background tasks)"))
    if a.all_subagents:
        env.append(("CLAUDE_CODE_SUBAGENT_MODEL", cf.get("worker_model", "haiku"), "model for subagents"))
        env.append(("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "1", "force it onto built-in agents too"))
    if a.context:
        env.append(("CLAUDE_CODE_MAX_CONTEXT_TOKENS", str(a.context), "context window of the model you route to"))
    env.append(("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", str(a.compact), "compact early; use 60 for long builds"))
    if a.shell == "json":
        print(json.dumps({"env": {k: v for k, v, _ in env}}, indent=2))
        print("# put this in .claude/settings.local.json (keep keys out of files you commit)")
    else:
        for k, v, why in env:
            if a.shell == "powershell":
                print('$env:%s = "%s"   # %s' % (k, v, why))
            else:
                print('export %s="%s"   # %s' % (k, v, why))
        print("# then launch Claude Code from this same shell")


p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = p.add_subparsers(dest="cmd", required=True)
sub.add_parser("show").set_defaults(f=cmd_show)
s = sub.add_parser("set")
s.add_argument("--worker")
s.add_argument("--orchestrator")
s.add_argument("--all", action="store_true", help="change every agent file, not only ones on the old worker model")
s.set_defaults(f=cmd_set)
e = sub.add_parser("env")
e.add_argument("--base-url")
e.add_argument("--worker-id")
e.add_argument("--orchestrator-id")
e.add_argument("--context", type=int)
e.add_argument("--compact", type=int, default=40)
e.add_argument("--all-subagents", action="store_true")
e.add_argument("--shell", choices=["bash", "powershell", "json"], default="bash")
e.set_defaults(f=cmd_env)
a = p.parse_args()
a.f(a)
