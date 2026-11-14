"""Command-line entry point: ``python3 -m videoswami <command> ...``.

Stdout carries exactly one JSON object on its last line; progress and logs go to
stderr. Failures print ``{"ok": false, "error": "..."}`` and exit non-zero.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, ff
from .ff import VSError


class _Parser(argparse.ArgumentParser):
    """Argument errors are reported as JSON too, so callers always get a parsable last line."""

    def error(self, message):
        self.print_usage(sys.stderr)
        _emit({"ok": False, "error": "usage: %s" % message})
        sys.exit(2)


def _emit(result):
    sys.stdout.write(json.dumps(result, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()


def _add_globals(parser):
    """Global flags every command accepts (skipping any the command defined itself)."""
    have = parser._option_string_actions
    if "--dry-run" not in have:
        parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                            help="print the ffmpeg command(s) as JSON and write nothing")
    if "-y" not in have and "--overwrite" not in have:
        parser.add_argument("-y", "--overwrite", action="store_true", dest="overwrite", help="replace an existing output file")
    if "--quiet" not in have:
        parser.add_argument("--quiet", action="store_true", help="no progress or log output on stderr")


# --------------------------------------------------------------------------- CORE commands

def cmd_probe(args):
    result = ff.probe(args.file)
    result["ok"] = True
    return result


def cmd_validate(args):
    from .spec import load_spec
    spec = load_spec(args.spec)
    return {"ok": True, "duration": round(spec["duration"], 3), "width": spec["output"]["width"],
            "height": spec["output"]["height"], "fps": round(spec["output"]["fps"], 3), "items": len(spec["timeline"])}


def cmd_render(args):
    from .render import render_spec
    return render_spec(args.spec, args.output, dry_run=args.dry_run, overwrite=args.overwrite)


def build_parser():
    parser = _Parser(prog="videoswami", description="ffmpeg-based video editor driven by JSON edit specs.")
    parser.add_argument("--version", action="version", version="videoswami " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="command", parser_class=_Parser)
    sub.required = True

    p = sub.add_parser("probe", help="describe a media file")
    p.add_argument("file")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("validate", help="check an edit spec and print the output duration")
    p.add_argument("spec")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("render", help="render an edit spec to a video file")
    p.add_argument("spec")
    p.add_argument("-o", "--output", required=True, help="output video file (.mp4 .mov .mkv .webm)")
    p.set_defaults(func=cmd_render)

    # Command modules owned by the TOOLS worker; the core works without them.
    for name in ("analyze", "quick", "speech", "smart", "finish", "reference"):
        try:
            module = __import__("videoswami." + name, fromlist=["register"])
        except ImportError as exc:
            if exc.name not in ("videoswami." + name, name):
                raise
            continue
        module.register(sub)

    for command in sub.choices.values():
        _add_globals(command)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    ff.set_quiet(getattr(args, "quiet", False))
    try:
        result = args.func(args)
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        result.setdefault("ok", True)
        _emit(result)
        return 0
    except VSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        _emit({"ok": False, "error": str(exc)})
    except KeyboardInterrupt:
        _emit({"ok": False, "error": "interrupted"})
        return 130
    except Exception as exc:  # last resort: still honour the JSON contract
        print("internal error: %r" % exc, file=sys.stderr)
        _emit({"ok": False, "error": "internal error: %s: %s" % (type(exc).__name__, exc)})
    return 1


if __name__ == "__main__":
    sys.exit(main())
