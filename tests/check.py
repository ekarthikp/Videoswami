#!/usr/bin/env python3
"""Orchestrator-owned end-to-end check. Usage: python3 tests/check.py [core|tools|all] (default all).
Exits non-zero on the first failing group; prints PASS/FAIL per assertion."""
import json, os, subprocess, sys, shutil, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
W = ROOT / "tests" / ("work-" + (sys.argv[1] if len(sys.argv) > 1 else "all"))  # separate dir per mode so parallel runs do not collide
FAILS = []

def sh(*a, **k):
    return subprocess.run([str(x) for x in a], capture_output=True, text=True, **k)

def vs(*a, expect_ok=True):
    r = sh(sys.executable, "-m", "videoswami", *a, cwd=ROOT)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        j = json.loads(last)
    except Exception:
        j = {"ok": False, "error": "no JSON on last stdout line: %r | stderr: %s" % (last[:200], r.stderr[-600:])}
    if expect_ok and (r.returncode != 0 or not j.get("ok")):
        print("   command failed:", " ".join(map(str, a)), "\n   ", json.dumps(j)[:600], "\n   ", r.stderr[-400:])
    j["_rc"] = r.returncode
    return j

def ok(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)
    return cond

def probe(p):
    r = sh("ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", p)
    j = json.loads(r.stdout or "{}")
    v = next((s for s in j.get("streams", []) if s["codec_type"] == "video"), None)
    a = next((s for s in j.get("streams", []) if s["codec_type"] == "audio"), None)
    s = next((s for s in j.get("streams", []) if s["codec_type"] == "subtitle"), None)
    dec = sh("ffmpeg", "-v", "error", "-i", p, "-f", "null", "-")
    return {"v": v, "a": a, "s": s, "dur": float(j.get("format", {}).get("duration", 0)), "decodes": dec.returncode == 0 and not dec.stderr.strip(),
            "size": os.path.getsize(p) if os.path.exists(p) else 0}

def rgb(p, t, x=0.5, y=0.5):
    """average colour of a small box centred at fractional x,y of the frame at time t"""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(p), "-frames:v", "1",
                        "-vf", "crop=iw/10:ih/10:iw*%f-iw/20:ih*%f-ih/20,scale=1:1" % (x, y), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    return tuple(r.stdout[:3]) if len(r.stdout) >= 3 else (0, 0, 0)

def near(a, b, tol):
    return abs(a - b) <= tol

def is_col(c, name):
    r, g, b = c
    return {"red": r > 150 and g < 90 and b < 90, "green": g > 100 and r < 90 and b < 90, "blue": b > 150 and r < 90 and g < 90,
            "white": min(c) > 180, "black": max(c) < 40, "gray": max(c) - min(c) < 25}[name]

def mean_volume(p, ss=None, t=None):
    a = ["ffmpeg", "-v", "info"] + (["-ss", str(ss)] if ss is not None else []) + (["-t", str(t)] if t else []) + ["-i", str(p), "-vn", "-af", "volumedetect", "-f", "null", "-"]
    e = subprocess.run(a, capture_output=True, text=True).stderr
    for line in e.splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    return -999.0

