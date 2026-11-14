#!/usr/bin/env python3
"""Orchestrator-owned check for version 3. Usage: python3 tests/check3.py reference|gallery|all"""
import json, os, subprocess, sys, shutil, pathlib, hashlib, re
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from check import vs, ok, probe, near, mean_volume, FAILS, ROOT

MODE = sys.argv[1] if len(sys.argv) > 1 else "all"
W = ROOT / "tests" / ("work3-" + MODE)

def g(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, a)], check=True)

def stats(p, t=None):
    """mean luma (0..255), mean saturation (0..1), temperature (R-B)/(R+B) over a few frames"""
    d = probe(p)["dur"] or 1
    ts = [t] if t is not None else [d * k / 6 for k in range(1, 6)]
    L = S = R = B = n = 0
    for x in ts:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(x), "-i", str(p), "-frames:v", "1", "-vf", "scale=64:64", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        for i in range(0, len(raw) - 2, 3):
            r, gg, b = raw[i], raw[i + 1], raw[i + 2]
            mx, mn = max(r, gg, b), min(r, gg, b)
            L += 0.299 * r + 0.587 * gg + 0.114 * b; S += (mx - mn) / mx if mx else 0; R += r; B += b; n += 1
    return {"luma": L / n, "sat": S / n, "temp": (R - B) / (R + B) if R + B else 0}

def closer(out, src, ref, key, frac):
    return abs(out[key] - ref[key]) <= abs(src[key] - ref[key]) * frac

