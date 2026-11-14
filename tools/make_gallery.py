#!/usr/bin/env python3
"""Render a picture of every VideoSwami look into docs/gallery/ and write docs/VISUAL-GUIDE.md.

Usage: python3 tools/make_gallery.py [--out docs/gallery]
Everything is made with the VideoSwami CLI on a generated demo clip (ffmpeg lavfi only). Re-runnable.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT = next((f for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                         "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                         "C:/Windows/Fonts/arialbd.ttf") if os.path.exists(f)), None)
JQ = "5"  # ffmpeg mjpeg quality (2 best .. 31 worst); about q80

CAPTION_STYLES = ["pop", "highlight", "karaoke", "box", "classic", "minimal"]
TEMPLATES = ["lower-third", "title-card", "corner-tag", "end-card", "chapter"]
FITS = ["contain", "cover", "stretch", "blur"]
TRANSITIONS = ["fade", "wipeleft", "slideup", "circleopen", "dissolve"]
ANIMS = ["fade", "slide-up", "pop"]
LOOKS = ["grayscale", "vignette", "warm", "cool", "blur", "sharpen"]
PROFILES = ["reels", "shorts", "tiktok", "youtube", "youtube-4k", "square", "portrait", "whatsapp", "x"]
VERTICAL = {"reels", "shorts", "tiktok", "portrait"}

WORDS = "Hello everyone and welcome to the show today we will make video editing simple".split()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def ff(*args):
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], capture_output=True, text=True)
    if p.returncode:
        raise SystemExit("ffmpeg failed: " + p.stderr[-600:])


def vs(*args):
    """Run the CLI from the repo root. Retries on import/internal errors (another worker may be mid-edit)."""
    cmd = [sys.executable, "-m", "videoswami", *map(str, args), "--quiet"]
    last = ""
    for attempt in range(9):
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
        try:
            res = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            res = None
        if p.returncode == 0 and res and res.get("ok"):
            return res
        last = (p.stderr or p.stdout)[-500:]
        transient = res is None or "Traceback" in p.stderr or "Error" in p.stderr and "Import" in p.stderr
        if not transient or attempt == 8:
            break
        log("videoswami failed (%s), retrying in 60 s" % last.strip().splitlines()[-1:])
        time.sleep(60)
    raise SystemExit("videoswami %s failed: %s" % (" ".join(map(str, args[:2])), last))


def make_demo(path, grad, seconds=6, size="1280x720"):
    """Footage-like clip: soft two-colour gradient, a drawn presenter, a few drifting shapes, quiet tone."""
    c0, c1 = grad
    w, h = map(int, size.split("x"))
    S = h / 720
    def n(v):
        return int(round(v * S))
    head, body = n(110), n(300)
    circ = "geq=lum='255':a='if(lt(hypot(X-W/2,Y-H/2),W/2),255,0)',format=yuva420p"
    fc = (
        "[0:v]format=yuv420p[bg];"
        "color=c=0x2B3550:s={bw}x{bh}:r=30,format=yuva420p,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':"
        "a='if(lt(hypot((X-W/2)/(W/2),(Y-H)/(H*0.95)),1),255,0)'[torso];"
        "color=c=0xF1C6A0:s={hs}x{hs}:r=30,format=yuva420p,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':"
        "a='if(lt(hypot(X-W/2,Y-H/2),W/2),255,0)'[head];"
        "color=c=0xFFFFFF:s={d}x{d}:r=30,format=yuva420p,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':"
        "a='if(lt(hypot(X-W/2,Y-H/2),W/2),110,0)'[dot];"
        "[dot]split=3[d1][d2][d3];"
        "[bg][d1]overlay=x='{w}*0.10+{w}*0.12*sin(t*1.3)':y='{h}*0.18+{h}*0.10*sin(t*0.9)':shortest=0[a];"
        "[a][d2]overlay=x='{w}*0.82+{w}*0.10*sin(t*1.1+1)':y='{h}*0.30+{h}*0.12*sin(t*1.4)':shortest=0[b];"
        "[b][d3]overlay=x='{w}*0.70+{w}*0.12*sin(t*0.8+2)':y='{h}*0.12+{h}*0.10*sin(t*1.7)':shortest=0[c];"
        "[c][torso]overlay=x='(W-w)/2+{w}*0.01*sin(t*1.5)':y=H-h+{bh}*0.08:shortest=0[d];"
        "[d][head]overlay=x='(W-w)/2+{w}*0.01*sin(t*1.5)':y='H-{bh}*0.92-{hs}*0.5+{h}*0.01*sin(t*3)':shortest=0[v]"
    ).format(w=w, h=h, bw=n(520), bh=body + n(100), hs=head, d=n(100))
    # note: the torso is a wide ellipse (shoulders) whose lower part runs off the frame
    ff("-f", "lavfi", "-i", "gradients=s=%s:r=30:d=%s:c0=%s:c1=%s:x0=0:y0=0:x1=%d:y1=%d:speed=0.012" % (size, seconds, c0, c1, w, h),
       "-f", "lavfi", "-i", "sine=f=220:r=48000:d=%s" % seconds,
       "-filter_complex", fc, "-map", "[v]", "-map", "1:a", "-af", "volume=0.1", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-shortest", path)


def frame(video, t, dest, width):
    """Exact frame -> JPG through the CLI."""
    d = Path(tempfile.mkdtemp(dir=dest.parent))
    r = vs("frames", video, "--at", t, "--out", d, "--width", width, "-y")
    src = Path(r["frames"][0])
    ff("-i", src, "-q:v", JQ, dest)
    shutil.rmtree(d, ignore_errors=True)


def write_spec(path, spec):
    path.write_text(json.dumps(spec, indent=2))
    return path


def fmt_json(spec):
    return json.dumps(spec, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="docs/gallery")
    a = ap.parse_args()
    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    if out.exists():
        for f in out.glob("*.jpg"):
            f.unlink()
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="vs-gallery-"))
    try:
        build(out, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    log("done in %.0f s" % (time.time() - t0))


def build(out, tmp):
    demo, demo2 = tmp / "demo.mp4", tmp / "demo2.mp4"
    make_demo(demo, ("0x7FB3D5", "0xF5CBA7"))
    make_demo(demo2, ("0x1B4F72", "0x7D3C98"))
    # transcript fixture
    segs, t, words = [], 0.4, []
    for i, w in enumerate(WORDS):
        words.append({"w": w, "start": round(t, 2), "end": round(t + 0.38, 2)})
        t += 0.42
    segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": " ".join(WORDS), "words": words})
    tr = tmp / "demo.transcript.json"
    tr.write_text(json.dumps({"language": "en", "duration": 6.0, "source": "demo.mp4", "segments": segs}))

    entries = []  # (group, name, jpg, caption, code, lang)

    # captions: frame while the 4th-6th words are spoken
    for ci, s in enumerate(CAPTION_STYLES):
        log("captions", s)
        o = tmp / ("cap-%s.mp4" % s)
        vs("captions", demo, "--transcript", tr, "--style", s, "--size", 96, "-o", o, "-y")
        frame(o, 1.6 + 0.65 * ci, out / ("captions-%s.jpg" % s), 800)
        entries.append(("captions", s, "captions-%s.jpg" % s, s,
                        "python3 -m videoswami captions demo.mp4 --transcript demo.transcript.json --style %s -o out.mp4" % s, "bash"))

    for ti, tpl in enumerate(TEMPLATES):
        log("title", tpl)
        o = tmp / ("title-%s.mp4" % tpl)
        text, sub = {"lower-third": ("Alex Rivera", "Video editor"), "title-card": ("Summer Highlights", "Made with VideoSwami"),
                     "corner-tag": ("LIVE", None), "end-card": ("Thanks for watching", None), "chapter": ("1", "The setup")}[tpl]
        args = ["title", demo, "--template", tpl, "--text", text, "--start", 0.5, "--end", 5.5]
        cmd = "python3 -m videoswami title demo.mp4 --template %s --text \"%s\"" % (tpl, text)
        if sub:
            args += ["--sub", sub]
            cmd += " --sub \"%s\"" % sub
        cmd += " --start 0.5 --end 5.5 -o out.mp4"
        vs(*args, "-o", o, "-y")
        frame(o, 1.5 + 0.8 * ti, out / ("title-%s.jpg" % tpl), 800)
        entries.append(("title", tpl, "title-%s.jpg" % tpl, tpl, cmd, "bash"))

    for fit in FITS:
        log("fit", fit)
        spec = {"output": {"width": 540, "height": 960, "fps": 30, "preset": "veryfast"},
                "timeline": [{"src": "demo.mp4", "out": 3, "fit": fit}]}
        sp = write_spec(tmp / ("fit-%s.json" % fit), spec)
        o = tmp / ("fit-%s.mp4" % fit)
        vs("render", sp, "-o", o, "-y")
        frame(o, 1.5, out / ("fit-%s.jpg" % fit), 360)
        shown = {"output": {"width": 1080, "height": 1920}, "timeline": [{"src": "demo.mp4", "fit": fit}]}
        entries.append(("fit", fit, "fit-%s.jpg" % fit, fit, fmt_json(shown), "json"))

    for tr_name in TRANSITIONS:
        log("transition", tr_name)
        spec = {"output": {"width": 640, "height": 360, "fps": 30, "preset": "veryfast"},
                "timeline": [{"src": "demo.mp4", "out": 3},
                             {"src": "demo2.mp4", "out": 3, "transition": {"type": tr_name, "duration": 1.0}}]}
        sp = write_spec(tmp / ("tr-%s.json" % tr_name), spec)
        o = tmp / ("tr-%s.mp4" % tr_name)
        vs("render", sp, "-o", o, "-y")
        frame(o, 2.25 if tr_name == "dissolve" else 2.5, out / ("transition-%s.jpg" % tr_name), 640)
        shown = {"timeline": [{"src": "a.mp4", "out": 3},
                              {"src": "b.mp4", "out": 3, "transition": {"type": tr_name, "duration": 1.0}}]}
        entries.append(("transition", tr_name, "transition-%s.jpg" % tr_name, tr_name, fmt_json(shown), "json"))

    for an in ANIMS:
        log("animate", an)
        ov = {"type": "text", "text": "Animated text", "start": 1, "end": 5, "x": "center", "y": "center",
              "size": 96, "color": "white", "bold": True, "outline": 3, "animate": {"in": an, "duration": 1.0}}
        spec = {"output": {"width": 640, "height": 360, "fps": 30, "preset": "veryfast"},
                "timeline": [{"src": "demo.mp4", "out": 4}], "overlays": [ov]}
        sp = write_spec(tmp / ("an-%s.json" % an), spec)
        o = tmp / ("an-%s.mp4" % an)
        vs("render", sp, "-o", o, "-y")
        frame(o, 1.45, out / ("animate-%s.jpg" % an), 640)
        shown = {"timeline": [{"src": "demo.mp4"}], "overlays": [ov]}
        entries.append(("animate", an, "animate-%s.jpg" % an, an, fmt_json(shown), "json"))

    look_items = {"grayscale": {"grayscale": True, "color": {"contrast": 1.3}}, "vignette": {"vignette": True},
                  "warm": {"color": {"temperature": 0.6}}, "cool": {"color": {"temperature": -0.6}},
                  "blur": {"blur": 20}, "sharpen": {"sharpen": 1.5}}
    look_cmd = {"grayscale": "python3 -m videoswami color demo.mp4 --grayscale --contrast 1.3 -o out.mp4",
                "warm": "python3 -m videoswami color demo.mp4 --temperature 0.6 -o out.mp4",
                "cool": "python3 -m videoswami color demo.mp4 --temperature -0.6 -o out.mp4"}
    for li, lk in enumerate(LOOKS):
        log("look", lk)
        item = {"src": "demo.mp4", "out": 3}
        item.update(look_items[lk])
        spec = {"output": {"width": 640, "height": 360, "fps": 30, "preset": "veryfast"}, "timeline": [item]}
        sp = write_spec(tmp / ("lk-%s.json" % lk), spec)
        o = tmp / ("lk-%s.mp4" % lk)
        vs("render", sp, "-o", o, "-y")
        frame(o, 0.8 + 0.4 * li, out / ("look-%s.jpg" % lk), 640)
        if lk in look_cmd:
            entries.append(("look", lk, "look-%s.jpg" % lk, lk, look_cmd[lk], "bash"))
        else:
            shown = {"timeline": [dict(src="demo.mp4", **look_items[lk])]}
            entries.append(("look", lk, "look-%s.jpg" % lk, lk, fmt_json(shown), "json"))

    short = tmp / "short.mp4"
    vs("trim", demo, "--start", 0, "--end", 2, "-o", short, "-y")
    for pr in PROFILES:
        log("profile", pr)
        o = tmp / ("pr-%s.mp4" % pr)
        vs("export", short, "--profile", pr, "-o", o, "-y")
        frame(o, 1.0, out / ("profile-%s.jpg" % pr), 360 if pr in VERTICAL else 640)
        entries.append(("profile", pr, "profile-%s.jpg" % pr, pr,
                        "python3 -m videoswami export demo.mp4 --profile %s -o out.mp4" % pr, "bash"))

    contact(out, entries, tmp)
    guide(out, entries)


def contact(out, entries, tmp):
    log("contact sheet")
    cell_w, cell_h, lab = 400, 360, 34
    cells = []
    for i, (grp, name, jpg, _c, _code, _l) in enumerate(entries):
        cell = tmp / ("cell%03d.png" % i)
        label = "%s / %s" % (grp, name)
        vf = "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:%d-ih:color=0x20232A" % (
            cell_w - 8, cell_h - lab - 14, cell_w, cell_h, cell_h - lab)
        if FONT:
            vf += ",drawtext=fontfile=%s:text='%s':fontcolor=white:fontsize=20:x=(w-text_w)/2:y=h-%d" % (
                FONT, label.replace("'", ""), lab - 6)
        ff("-i", out / jpg, "-vf", vf, "-frames:v", "1", cell)
        cells.append(cell)
    cols = 4
    rows = (len(cells) + cols - 1) // cols
    args = []
    for c in cells:
        args += ["-i", c]
    for _ in range(rows * cols - len(cells)):
        args += ["-f", "lavfi", "-i", "color=c=0x20232A:s=%dx%d:d=1" % (cell_w, cell_h)]
    n = rows * cols
    layout = "|".join("%d_%d" % ((k % cols) * cell_w, (k // cols) * cell_h) for k in range(n))
    fc = "".join("[%d:v]" % k for k in range(n)) + "xstack=inputs=%d:layout=%s:fill=0x20232A" % (n, layout)
    ff(*args, "-filter_complex", fc, "-frames:v", "1", "-q:v", "4", out / "contact.jpg")


TITLES = {
    "captions": ("Captions", "Animated word-by-word captions, burned in. Each look is one `--style` value."),
    "title": ("Title templates", "Ready-made animated titles and name tags. No image files needed."),
    "fit": ("Fitting wide video into a tall frame", "A 16:9 clip placed in a 9:16 frame. `fit` decides what happens to the edges."),
    "transition": ("Transitions", "The frame in the middle of a one-second transition between two clips. Any ffmpeg `xfade` name works."),
    "animate": ("Text animation", "The frame halfway through the text's entrance. Use `animate.in` and `animate.out` on any overlay."),
    "look": ("Looks and colour", "Quick changes to how the picture feels."),
    "profile": ("Export profiles", "One picture per platform preset, showing the shape of the finished video."),
}
NOTES = {
    "fit": {"contain": "Whole picture visible, empty bars (black here) above and below.",
            "cover": "Fills the frame; the sides are cut off.",
            "stretch": "Fills the frame by squashing the picture. Rarely what you want.",
            "blur": "Whole picture visible over a blurred copy of itself."},
    "profile": {"reels": "1080x1920, 30 fps, loudness normalised.", "shorts": "1080x1920, 30 fps, loudness normalised.",
                "tiktok": "1080x1920, 30 fps, loudness normalised.", "youtube": "1920x1080.", "youtube-4k": "3840x2160.",
                "square": "1080x1080.", "portrait": "1080x1350.", "whatsapp": "1280x720 at most, small file.", "x": "1280x720."},
}


def guide(out, entries):
    md = ["# Visual guide", "",
          "A picture of every look VideoSwami offers, made by VideoSwami itself on a generated demo clip "
          "(a presenter on a soft gradient). Under each picture is the command or edit spec that makes it.",
          "", "Regenerate everything with `python3 tools/make_gallery.py`.", "",
          "![Everything on one sheet](gallery/contact.jpg)", ""]
    groups = []
    for e in entries:
        if e[0] not in groups:
            groups.append(e[0])
    for g in groups:
        title, intro = TITLES[g]
        md += ["## " + title, "", intro, ""]
        for grp, name, jpg, cap, code, lang in entries:
            if grp != g:
                continue
            md += ["### " + name, "", "![%s %s](gallery/%s)" % (g, name, jpg), ""]
            note = NOTES.get(g, {}).get(name)
            if note:
                md += [note, ""]
            md += ["```" + lang, code, "```", ""]
    (out.parent / "VISUAL-GUIDE.md").write_text("\n".join(md))


if __name__ == "__main__":
    main()
