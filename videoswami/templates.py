"""Title templates: lower thirds, title cards and friends, drawn as ASS vector shapes and animation tags.

No external assets. Each template turns (text, sub, start, end, accent, color) into ASS styles and
Dialogue lines for an output of a given size; render.py burns them with the `ass` filter.
"""
from __future__ import annotations

from .captions import (ass_colour, ass_doc, ass_style, ass_text, bgr, dialogue, resolve_font)
from .ff import VSError
from .spec import _choice, _color, _keys, _str, _time

DEFAULT_ACCENT = "#FFE600"
TEMPLATE_INFO = [
    {"name": "lower-third", "description": "accent bar with a name and a subtitle line sliding in from the left, bottom-left",
     "fields": ["text", "sub", "start", "end", "accent", "color"]},
    {"name": "title-card", "description": "big centred title scaling and fading in, with a sub line under an accent rule",
     "fields": ["text", "sub", "start", "end", "accent", "color"]},
    {"name": "corner-tag", "description": "small label on an accent pill in the top-left corner",
     "fields": ["text", "start", "end", "accent", "color"]},
    {"name": "end-card", "description": "centred text on a darkened picture",
     "fields": ["text", "sub", "start", "end", "accent", "color"]},
    {"name": "chapter", "description": "chapter label (sub line as number or kicker) wiping in across the top with an accent rule",
     "fields": ["text", "sub", "start", "end", "accent", "color"]},
]
NAMES = tuple(t["name"] for t in TEMPLATE_INFO)
TITLE_KEYS = ["type", "template", "text", "sub", "start", "end", "accent", "color"]


def normalise(item, where):
    """Validate a spec overlay of type 'title'; returns the normalised overlay dict (end None = to the end)."""
    _keys(item, TITLE_KEYS, where)
    o = {"type": "title"}
    o["template"] = _choice(item.get("template"), NAMES, where + ".template")
    o["text"] = _str(item.get("text"), where + ".text")
    o["sub"] = _str(item["sub"], where + ".sub") if item.get("sub") is not None else None
    o["accent"] = _color(item.get("accent", DEFAULT_ACCENT), where + ".accent")
    o["color"] = _color(item.get("color", "white"), where + ".color")
    return o


def _drawing(w, h):
    return "{\\p1}m 0 0 l %d 0 %d %d 0 %d{\\p0}" % (w, w, h, h)


def _fade(start, end, fin, fout):
    span = end - start
    fin, fout = min(fin, span / 3.0), min(fout, span / 3.0)
    return "\\fad(%d,%d)" % (int(fin * 1000), int(fout * 1000))


