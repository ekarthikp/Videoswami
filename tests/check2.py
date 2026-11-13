#!/usr/bin/env python3
"""Orchestrator-owned check for version 2. Usage: python3 tests/check2.py speech|smart|finish|all"""
import json, os, subprocess, sys, shutil, pathlib, stat

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check as C  # reuse helpers from the v1 check
from check import vs, ok, probe, rgb, near, is_col, mean_volume, FAILS, ROOT

MODE = sys.argv[1] if len(sys.argv) > 1 else "all"
W = ROOT / "tests" / ("work2-" + MODE)

def g(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, a)], check=True)

def pixels(p, t, w=320):
    """list of (r,g,b) for the frame at t, scaled to w wide; also returns (w,h)"""
    q = probe(p)["v"]; h = max(2, round(w * q["height"] / q["width"]))
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(p), "-frames:v", "1", "-vf", "scale=%d:%d" % (w, h), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return [(r[i], r[i + 1], r[i + 2]) for i in range(0, len(r) - 2, 3)], (w, h)

def count(p, t, pred, region=None):
    px, (w, h) = pixels(p, t)
    n = 0
    for i, c in enumerate(px):
        x, y = (i % w) / w, (i // w) / h
        if region and not (region[0] <= x <= region[2] and region[1] <= y <= region[3]):
            continue
        if pred(c):
            n += 1
    return n

bright = lambda c: min(c) > 170
greenish = lambda c: c[1] > 150 and c[0] < 110 and c[2] < 110
yellowish = lambda c: c[0] > 150 and c[1] > 150 and c[2] < 110
luma = lambda p, t: (lambda px: sum(sum(c) for c in px) / (3 * len(px)))(pixels(p, t)[0])

TRANSCRIPT = {"language": "en", "duration": 8.0, "source": "talk8.mp4", "segments": [
    {"start": 0.2, "end": 1.3, "text": "Hello everyone", "words": [{"w": "Hello", "start": 0.2, "end": 0.6}, {"w": "everyone", "start": 0.7, "end": 1.3}]},
    {"start": 2.2, "end": 3.7, "text": "um this is the", "words": [{"w": "um", "start": 2.2, "end": 2.6}, {"w": "this", "start": 2.8, "end": 3.1}, {"w": "is", "start": 3.2, "end": 3.4}, {"w": "the", "start": 3.5, "end": 3.7}]},
    {"start": 4.2, "end": 5.5, "text": "pricing part.", "words": [{"w": "pricing", "start": 4.2, "end": 4.9}, {"w": "part.", "start": 5.0, "end": 5.5}]},
    {"start": 6.5, "end": 7.2, "text": "Goodbye!", "words": [{"w": "Goodbye!", "start": 6.5, "end": 7.2}]}]}

def fixtures():
    if W.exists():
        shutil.rmtree(W)
    W.mkdir(parents=True)
    # talk8: red 0-2, green 2-4, blue 4-6, white 6-8, with a tone
    g(*sum([["-f", "lavfi", "-i", "color=c=%s:s=320x180:r=30:d=2" % c] for c in ("red", "green", "blue", "white")], []), "-f", "lavfi", "-i", "sine=f=400:r=48000:d=8",
      "-filter_complex", "[0][1][2][3]concat=n=4[v]", "-map", "[v]", "-map", "4:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "15", "-c:a", "aac", W / "talk8.mp4")
    (W / "t.json").write_text(json.dumps(TRANSCRIPT))
    g("-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=8", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", W / "black.mp4")
    # stub whisper backend: writes openai-whisper style JSON
    stub = W / "stub.py"
    ow = {"text": " Hello everyone um this is the pricing part. Goodbye!", "language": "en", "segments": [
        {"start": s["start"], "end": s["end"], "text": " " + s["text"], "words": [{"word": " " + w["w"], "start": w["start"], "end": w["end"]} for w in s["words"]]} for s in TRANSCRIPT["segments"]]}
    stub.write_text("import sys, json, os\nassert os.path.getsize(sys.argv[1]) > 1000, 'audio missing'\njson.dump(%r, open(sys.argv[2], 'w'))\n" % (ow,))
    # mover: white box travels left -> right on black, 640x360, 4s
    g("-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=4", "-f", "lavfi", "-i", "color=c=white:s=80x80:r=30:d=4", "-filter_complex", "[0][1]overlay=x='20+t*125':y=140", "-c:v", "libx264", "-pix_fmt", "yuv420p", W / "mover.mp4")
    # event: 12s, static dark + silence, except 5-8 where the picture flickers colours and a loud tone plays
    g("-f", "lavfi", "-i", "color=c=0x202020:s=320x180:r=30:d=12", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=12", "-f", "lavfi", "-i", "sine=f=600:r=48000:d=12",
      "-filter_complex", "[0][1]overlay=enable='between(t,5,8)'[v];[2]volume='if(between(t,5,8),1,0.001)':eval=frame[a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", W / "event.mp4")
    # click track: 120 bpm for 8s (short 1 kHz bursts every 0.5s)
    g("-f", "lavfi", "-i", "sine=f=1000:r=44100:d=8", "-af", "volume='if(lt(mod(t,0.5),0.04),1,0)':eval=frame", W / "click.wav")
    for c in ("red", "green", "blue"):
        g("-f", "lavfi", "-i", "color=c=%s:s=320x180:r=30:d=5" % c, "-c:v", "libx264", "-pix_fmt", "yuv420p", W / (c + ".mp4"))
    # dull: dark low-contrast picture + very quiet voice-band tone
    g("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=3", "-f", "lavfi", "-i", "sine=f=300:r=48000:d=3", "-vf", "eq=contrast=0.4:brightness=-0.25", "-af", "volume=0.02", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", W / "dull.mp4")
    # halves: left red, right blue, 640x360, 3s
    g("-f", "lavfi", "-i", "color=c=red:s=320x360:r=30:d=3", "-f", "lavfi", "-i", "color=c=blue:s=320x360:r=30:d=3", "-filter_complex", "[0][1]hstack", "-c:v", "libx264", "-pix_fmt", "yuv420p", W / "halves.mp4")
    g("-f", "lavfi", "-i", "color=c=white:s=100x100", "-frames:v", "1", W / "logo.png")

def speech():
    T, tj = W / "talk8.mp4", W / "t.json"
    env_cmd = "%s %s {audio} {out}" % (sys.executable, W / "stub.py")
    os.environ["VIDEOSWAMI_WHISPER_CMD"] = env_cmd
    r = vs("transcribe", T, "--srt", "--txt")
    side = W / "talk8.transcript.json"
    good = r.get("ok") and side.exists() and r.get("word_count") == 9 and r.get("backend") == "cmd" and not r.get("cached")
    if good:
        t = json.loads(side.read_text())
        good = t["segments"][0]["words"][0] == {"w": "Hello", "start": 0.2, "end": 0.6} and t["segments"][2]["words"][1]["w"] == "part."
    ok(good, "transcribe via command backend writes a normalised sidecar transcript")
    ok((W / "talk8.srt").exists() and "-->" in (W / "talk8.srt").read_text() and "Hello everyone" in (W / "talk8.txt").read_text(), "transcribe --srt --txt")
    r = vs("transcribe", T)
    ok(r.get("ok") and r.get("cached") is True, "transcribe reuses the sidecar (cached)")
    del os.environ["VIDEOSWAMI_WHISPER_CMD"]
    f = vs("find", tj, "--text", "Pricing PART")
    ok(f.get("ok") and len(f.get("matches", [])) == 1 and near(f["matches"][0]["start"], 4.2, 0.01) and near(f["matches"][0]["end"], 5.5, 0.01), "find phrase, case/punctuation-insensitive")
    o = W / "o" / "nopricing.mp4"
    r = vs("cut-speech", T, "--remove", "pricing part", "--transcript", tj, "--pad", "0", "-o", o)
    good = r.get("ok") and o.exists() and near(probe(o)["dur"], 6.7, 0.12) and is_col(rgb(o, 4.5), "blue") and is_col(rgb(o, 5.6), "white") and near(r.get("removed_seconds", 0), 1.3, 0.05)
    ok(good, "cut-speech --remove cuts the phrase word-accurately")
    rt = r.get("transcript")
    if ok(bool(rt) and os.path.exists(rt), "cut-speech writes a retimed transcript beside the output"):
        ws = [w for s in json.loads(open(rt).read())["segments"] for w in s["words"]]
        ok(len(ws) == 7 and ws[-1]["w"] == "Goodbye!" and near(ws[-1]["start"], 5.2, 0.05), "retimed transcript drops the cut words and shifts the rest")
    r = vs("cut-speech", T, "--keep", "hello everyone", "--keep", "goodbye", "--transcript", tj, "--pad", "0", "-o", W / "o" / "keep.mp4")
    ok(r.get("ok") and near(probe(W / "o" / "keep.mp4")["dur"], 1.8, 0.12) and is_col(rgb(W / "o" / "keep.mp4", 1.5), "white"), "cut-speech --keep keeps only the phrases")
    r = vs("cut-speech", T, "--remove", "refund policy", "--transcript", tj, "-o", W / "o" / "x.mp4", expect_ok=False)
    ok(not r.get("ok") and "refund policy" in r.get("error", ""), "phrase not found is an error naming it")
    r = vs("remove-fillers", T, "--transcript", tj, "--pad", "0", "-o", W / "o" / "nofill.mp4")
    ok(r.get("ok") and r.get("removed_count") == 1 and near(probe(W / "o" / "nofill.mp4")["dur"], 7.6, 0.12), "remove-fillers removes 'um'")
    (W / "edited.txt").write_text("Hello everyone.\nThis is the\n\ngoodbye\n")
    r = vs("edit-text", T, "--edited", W / "edited.txt", "--transcript", tj, "--pad", "0", "-o", W / "o" / "edited.mp4")
    ok(r.get("ok") and len(r.get("removed", [])) == 2 and near(probe(W / "o" / "edited.mp4")["dur"], 6.3, 0.12), "edit-text cuts what was deleted from the text")
    (W / "edited2.txt").write_text("Hello everyone banana goodbye")
    r = vs("edit-text", T, "--edited", W / "edited2.txt", "--transcript", tj, "-o", W / "o" / "x.mp4", expect_ok=False)
    ok(not r.get("ok") and "banana" in r.get("error", ""), "edit-text rejects inserted words by name")
    shutil.copy(T, W / "plain.mp4")
    r = subprocess.run([sys.executable, "-m", "videoswami", "transcribe", str(W / "plain.mp4"), "--backend", "whisper-cpp"], capture_output=True, text=True, cwd=ROOT,
                       env={k: v for k, v in os.environ.items() if not k.startswith("VIDEOSWAMI_")})
    ok(r.returncode != 0 and '"ok":false' in r.stdout.replace(" ", ""), "missing backend fails cleanly with JSON")

def smart():
    M = W / "mover.mp4"
    o = W / "o" / "reframed.mp4"
    r = vs("reframe", M, "--aspect", "9:16", "-o", o)
    if ok(r.get("ok") and o.exists() and r.get("track") in ("motion", "face") and len(r.get("path", [])) >= 4, "reframe returns a tracking path"):
        q = probe(o)
        ok(abs(q["v"]["width"] / q["v"]["height"] - 9 / 16) < 0.02 and near(q["dur"], 4, 0.12), "reframe output is 9:16, same length")
        hits = [count(o, t, bright) for t in (0.6, 2.0, 3.4)]
        ok(all(h > 40 for h in hits), "reframe keeps the moving subject in frame at start, middle, end (%s)" % hits)
        xs = [p["x"] for p in r["path"]]
        ok(xs[0] < 0.35 and xs[-1] > 0.65 and all(b >= a - 0.02 for a, b in zip(xs, xs[1:])), "tracking path moves smoothly left to right")
    c = vs("reframe", M, "--aspect", "9:16", "--track", "center", "-o", W / "o" / "center.mp4")
    ok(c.get("ok") and count(W / "o" / "center.mp4", 0.6, bright) == 0, "--track center is a plain centre crop")
    E = W / "event.mp4"
    o = W / "o" / "hl.mp4"
    r = vs("highlights", E, "--duration", "3", "--min-clip", "1", "-o", o)
    segs = r.get("segments", [])
    ok(r.get("ok") and o.exists() and near(probe(o)["dur"], 3, 0.6) and segs and all(s["end"] > 4.5 and s["start"] < 8.5 for s in segs), "highlights picks the eventful part (%s)" % segs)
    ok(r.get("ok") and mean_volume(o) > -30, "highlights output contains the loud section")
    b = vs("beats", W / "click.wav")
    bt = b.get("beats", [])
    gaps = [y - x for x, y in zip(bt, bt[1:])]
    ok(b.get("ok") and near(b.get("bpm", 0), 120, 4) and len(bt) >= 12 and all(near(x, 0.5, 0.06) for x in gaps), "beats: 120 bpm click track (bpm %s, %d beats)" % (b.get("bpm"), len(bt)))
    o = W / "o" / "beat.mp4"
    r = vs("beat-sync", W / "red.mp4", W / "green.mp4", W / "blue.mp4", "--audio", W / "click.wav", "--every", "2", "--duration", "6", "-o", o)
    cuts = r.get("cuts", [])
    if ok(r.get("ok") and o.exists() and near(probe(o)["dur"], 6, 0.15) and len(cuts) >= 4, "beat-sync renders to the requested length"):
        ok(all(near(y - x, 1.0, 0.08) for x, y in zip(cuts, cuts[1:])), "beat-sync cuts every 2 beats = 1s (%s)" % cuts)
        seq = []
        for i in range(len(cuts) + 1):
            a = cuts[i - 1] if i else 0.0; z = cuts[i] if i < len(cuts) else 6.0
            if z - a > 0.3:
                col = rgb(o, (a + z) / 2); seq.append(next((n for n in ("red", "green", "blue") if is_col(col, n)), "?"))
        ok("?" not in seq and all(x != y for x, y in zip(seq, seq[1:])) and seq[:3] == ["red", "green", "blue"], "beat-sync cycles clips on the cuts (%s)" % seq)
        ok(probe(o)["a"] is not None and mean_volume(o) > -45, "beat-sync has the music")
    D = W / "dull.mp4"
    o = W / "o" / "enh.mp4"
    r = vs("enhance", D, "-o", o)
    if ok(r.get("ok") and o.exists() and near(probe(o)["dur"], 3, 0.12), "enhance renders"):
        ok(luma(o, 1.5) > luma(D, 1.5) + 12, "enhance brightens a dull picture (%.0f -> %.0f)" % (luma(D, 1.5), luma(o, 1.5)))
        ok(mean_volume(o) > mean_volume(D) + 10, "enhance lifts quiet audio (%.1f -> %.1f dB)" % (mean_volume(D), mean_volume(o)))
    r = vs("enhance", D, "--no-video", "-o", W / "o" / "enh2.mp4")
    ok(r.get("ok") and abs(luma(W / "o" / "enh2.mp4", 1.5) - luma(D, 1.5)) < 4, "enhance --no-video leaves the picture alone")

def finish():
    B, tj = W / "black.mp4", W / "t.json"
    low = (0, 0.5, 1, 1)
    o = W / "o" / "cap_hl.mp4"
    r = vs("captions", B, "--transcript", tj, "--style", "highlight", "--accent", "#00FF00", "--color", "white", "--size", "110", "-o", o)
    if ok(r.get("ok") and o.exists() and near(probe(o)["dur"], 8, 0.12) and r.get("groups", 0) >= 3 and os.path.exists(r.get("ass", "")), "captions renders and returns the .ass"):
        ok(count(o, 0.4, greenish, low) > 15 and count(o, 0.4, bright, low) > 15, "highlight style: spoken word in the accent colour, the rest white")
        a, b = count(o, 0.4, greenish, low), count(o, 1.0, greenish, low)
        ok(a > 0 and b > 0 and abs(a - b) > 3, "highlight moves from word to word (%d vs %d accent pixels)" % (a, b))
        ok(count(o, 1.9, lambda c: max(c) > 60) == 0 and count(o, 5.95, lambda c: max(c) > 60) == 0, "no caption on screen in the gaps")
        ok(count(o, 0.4, lambda c: max(c) > 60, (0, 0, 1, 0.4)) == 0, "captions sit at the bottom by default")
    o = W / "o" / "cap_pop.mp4"
    r = vs("captions", B, "--transcript", tj, "--style", "pop", "--position", "center", "--uppercase", "--size", "140", "-o", o)
    ok(r.get("ok") and count(o, 4.6, bright, (0, 0.25, 1, 0.75)) > 30 and count(o, 4.6, lambda c: max(c) > 60, (0, 0.8, 1, 1)) == 0, "pop style, centred")
    for st in ("karaoke", "box", "classic", "minimal"):
        o = W / "o" / ("cap_%s.mp4" % st)
        r = vs("captions", B, "--transcript", tj, "--style", st, "-o", o)
        ok(r.get("ok") and count(o, 3.0, lambda c: max(c) > 120) > 8, "captions style %s" % st)
    r = vs("captions", B, "--transcript", tj, "--ass-only", "-o", W / "o" / "only.ass")
    ok(r.get("ok") and "[Events]" in (W / "o" / "only.ass").read_text(), "captions --ass-only")
    tl = vs("templates")
    names = [t["name"] for t in tl.get("templates", [])]
    ok(all(n in names for n in ("lower-third", "title-card", "corner-tag", "end-card", "chapter")), "templates lists the five templates")
    o = W / "o" / "lt.mp4"
    r = vs("title", B, "--template", "lower-third", "--text", "Swami K", "--sub", "Editor in chief", "--start", "1", "--end", "4", "--accent", "#FFE600", "-o", o)
    ok(r.get("ok") and count(o, 2.5, yellowish, (0, 0.55, 0.6, 1)) > 5 and count(o, 2.5, bright, (0, 0.55, 0.7, 1)) > 10, "lower-third: accent bar and text bottom-left")
    ok(r.get("ok") and count(o, 0.5, lambda c: max(c) > 60) == 0 and count(o, 5, lambda c: max(c) > 60) == 0 and count(o, 2.5, lambda c: max(c) > 60, (0, 0, 1, 0.45)) == 0, "lower-third only between start and end, only at the bottom")
    for n in ("title-card", "corner-tag", "end-card", "chapter"):
        o = W / "o" / ("tpl_%s.mp4" % n)
        r = vs("title", B, "--template", n, "--text", "Part One", "--sub", "The setup", "--start", "0", "--end", "3", "-o", o)
        ok(r.get("ok") and count(o, 1.5, lambda c: max(c) > 120) > 8 and count(o, 5, lambda c: max(c) > 60) == 0, "template %s" % n)
    # keyframes: pan from the red half to the blue half at zoom 2
    spec = {"output": {"preset": "veryfast"}, "timeline": [{"src": "halves.mp4", "keyframes": [{"t": 0, "zoom": 2, "x": 0.25, "y": 0.5}, {"t": 2, "zoom": 2, "x": 0.75, "y": 0.5, "ease": "inout"}]}],
            "overlays": [{"type": "image", "src": "logo.png", "start": 1, "end": 3, "x": "center", "y": "center", "scale": 0.2, "animate": {"in": "slide-up", "out": "fade", "duration": 0.3}},
                         {"type": "title", "template": "corner-tag", "text": "LIVE", "start": 0, "end": 3}],
            "captions": {"transcript": "t.json", "style": "pop", "position": "bottom", "max_words": 2}}
    (W / "kf.json").write_text(json.dumps(spec))
    o = W / "o" / "kf.mp4"
    r = vs("render", W / "kf.json", "-o", o)
    if ok(r.get("ok") and o.exists() and near(probe(o)["dur"], 3, 0.1) and probe(o)["v"]["width"] == 640, "spec with keyframes, animated overlay, title overlay and captions renders"):
        ok(is_col(rgb(o, 0.1, 0.9, 0.5), "red") and is_col(rgb(o, 0.1, 0.1, 0.5), "red"), "keyframes: whole frame red at the start (zoomed on the left half)")
        ok(is_col(rgb(o, 2.6, 0.1, 0.3), "blue") and is_col(rgb(o, 2.6, 0.9, 0.3), "blue"), "keyframes: whole frame blue after the move")
        ok(is_col(rgb(o, 1.0, 0.1, 0.3), "red") and is_col(rgb(o, 1.0, 0.9, 0.3), "blue"), "keyframes: halfway shows both halves")
        ok(is_col(rgb(o, 2.0), "white") and not is_col(rgb(o, 0.5), "white"), "animated image overlay appears in its window")
        ok(count(o, 0.4, bright, (0, 0.6, 1, 1)) > 10, "spec-level captions are drawn")
    bad = {"timeline": [{"src": "halves.mp4", "zoom": {"from": 1, "to": 2}, "keyframes": [{"t": 0, "zoom": 1}]}]}
    (W / "bad.json").write_text(json.dumps(bad))
    r = vs("validate", W / "bad.json", expect_ok=False)
    ok(not r.get("ok") and "keyframes" in r.get("error", ""), "zoom + keyframes together is rejected")
    pr = vs("profiles")
    ok(all(n in pr.get("profiles", {}) for n in ("reels", "shorts", "tiktok", "youtube", "youtube-4k", "square", "portrait", "whatsapp", "x")), "profiles lists all profiles")
    o = W / "o" / "reel.mp4"
    r = vs("export", W / "halves.mp4", "--profile", "reels", "--fit", "blur", "-o", o)
    q = probe(o) if r.get("ok") else None
    ok(bool(q) and q["v"]["width"] == 1080 and q["v"]["height"] == 1920 and q["v"]["r_frame_rate"] == "30/1" and near(q["dur"], 3, 0.1), "export --profile reels is 1080x1920@30")
    (W / "p.json").write_text(json.dumps({"output": {"profile": "square", "width": 540, "height": 540, "preset": "veryfast"}, "timeline": [{"src": "halves.mp4", "out": 1}]}))
    r = vs("render", W / "p.json", "-o", W / "o" / "sq.mp4")
    ok(r.get("ok") and probe(W / "o" / "sq.mp4")["v"]["width"] == 540, "explicit output keys override the profile")
    reg = subprocess.run([sys.executable, str(ROOT / "tests" / "check.py"), "core"], capture_output=True, text=True)
    ok(reg.returncode == 0, "v1 core check still passes (regression)" + ("" if reg.returncode == 0 else ": " + reg.stdout[-300:]))

if __name__ == "__main__":
    fixtures()
    for name, fn in (("speech", speech), ("smart", smart), ("finish", finish)):
        if MODE in (name, "all"):
            fn()
    print("FAIL (%d): %s" % (len(FAILS), "; ".join(FAILS[:6])) if FAILS else "PASS " + MODE)
    sys.exit(1 if FAILS else 0)
