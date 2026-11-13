"""Finishing commands: animated captions, title templates and export profiles.

`register` wires the commands of captions.py, templates.py and the profile commands below.
"""
from __future__ import annotations

from . import captions, templates
from .spec import PROFILES


def cmd_profiles(a):
    return {"ok": True, "profiles": {name: dict(opts) for name, opts in PROFILES.items()}}


def cmd_export(a):
    from .quick import _abs, _need_file, _out_path, _spec_result
    src = _need_file(a.file)
    out = _out_path(a, [src])
    spec = {"output": {"profile": a.profile}, "timeline": [{"src": _abs(src), "fit": a.fit}]}
    res = _spec_result(spec, out, a)
    res["profile"] = a.profile
    return res


def register(sub):
    captions.register(sub)
    templates.register(sub)

    p = sub.add_parser("profiles", help="list the export profiles")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("export", help="render a file for a platform (reels, youtube, square, whatsapp, ...)")
    p.add_argument("file", metavar="FILE")
    p.add_argument("-o", "--output", help="output file (required)")
    p.add_argument("--profile", required=True, choices=sorted(PROFILES))
    p.add_argument("--fit", default="blur", choices=["blur", "cover", "contain"], help="how the picture fills the frame (default blur)")
    p.set_defaults(func=cmd_export)