def build(template, text, sub, start, end, accent, color, width, height):
    """(style lines, Dialogue lines, fonts dir) for one template instance on a width x height picture."""
    if template not in NAMES:
        raise VSError("unknown template %r (try one of %s)" % (template, ", ".join(NAMES)))
    if end <= start:
        raise VSError("title end (%.3f) must be greater than start (%.3f)" % (end, start))
    family, fontsdir = resolve_font(None)
    u = height / 1080.0
    W, H = width, height
    txt, subtxt = ass_text(text), ass_text(sub) if sub else None
    c_main, c_acc = bgr(color), bgr(accent)
    styles, ev = [], []

    def style(name, size, primary, alignment=7, outline=0.0, outline_colour="&H00000000", shadow=0.0, bold=True):
        styles.append(ass_style(name, family, size * u, primary, "&H00FFFFFF", outline_colour, "&H80000000", bold, 1, outline * u, shadow * u,
                                alignment))

    def add(sty, body, layer=0):
        ev.append(dialogue(start, end, sty, body, layer))

    fade = _fade(start, end, 0.25, 0.3)
    if template == "lower-third":
        x0, bar_w, name_h, sub_h = 0.06 * W, 14 * u, 62 * u, 38 * u
        bar_h = name_h * 1.2 + (sub_h * 1.5 if sub else 0)
        y0 = H * 0.9 - bar_h
        xt = x0 + bar_w + 22 * u
        slide = 170 * u
        style("LTName", 62, ass_colour(color), outline=2.0, shadow=1.0)
        style("LTSub", 38, ass_colour(color), outline=1.5, shadow=1.0, bold=False)
        style("LTBar", 20, ass_colour(accent))
        add("LTBar", "{\\an7\\1c&H%s&%s\\move(%.1f,%.1f,%.1f,%.1f,0,450)}%s" % (c_acc, fade, x0 - slide, y0, x0, y0, _drawing(bar_w, bar_h)))
        add("LTName", "{\\an7%s\\move(%.1f,%.1f,%.1f,%.1f,60,520)}%s" % (fade, xt - slide, y0 - 6 * u, xt, y0 - 6 * u, txt), 1)
        if subtxt:
            add("LTSub", "{\\an7%s\\move(%.1f,%.1f,%.1f,%.1f,140,600)}%s" % (fade, xt - slide, y0 + name_h * 1.15, xt, y0 + name_h * 1.15, subtxt), 1)
    elif template == "title-card":
        style("TCTitle", 128, ass_colour(color), alignment=5, outline=3.0, shadow=2.0)
        style("TCSub", 52, ass_colour(accent), alignment=5, outline=1.5, shadow=1.0, bold=False)
        style("TCRule", 20, ass_colour(accent))
        cy = H / 2 - (50 * u if sub else 0)
        add("TCTitle", "{\\an5\\pos(%.1f,%.1f)%s\\fscx72\\fscy72\\t(0,550,\\fscx100\\fscy100)}%s" % (W / 2, cy, _fade(start, end, 0.45, 0.4), txt), 1)
        if subtxt:
            rw, rh = 180 * u, 6 * u
            add("TCRule", "{\\an5\\pos(%.1f,%.1f)\\1c&H%s&%s\\fscx10\\t(150,700,\\fscx100)}%s" % (W / 2, cy + 85 * u, c_acc, _fade(start, end, 0.45, 0.4), _drawing(rw, rh)))
            add("TCSub", "{\\an5\\pos(%.1f,%.1f)%s}%s" % (W / 2, cy + 150 * u, _fade(start, end, 0.7, 0.4), subtxt), 1)
    elif template == "end-card":
        style("ECText", 112, ass_colour(color), alignment=5, outline=3.0, shadow=2.0)
        style("ECSub", 48, ass_colour(accent), alignment=5, outline=1.5, shadow=1.0, bold=False)
        style("ECShade", 20, "&H00000000")
        cy = H / 2 - (40 * u if sub else 0)
        add("ECShade", "{\\an7\\pos(0,0)\\1a&H55&%s}%s" % (_fade(start, end, 0.4, 0.3), _drawing(W, H)))
        add("ECText", "{\\an5\\pos(%.1f,%.1f)%s\\fscx92\\fscy92\\t(0,600,\\fscx100\\fscy100)}%s" % (W / 2, cy, _fade(start, end, 0.5, 0.3), txt), 1)
        if subtxt:
            add("ECSub", "{\\an5\\pos(%.1f,%.1f)%s}%s" % (W / 2, cy + 110 * u, _fade(start, end, 0.8, 0.3), subtxt), 1)
    elif template == "corner-tag":
        pad = 12
        style("CTText", 38, "&H00181818", outline=pad, outline_colour=ass_colour(accent), bold=True)
        add("CTText", "{\\an7%s\\move(%.1f,%.1f,%.1f,%.1f,0,350)}%s" % (_fade(start, end, 0.25, 0.25), 0.04 * W - 80 * u + pad * u, 0.05 * H + pad * u,
                                                                         0.04 * W + pad * u, 0.05 * H + pad * u, txt))
    else:  # chapter
        x0, y0 = 0.06 * W, 0.06 * H
        label_y = y0 + (50 * u if sub else 0)
        style("CHKick", 34, ass_colour(accent), outline=1.5, shadow=1.0)
        style("CHLabel", 74, ass_colour(color), outline=2.5, shadow=1.5)
        style("CHRule", 20, ass_colour(accent))
        wipe = "\\clip(0,0,0,%d)\\t(0,600,\\clip(0,0,%d,%d))" % (H, W, H)
        if subtxt:
            add("CHKick", "{\\an7\\pos(%.1f,%.1f)%s%s}%s" % (x0, y0, wipe, _fade(start, end, 0, 0.3), subtxt), 1)
        add("CHLabel", "{\\an7\\pos(%.1f,%.1f)%s%s}%s" % (x0, label_y, wipe, _fade(start, end, 0, 0.3), txt), 1)
        add("CHRule", "{\\an7\\pos(%.1f,%.1f)\\1c&H%s&%s%s}%s" % (x0, label_y + 96 * u, c_acc, wipe, _fade(start, end, 0, 0.3), _drawing(W * 0.88, 6 * u)))
    return styles, ev, fontsdir


def build_ass(o, width, height, total):
    """ASS text and fonts dir for a normalised spec overlay of type 'title'."""
    end = o["end"] if o["end"] is not None else total
    styles, ev, fontsdir = build(o["template"], o["text"], o["sub"], o["start"], end, o["accent"], o["color"], width, height)
    return ass_doc(width, height, styles, ev), fontsdir


# --------------------------------------------------------------------------- commands

def cmd_templates(a):
    return {"ok": True, "templates": TEMPLATE_INFO}


def cmd_title(a):
    from .quick import _abs, _need_file, _out_path, _spec_result
    src = _need_file(a.file)
    out = _out_path(a, [src])
    start = _time(a.start, "--start")
    end = _time(a.end, "--end") if a.end is not None else start + 4.0
    overlay = {"type": "title", "template": a.template, "text": a.text, "start": start, "end": end}
    if a.sub:
        overlay["sub"] = a.sub
    if a.accent:
        overlay["accent"] = a.accent
    if a.color:
        overlay["color"] = a.color
    res = _spec_result({"timeline": [{"src": _abs(src)}], "overlays": [overlay]}, out, a)
    res["template"] = a.template
    return res


def register(sub):
    p = sub.add_parser("templates", help="list the title templates")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("title", help="burn an animated title template into a video")
    p.add_argument("file", metavar="FILE")
    p.add_argument("-o", "--output", help="output file (required)")
    p.add_argument("--template", required=True, choices=NAMES)
    p.add_argument("--text", required=True)
    p.add_argument("--sub", help="second line")
    p.add_argument("--start", default="0", help="seconds (default 0)")
    p.add_argument("--end", help="seconds (default start + 4)")
    p.add_argument("--accent", help="accent colour (default %s)" % DEFAULT_ACCENT)
    p.add_argument("--color", help="text colour (default white)")
    p.set_defaults(func=cmd_title)
