"""Shared helpers for the omega runtime scripts. Standard library only."""
import datetime
import json
import os
import pathlib
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def root():
    env = os.environ.get("OMEGA_ROOT")
    if env:
        return pathlib.Path(env).resolve()
    here = pathlib.Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / ".omega").is_dir():
            return p
    return here


def state():
    d = root() / ".omega"
    (d / "briefs").mkdir(parents=True, exist_ok=True)
    (d / "logs").mkdir(parents=True, exist_ok=True)
    return d


def cfg():
    f = root() / ".omega" / "config.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def save_cfg(c):
    f = state() / "config.json"
    f.write_text(json.dumps(c, indent=2) + "\n", encoding="utf-8")


def py():
    return cfg().get("py") or ("python" if os.name == "nt" else "python3")


def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def die(msg, code=2):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)


def one_line(s, n=110):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ---- briefs -----------------------------------------------------------------

def briefs():
    return sorted((state() / "briefs").glob("[0-9][0-9][0-9]-*.md"))


def find_brief(ident):
    p = pathlib.Path(ident)
    if p.is_file():
        return p
    m = re.match(r"^\d+", str(ident))
    if m:
        num = "%03d" % int(m.group())
        for b in briefs():
            if b.name.startswith(num + "-"):
                return b
    die("no brief matching %r (see: brief.py list)" % ident)


def parse_brief(path):
    b = {"path": path, "id": path.name[:3], "slug": path.stem[4:], "status": "open",
         "goal": "", "read": [], "run": [], "see": [], "notes": ""}
    section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("Status:"):
            b["status"] = s[7:].strip()
        elif s.startswith("## "):
            section = s[3:].strip().lower()
        elif not s:
            continue
        elif section == "goal":
            b["goal"] = (b["goal"] + " " + s).strip()
        elif section == "read" and s.startswith("- "):
            b["read"].append(s[2:].strip())
        elif section == "done when" and s.startswith("- run:"):
            b["run"].append(s[6:].strip())
        elif section == "done when" and s.startswith("- see:"):
            b["see"].append(s[6:].strip())
        elif section == "notes":
            b["notes"] = (b["notes"] + " " + s).strip()
    return b


def set_status(path, status):
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines):
        if l.startswith("Status:"):
            lines[i] = "Status: " + status
            break
    else:
        lines.insert(1, "Status: " + status)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---- docs files -------------------------------------------------------------

def doc(name):
    return root() / "docs" / name


def section_lines(path, heading):
    """Lines under '## heading' up to the next '## ' heading."""
    if not path.exists():
        return []
    out, on = [], False
    for l in path.read_text(encoding="utf-8").splitlines():
        if l.startswith("## "):
            on = l[3:].strip().lower() == heading.lower()
            continue
        if on and l.strip():
            out.append(l)
    return out


def append_under(path, heading, line):
    """Append a line to the end of the '## heading' section, creating it if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    start = None
    for i, l in enumerate(lines):
        if l.startswith("## ") and l[3:].strip().lower() == heading.lower():
            start = i
            break
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["## " + heading, "", line]
    else:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        ins = end
        while ins > start + 1 and not lines[ins - 1].strip():
            ins -= 1
        new = [line] if ins > start + 1 else ["", line]
        if ins == end and end < len(lines):
            new.append("")
        lines[ins:ins] = new
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_matching(path, heading, substr):
    """Remove the first line in a section containing substr. Returns the line or None."""
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    on = False
    for i, l in enumerate(lines):
        if l.startswith("## "):
            on = l[3:].strip().lower() == heading.lower()
            continue
        if on and substr.lower() in l.lower():
            del lines[i]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return l
    return None