def fixtures():
    if W.exists():
        shutil.rmtree(W)
    W.mkdir(parents=True)
    # reference: portrait 540x960 @30, bright, saturated, warm gradients, a cut every second (3 shots), normal loudness
    for i, (c0, c1) in enumerate((("0xFF8A3D", "0xFF4D6D"), ("0xFFD23F", "0xE4572E"), ("0xF72585", "0xFFB703"))):
        g("-f", "lavfi", "-i", "gradients=s=540x960:r=30:d=1:c0=%s:c1=%s:x0=0:y0=0:x1=540:y1=960:speed=0.01" % (c0, c1), "-c:v", "libx264", "-pix_fmt", "yuv420p", W / ("r%d.mp4" % i))
    (W / "list.txt").write_text("".join("file 'r%d.mp4'\n" % i for i in range(3)))
    g("-f", "concat", "-safe", "0", "-i", W / "list.txt", "-f", "lavfi", "-i", "sine=f=330:r=48000:d=3", "-af", "volume=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", "-c:a", "aac", "-shortest", W / "ref.mp4")
    # source: landscape 640x360 @25, dark, washed out, cool, quiet, no cuts
    g("-f", "lavfi", "-i", "gradients=s=640x360:r=25:d=4:c0=0x2E3A48:c1=0x56677A:x0=0:y0=0:x1=640:y1=360:speed=0.01", "-f", "lavfi", "-i", "sine=f=330:r=48000:d=4",
      "-af", "volume=0.05", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", W / "src.mp4")
    g("-ss", "0.5", "-i", W / "ref.mp4", "-frames:v", "1", W / "ref.jpg")

def reference():
    R, S = W / "ref.mp4", W / "src.mp4"
    r = vs("reference", R, "--out", W / "style")
    p = r.get("profile", {})
    ok(r.get("ok") and os.path.exists(r.get("style", "")) and os.path.exists(r.get("contact_sheet", "")), "reference writes style.json and a contact sheet")
    fr, co, pa, au = p.get("frame", {}), p.get("color", {}), p.get("pacing", {}), p.get("audio", {})
    ok(fr.get("aspect") == "9:16" and near(fr.get("fps", 0), 30, 0.1) and fr.get("width") == 540, "profile.frame (aspect %s)" % fr.get("aspect"))
    mine = stats(R)
    ok(near(co.get("luma_mean", -99), mine["luma"], 14) and near(co.get("saturation", -9), mine["sat"], 0.12) and co.get("temperature", 0) > 0.03 and co.get("contrast", 0) > 8, "profile.color agrees with an independent measurement")
    ok(pa.get("cuts") == 2 and near(pa.get("avg_shot", 0), 1.0, 0.15), "profile.pacing: 2 cuts, 1 s shots (%s)" % pa)
    ok(isinstance(au.get("integrated"), (int, float)), "profile.audio loudness")
    ri = vs("reference", W / "ref.jpg", "--out", W / "style-img")
    ok(ri.get("ok") and ri.get("profile", {}).get("kind") == "image" and ri["profile"].get("color", {}).get("temperature", 0) > 0.03, "reference accepts a still image")
    src, ref = stats(S), stats(R)
    o = W / "o" / "matched.mp4"
    m = vs("match", S, "--reference", R, "-o", o)
    if ok(m.get("ok") and o.exists(), "match renders"):
        q = probe(o); out = stats(o)
        ok(q["v"]["width"] * 16 == q["v"]["height"] * 9 and q["v"]["r_frame_rate"] == "30/1" and near(q["dur"], 4, 0.12), "match: frame and fps follow the reference (%sx%s)" % (q["v"]["width"], q["v"]["height"]))
        ok(closer(out, src, ref, "luma", 0.45), "match: brightness lands near the reference (src %.0f, out %.0f, ref %.0f)" % (src["luma"], out["luma"], ref["luma"]))
        ok(closer(out, src, ref, "sat", 0.5), "match: saturation lands near the reference (src %.2f, out %.2f, ref %.2f)" % (src["sat"], out["sat"], ref["sat"]))
        ok(out["temp"] > src["temp"] + 0.04, "match: colour temperature moves warm (src %.3f, out %.3f, ref %.3f)" % (src["temp"], out["temp"], ref["temp"]))
        ok(abs(mean_volume(o) - mean_volume(R)) < 5 and mean_volume(o) > mean_volume(S) + 10, "match: loudness follows the reference")
        ok(isinstance(m.get("applied", {}).get("color"), dict) and m["applied"].get("frame") and "pacing_hint" in m and m.get("after", {}).get("color"), "match reports what it applied and a pacing hint")
    o2 = W / "o" / "color-only.mp4"
    m2 = vs("match", S, "--reference", W / "style" / "style.json", "--only", "color", "-o", o2)
    if ok(m2.get("ok") and o2.exists(), "match --only color from a saved style.json"):
        q = probe(o2); out = stats(o2)
        ok(q["v"]["width"] == 640 and q["v"]["r_frame_rate"] == "25/1" and closer(out, src, ref, "luma", 0.45) and abs(mean_volume(o2) - mean_volume(S)) < 3, "--only color leaves frame, fps and sound alone")
    o3 = W / "o" / "half.mp4"
    m3 = vs("match", S, "--reference", R, "--only", "color", "--strength", "0.5", "-o", o3)
    if ok(m3.get("ok"), "match --strength 0.5"):
        h = stats(o3); full = stats(o2)
        ok(src["luma"] + 5 < h["luma"] < full["luma"] - 5, "strength 0.5 lands between source and full match (%.0f < %.0f < %.0f)" % (src["luma"], h["luma"], full["luma"]))
    (W / "look.json").write_text(json.dumps({"output": {"preset": "veryfast"}, "timeline": [{"src": "src.mp4", "out": 2}], "look": {"reference": "ref.mp4"}}))
    r = vs("render", W / "look.json", "-o", W / "o" / "look.mp4")
    ok(r.get("ok") and closer(stats(W / "o" / "look.mp4"), src, ref, "luma", 0.5) and probe(W / "o" / "look.mp4")["v"]["width"] == 640, "edit spec `look.reference` grades the timeline")
    e = vs("match", S, "--reference", W / "nope.mp4", "-o", W / "o" / "x.mp4", expect_ok=False)
    ok(not e.get("ok") and "nope.mp4" in e.get("error", ""), "missing reference is a clean error")
    env = dict(os.environ, VIDEOSWAMI_NO_NUMPY="1")
    z = subprocess.run([sys.executable, "-m", "videoswami", "reference", str(R), "--out", str(W / "style-py"), "--quiet"], capture_output=True, text=True, cwd=ROOT, env=env)
    try:
        zp = json.loads(z.stdout.strip().splitlines()[-1])["profile"]["color"]
    except Exception:
        zp = {}
    ok(near(zp.get("luma_mean", -99), co.get("luma_mean", 99), 3), "pure-Python fallback gives the same colour profile")
    reg = subprocess.run([sys.executable, str(ROOT / "tests" / "check.py"), "core"], capture_output=True, text=True)
    ok(reg.returncode == 0, "v1 core check still passes")

def gallery():
    out = ROOT / "docs" / "gallery"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_gallery.py"), "--out", str(out)], capture_output=True, text=True, cwd=ROOT)
    ok(r.returncode == 0, "tools/make_gallery.py runs" + ("" if r.returncode == 0 else ": " + r.stderr[-400:]))
    want = ["captions-%s.jpg" % s for s in ("pop", "highlight", "karaoke", "box", "classic", "minimal")] + \
           ["title-%s.jpg" % s for s in ("lower-third", "title-card", "corner-tag", "end-card", "chapter")] + \
           ["fit-%s.jpg" % s for s in ("contain", "cover", "stretch", "blur")] + \
           ["transition-%s.jpg" % s for s in ("fade", "wipeleft", "slideup", "circleopen", "dissolve")] + \
           ["animate-%s.jpg" % s for s in ("fade", "slide-up", "pop")] + \
           ["look-%s.jpg" % s for s in ("grayscale", "vignette", "warm", "cool", "blur", "sharpen")] + \
           ["profile-%s.jpg" % s for s in ("reels", "shorts", "tiktok", "youtube", "youtube-4k", "square", "portrait", "whatsapp", "x")] + ["contact.jpg"]
    missing = [w for w in want if not (out / w).exists() or (out / w).stat().st_size < 3000]
    ok(not missing, "all %d gallery pictures exist (missing: %s)" % (len(want), missing[:6]))
    if not missing:
        dims = {w: (lambda v: (v["width"], v["height"]))(probe(out / w)["v"]) for w in want}
        ok(all(d[0] <= 1700 and d[0] >= 300 for d in dims.values()) and dims["fit-blur.jpg"][1] > dims["fit-blur.jpg"][0] and dims["profile-square.jpg"][0] == dims["profile-square.jpg"][1], "picture sizes are sensible and shapes are right")
        def sig(p):
            return subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-vf", "scale=24:24", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
        sigs = {w: sig(out / w) for w in want}
        def spread(b):
            m = sum(b) / len(b); return (sum((x - m) ** 2 for x in b) / len(b)) ** 0.5
        ok(all(spread(s) > 6 for s in sigs.values()), "no picture is blank")
        def diff(a, b):
            return sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        for grp in ("captions-", "title-", "fit-", "transition-", "look-"):
            names = [w for w in want if w.startswith(grp)]
            same = [(a, b) for i, a in enumerate(names) for b in names[i + 1:] if len(sigs[a]) == len(sigs[b]) and diff(sigs[a], sigs[b]) < 0.6]
            ok(not same, "%s pictures differ from each other %s" % (grp, same[:2]))
        base = sigs["look-grayscale.jpg"]
        size_total = sum((out / w).stat().st_size for w in want)
        ok(size_total < 6_000_000, "gallery is under 6 MB (%.1f MB)" % (size_total / 1e6))
    md = ROOT / "docs" / "VISUAL-GUIDE.md"
    text = md.read_text() if md.exists() else ""
    refs = re.findall(r"\]\((gallery/[^)]+)\)", text)
    ok(text and all(("gallery/" + w) in refs for w in want) and all((ROOT / "docs" / x).exists() for x in refs), "VISUAL-GUIDE.md shows every picture and every link resolves")
    ok(text.count("```") >= 14 and "captions" in text and "--style highlight" in text and "transition" in text, "VISUAL-GUIDE.md gives the command or spec for each look")

if __name__ == "__main__":
    fixtures()
    for name, fn in (("reference", reference), ("gallery", gallery)):
        if MODE in (name, "all"):
            fn()
    print("FAIL (%d): %s" % (len(FAILS), "; ".join(FAILS[:6])) if FAILS else "PASS " + MODE)
    sys.exit(1 if FAILS else 0)