def fixtures():
    if W.exists():
        shutil.rmtree(W)
    W.mkdir(parents=True)
    g = lambda *a: subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, a)], check=True)
    # red 640x360 30fps 4s with tone; green 1280x720 25fps 3s NO audio; blue portrait 360x640 30fps 3s with tone
    g("-f", "lavfi", "-i", "color=c=red:s=640x360:r=30", "-f", "lavfi", "-i", "sine=f=440:r=44100", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", W / "red.mp4")
    g("-f", "lavfi", "-i", "color=c=green:s=1280x720:r=25", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", W / "green.mp4")
    g("-f", "lavfi", "-i", "color=c=blue:s=360x640:r=30", "-f", "lavfi", "-i", "sine=f=880:r=48000", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1", W / "blue.mp4")
    g("-f", "lavfi", "-i", "color=c=white:s=100x100", "-frames:v", "1", W / "logo.png")
    g("-f", "lavfi", "-i", "sine=f=220:r=44100", "-t", "2", W / "music.wav")
    # talk: 6s, tone 0-2, silence 2-4, tone 4-6; picture red->green->blue every 2s
    g("-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=2", "-f", "lavfi", "-i", "color=c=green:s=320x180:r=30:d=2", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=2",
      "-f", "lavfi", "-i", "sine=f=500:r=48000:d=6", "-filter_complex", "[0][1][2]concat=n=3[v];[3]volume='if(between(t,2,4),0,1)':eval=frame[a]",
      "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", "-c:a", "aac", W / "talk.mp4")
    (W / "subs.srt").write_text("1\n00:00:00,500 --> 00:00:02,500\nHello Swami\n", encoding="utf-8")

def core():
    p = vs("probe", W / "blue.mp4")
    ok(p.get("ok") and p.get("width") == 360 and p.get("height") == 640 and near(p.get("duration", 0), 3, 0.1) and p.get("has_audio") is True and near(p.get("fps", 0), 30, 0.01), "probe")
    spec = {
        "output": {"width": 640, "height": 360, "fps": 30, "crf": 23, "preset": "veryfast"},
        "timeline": [
            {"src": "red.mp4", "in": 1, "out": 3},
            {"src": "green.mp4", "transition": {"type": "fade", "duration": 1}, "speed": 2},
            {"src": "blue.mp4", "fit": "cover", "volume": 0.5, "fade_out": 0.5},
            {"image": "logo.png", "duration": 2, "fit": "contain", "zoom": {"from": 1, "to": 1.2}},
            {"color": "#0000ff", "duration": 1},
        ],
        "overlays": [
            {"type": "image", "src": "logo.png", "start": 0, "end": 0.8, "x": "left", "y": "top", "margin": 0, "scale": 0.25},
            {"type": "text", "text": "SWAMI", "start": 3, "end": 5, "x": "center", "y": "center", "size": 300, "color": "white", "box": True, "box_color": "white@1"},
        ],
        "audio": [{"src": "music.wav", "volume": 0.5, "loop": True, "fade_out": 1, "duck": True}],
    }
    (W / "edit.json").write_text(json.dumps(spec))
    # red 2s + green 3s/2=1.5s - 1s transition + blue 3s + image 2s + colour 1s = 8.5s
    v = vs("validate", W / "edit.json")
    ok(v.get("ok") and near(v.get("duration", 0), 8.5, 0.01), "validate computes duration 8.5 (got %s)" % v.get("duration"))
    d = vs("render", W / "edit.json", "-o", W / "dry.mp4", "--dry-run")
    ok(d.get("ok") and d.get("commands") and not (W / "dry.mp4").exists(), "--dry-run lists commands and writes nothing")
    r = vs("render", W / "edit.json", "-o", W / "out" / "edit.mp4")
    o = W / "out" / "edit.mp4"
    if ok(r.get("ok") and o.exists(), "render writes the file (creating parent dirs)"):
        q = probe(o)
        ok(q["decodes"] and q["v"] and q["a"], "render: decodes cleanly, has video+audio")
        ok(q["v"]["width"] == 640 and q["v"]["height"] == 360 and q["v"]["r_frame_rate"] in ("30/1", "30"), "render: 640x360 @30")
        ok(near(q["dur"], 8.5, 0.08), "render: duration 8.5 (got %.3f)" % q["dur"])
        ok(q["v"]["pix_fmt"] == "yuv420p" and q["a"]["sample_rate"] == "48000" and q["a"]["channels"] == 2, "render: yuv420p, 48k stereo")
        ok(is_col(rgb(o, 0.5), "red") and is_col(rgb(o, 0.5, 0.1, 0.1), "white"), "render: red clip with logo overlay top-left")
        ok(is_col(rgb(o, 0.93, 0.1, 0.1), "red"), "render: overlay gone after its end")
        ok(is_col(rgb(o, 2.3, 0.5, 0.9), "green"), "render: green after the transition")
        ok(is_col(rgb(o, 3.5, 0.05, 0.5), "blue") and is_col(rgb(o, 3.5), "white"), "render: portrait clip covers the frame, text box in the centre")
        ok(is_col(rgb(o, 6.5), "white") and is_col(rgb(o, 6.5, 0.06, 0.5), "black"), "render: still image contained on black")
        ok(is_col(rgb(o, 8.2), "blue"), "render: colour item last")
        ok(mean_volume(o, 2.2, 0.2) > -50, "render: silent clip section still has music")
    r = vs("render", W / "edit.json", "-o", W / "out" / "edit.mp4", expect_ok=False)
    ok(not r.get("ok") and r["_rc"] != 0, "refuses to overwrite without -y")
    bad = dict(spec); bad["timeline"] = [{"src": "red.mp4", "sped": 2}]
    (W / "bad.json").write_text(json.dumps(bad))
    r = vs("validate", W / "bad.json", expect_ok=False)
    ok(not r.get("ok") and "sped" in r.get("error", ""), "unknown key is rejected by name")
    bad["timeline"] = [{"src": "nope.mp4"}]; (W / "bad.json").write_text(json.dumps(bad))
    r = vs("validate", W / "bad.json", expect_ok=False)
    ok(not r.get("ok") and "nope.mp4" in r.get("error", ""), "missing file is rejected by name")
    # defaults from the first clip, soft subs, vertical blur fit, reverse, h265 in mkv
    (W / "s2.json").write_text(json.dumps({"timeline": [{"src": "red.mp4", "out": 2, "reverse": True, "grayscale": True}], "subtitles": {"file": "subs.srt", "burn": False}}))
    r = vs("render", W / "s2.json", "-o", W / "s2.mp4")
    q = probe(W / "s2.mp4") if r.get("ok") else None
    ok(bool(q) and q["v"]["width"] == 640 and near(q["dur"], 2, 0.08) and q["s"] is not None and is_col(rgb(W / "s2.mp4", 1), "gray"), "defaults from first clip + soft subtitle track + grayscale")
    (W / "s3.json").write_text(json.dumps({"output": {"width": 360, "height": 640, "vcodec": "h265"}, "timeline": [{"src": "red.mp4", "out": 1, "fit": "blur"}], "subtitles": {"file": "subs.srt", "burn": True}}))
    r = vs("render", W / "s3.json", "-o", W / "s3.mkv")
    q = probe(W / "s3.mkv") if r.get("ok") else None
    ok(bool(q) and q["v"]["codec_name"] == "hevc" and q["v"]["width"] == 360 and q["v"]["height"] == 640 and q["decodes"], "h265 mkv, vertical blur fit, burned subtitles")
    b = sh(sys.executable, "build.py", cwd=ROOT)
    z = sh(sys.executable, ROOT / "dist" / "videoswami.pyz", "probe", W / "red.mp4", cwd=W)
    ok(b.returncode == 0 and z.returncode == 0 and '"ok": true' in z.stdout.replace('"ok":true', '"ok": true'), "build.py makes a working dist/videoswami.pyz")

def tools():
    def run(name, args, out, dur=None, tol=0.12, extra=None):
        o = W / "q" / out
        r = vs(*args, "-o", o)
        good = bool(r.get("ok")) and o.exists()
        q = probe(o) if good and o.suffix in (".mp4", ".mkv", ".webm", ".mov") else None
        if q is not None:
            good = q["decodes"] and (dur is None or near(q["dur"], dur, tol))
        if good and extra:
            good = bool(extra(o, q, r))
        ok(good, "%s%s" % (name, "" if good or q is None else " (dur %.2f)" % q["dur"]))
        return o
    T, R, G, B = W / "talk.mp4", W / "red.mp4", W / "green.mp4", W / "blue.mp4"
    i = vs("inspect", T, "--out", W / "insp", "--frames", "6")
    ok(i.get("ok") and os.path.exists(i.get("contact_sheet", "")) and len(i.get("frame_times", [])) == 6 and i.get("probe", {}).get("width") == 320, "inspect writes a contact sheet")
    ok(any(near(s["start"], 2, 0.3) and near(s["end"], 4, 0.3) for s in i.get("silences", [])), "inspect finds the silence 2-4")
    ok(any(near(t, 2, 0.2) for t in i.get("scenes", [])) and any(near(t, 4, 0.2) for t in i.get("scenes", [])), "inspect finds scene cuts at 2 and 4")
    ok(isinstance(i.get("loudness", {}).get("integrated"), (int, float)), "inspect reports loudness")
    f = vs("frames", T, "--at", "1,3,5", "--out", W / "fr")
    ok(f.get("ok") and len(f.get("frames", [])) == 3 and all(os.path.exists(x) for x in f["frames"]), "frames")
    s = vs("silences", T)
    ok(s.get("ok") and len(s.get("silences", [])) == 1 and len(s.get("speech", [])) == 2, "silences + speech ranges")
    run("trim", ["trim", T, "--start", "1", "--end", "3.5"], "trim.mp4", 2.5, extra=lambda o, q, r: is_col(rgb(o, 0.2), "red") and is_col(rgb(o, 2.2), "green"))
    run("trim --fast", ["trim", T, "--start", "2", "--end", "4", "--fast"], "trimf.mp4", 2, tol=0.6)
    run("cut", ["cut", T, "--remove", "2-4"], "cut.mp4", 4, extra=lambda o, q, r: is_col(rgb(o, 1.5), "red") and is_col(rgb(o, 2.5), "blue"))
    run("keep", ["keep", T, "--ranges", "0-1,4-5"], "keep.mp4", 2)
    run("concat mixed sizes", ["concat", R, G, B], "concat.mp4", 10, extra=lambda o, q, r: q["v"]["width"] == 640 and q["a"] and is_col(rgb(o, 5), "green"))
    run("concat transition", ["concat", R, G, "--transition", "fade", "--duration", "1"], "concatx.mp4", 6)
    run("speed", ["speed", R, "--factor", "2"], "speed.mp4", 2)
    run("reverse", ["reverse", T], "rev.mp4", 6, extra=lambda o, q, r: is_col(rgb(o, 1), "blue"))
    run("resize 9:16 blur", ["resize", R, "--aspect", "9:16", "--fit", "blur"], "vert.mp4", 4, extra=lambda o, q, r: q["v"]["width"] * 16 == q["v"]["height"] * 9 or abs(q["v"]["width"] / q["v"]["height"] - 9 / 16) < 0.01)
    run("resize width", ["resize", G, "--width", "320"], "small.mp4", 3, extra=lambda o, q, r: q["v"]["width"] == 320 and q["v"]["height"] == 180)
    run("crop", ["crop", R, "--x", "0", "--y", "0", "--w", "0.5", "--h", "1"], "crop.mp4", 4, extra=lambda o, q, r: q["v"]["width"] == 320)
    run("rotate", ["rotate", R, "--deg", "90"], "rot.mp4", 4, extra=lambda o, q, r: q["v"]["width"] == 360 and q["v"]["height"] == 640)
    run("color grayscale", ["color", R, "--grayscale"], "gray.mp4", 4, extra=lambda o, q, r: is_col(rgb(o, 1), "gray"))
    run("text", ["text", R, "--text", "HI", "--size", "400", "--box", "--color", "white", "--start", "1", "--end", "2"], "text.mp4", 4, extra=lambda o, q, r: is_col(rgb(o, 0.5), "red") and not is_col(rgb(o, 1.5), "red"))
    run("watermark", ["watermark", R, "--image", W / "logo.png", "--x", "right", "--y", "bottom", "--scale", "0.3", "--opacity", "1"], "wm.mp4", 4, extra=lambda o, q, r: is_col(rgb(o, 2, 0.8, 0.7), "white") and is_col(rgb(o, 2, 0.1, 0.1), "red"))
    run("pip", ["pip", R, "--overlay", B, "--x", "left", "--y", "top", "--scale", "0.3"], "pip.mp4", 4, extra=lambda o, q, r: is_col(rgb(o, 1, 0.2, 0.5), "blue") and is_col(rgb(o, 1, 0.9, 0.9), "red"))
    run("music on silent video", ["music", G, "--audio", W / "music.wav"], "mus.mp4", 3, extra=lambda o, q, r: q["a"] is not None and mean_volume(o) > -60)
    run("music --replace", ["music", R, "--audio", W / "music.wav", "--replace"], "musr.mp4", 4, extra=lambda o, q, r: q["a"] is not None)
    run("mute", ["mute", R], "mute.mp4", 4, extra=lambda o, q, r: q["a"] is None or mean_volume(o) < -80)
    run("volume", ["volume", R, "--gain", "0.25"], "vol.mp4", 4, extra=lambda o, q, r: mean_volume(o) < mean_volume(R) - 8)
    run("normalize", ["normalize", R], "norm.mp4", 4)
    run("fade", ["fade", R, "--in", "1", "--out", "1"], "fade.mp4", 4, extra=lambda o, q, r: is_col(rgb(o, 0.03), "black") and is_col(rgb(o, 2), "red"))
    run("subtitles burn", ["subtitles", R, "--srt", W / "subs.srt"], "sub.mp4", 4)
    run("subtitles soft", ["subtitles", R, "--srt", W / "subs.srt", "--soft"], "subs.mp4", 4, extra=lambda o, q, r: q["s"] is not None)
    run("remove-silence", ["remove-silence", T, "--pad", "0.1"], "nosil.mp4", 4.2, tol=0.35, extra=lambda o, q, r: is_col(rgb(o, 3.6), "blue"))
    run("denoise", ["denoise", R], "dn.mp4", 4)
    run("compress target", ["compress", T, "--target-mb", "0.08"], "small2.mp4", 6, extra=lambda o, q, r: q["size"] <= 0.08 * 1024 * 1024)
    run("compress crf+height", ["compress", G, "--crf", "32", "--height", "360"], "small3.mp4", 3, extra=lambda o, q, r: q["v"]["height"] == 360)
    run("gif", ["gif", T, "--start", "0", "--end", "2", "--width", "160"], "a.gif", extra=lambda o, q, r: o.read_bytes()[:3] == b"GIF")
    run("extract-audio", ["extract-audio", R], "a.mp3", extra=lambda o, q, r: os.path.getsize(o) > 2000)
    run("thumbnail", ["thumbnail", R, "--at", "1"], "t.jpg", extra=lambda o, q, r: o.read_bytes()[:2] == b"\xff\xd8")
    run("convert webm", ["convert", R], "c.webm", 4, extra=lambda o, q, r: q["v"]["codec_name"] in ("vp9", "vp8"))
    run("slideshow", ["slideshow", W / "logo.png", W / "logo.png", W / "logo.png", "--each", "2", "--transition", "fade", "--duration", "0.5", "--audio", W / "music.wav"], "slides.mp4", 5, tol=0.2, extra=lambda o, q, r: q["a"] is not None)
    sp = vs("split", T, "--every", "2", "-o", W / "q" / "parts")
    ok(sp.get("ok") and len(sp.get("outputs", [])) == 3 and all(near(probe(x)["dur"], 2, 0.15) for x in sp["outputs"]), "split --every")
    st = vs("stabilize", R, "-o", W / "q" / "stab.mp4")
    ok(st.get("ok") and near(probe(W / "q" / "stab.mp4")["dur"], 4, 0.12), "stabilize")
    d = vs("cut", T, "--remove", "2-4", "-o", W / "q" / "dry.mp4", "--dry-run")
    ok(d.get("ok") and d.get("commands") and not (W / "q" / "dry.mp4").exists(), "quick commands honour --dry-run")
    e = vs("trim", W / "missing.mp4", "--start", "0", "--end", "1", "-o", W / "q" / "x.mp4", expect_ok=False)
    ok(not e.get("ok") and e["_rc"] != 0 and "missing.mp4" in e.get("error", ""), "missing input gives a clean JSON error")

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    fixtures()
    if which in ("core", "all"):
        core()
    if which in ("tools", "all") and not (which == "all" and FAILS):
        tools()
    print("FAIL (%d): %s" % (len(FAILS), "; ".join(FAILS[:6])) if FAILS else "PASS " + which)
    sys.exit(1 if FAILS else 0)
