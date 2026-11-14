import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipswami import captions, fetch, ff, picker, reframe, render, signals  # noqa: E402
from clipswami import transcript as tr  # noqa: E402

SRT = """1
00:00:01,000 --> 00:00:03,000
Why does <i>nobody</i> talk about this?

2
00:00:03,100 --> 00:00:05,500
Because it cost me 3 clients.
"""

VTT = """WEBVTT

00:01.000 --> 00:02.000
hello there

00:02.000 --> 00:03.000
hello there
general kenobi
"""


def fake_analysis(duration=120.0):
    """Speech in 8s blocks with 1.8s pauses; a louder stretch from 40s to 64s."""
    energy = []
    for i in range(int(duration * 10)):
        t = i / 10
        if t % 8 >= 6.2:
            energy.append(-70.0)
        else:
            energy.append((-18.0 if 40 <= t < 64 else -28.0) + 3 * math.sin(t * 2.1))
    return {
        "info": {"duration": duration, "width": 1920, "height": 1080, "fps": 30.0,
                 "has_audio": True, "path": "x.mp4"},
        "energy_dt": 0.1, "energy": energy,
        "silences": signals.find_silences(energy), "scenes": [[32.0, 0.6], [64.0, 0.5]],
    }


class TimeTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(ff.parse_time("90"), 90)
        self.assertEqual(ff.parse_time("1:30"), 90)
        self.assertEqual(ff.parse_time("01:02:03.5"), 3723.5)
        self.assertEqual(ff.parse_time("1m30s"), 90)
        self.assertEqual(ff.parse_time("2h"), 7200)
        self.assertEqual(ff.parse_time(12.5), 12.5)
        with self.assertRaises(ff.ClipError):
            ff.parse_time("soon")

    def test_slug(self):
        self.assertEqual(ff.slugify("Why Most People QUIT!"), "why-most-people-quit")
        self.assertEqual(ff.slugify("???", "clip-01"), "clip-01")


class FetchTests(unittest.TestCase):
    def test_is_url(self):
        self.assertTrue(fetch.is_url("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(fetch.is_url("www.youtube.com/watch?v=abc"))
        self.assertFalse(fetch.is_url("videos/talk.mp4"))
        self.assertFalse(fetch.is_url("http talk.mp4"))

    def test_command(self):
        cmd = fetch.build_command("www.youtube.com/watch?v=abc", Path("dl"), 720, "10:00-12:30")
        self.assertEqual(cmd[-1], "https://www.youtube.com/watch?v=abc")
        self.assertIn("*600-750", cmd)
        self.assertIn("--write-auto-subs", cmd)
        self.assertTrue(any("height<=720" in part for part in cmd))

    def test_download_with_stub(self):
        tmp = Path(tempfile.mkdtemp(prefix="clipswami-fetch-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        stub = tmp / "yt-dlp"
        stub.write_text("#!/bin/sh\nfor a; do last=$a; done\n"
                        f"touch {tmp}/dl/video-abc.mp4; echo {tmp}/dl/video-abc.mp4\n")
        stub.chmod(0o755)
        original = fetch.downloader
        fetch.downloader = lambda: [str(stub)]
        self.addCleanup(setattr, fetch, "downloader", original)
        path = fetch.download("https://youtu.be/abc", tmp / "dl")
        self.assertEqual(path.name, "video-abc.mp4")

    def test_missing_downloader_is_explained(self):
        original = fetch.downloader
        fetch.downloader = lambda: None
        self.addCleanup(setattr, fetch, "downloader", original)
        with self.assertRaisesRegex(ff.ClipError, "pip install"):
            fetch.download("https://youtu.be/abc")


class TranscriptTests(unittest.TestCase):
    def test_srt(self):
        cues = tr.parse_subtitles(SRT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "Why does nobody talk about this?")
        self.assertAlmostEqual(cues[1]["end"], 5.5)

    def test_vtt_rolling_duplicates(self):
        cues = tr.parse_subtitles(VTT)
        self.assertEqual([c["text"] for c in cues], ["hello there", "general kenobi"])

    def test_srt_round_trip(self):
        tmp = Path(tempfile.mkdtemp(prefix="clipswami-srt-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cues = tr.parse_subtitles(SRT)
        tr.write_srt(cues, tmp / "a.srt")
        self.assertEqual(tr.parse_subtitles((tmp / "a.srt").read_text()), cues)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_whisper_stub(self):
        tmp = Path(tempfile.mkdtemp(prefix="clipswami-whisper-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=d=2",
                        "-f", "lavfi", "-i", "color=s=64x64:d=2", str(tmp / "v.mp4")], check=True)
        stub = tmp / "whisper"
        stub.write_text('#!/bin/sh\nout=""; while [ $# -gt 0 ]; do '
                        '[ "$1" = "--output_dir" ] && out=$2; shift; done\n'
                        'printf "1\\n00:00:00,000 --> 00:00:01,500\\nhello world\\n" > "$out/audio.srt"\n')
        stub.chmod(0o755)
        original = tr.whisper_command
        tr.whisper_command = lambda: str(stub)
        self.addCleanup(setattr, tr, "whisper_command", original)
        cues = tr.run_whisper(tmp / "v.mp4", tmp / "work", "base", "en", strict=True)
        self.assertEqual(cues, [{"start": 0.0, "end": 1.5, "text": "hello world"}])

    def test_whisper_missing(self):
        original = tr.whisper_command
        tr.whisper_command = lambda: None
        self.addCleanup(setattr, tr, "whisper_command", original)
        self.assertEqual(tr.run_whisper(Path("x.mp4"), Path("w")), [])
        with self.assertRaisesRegex(ff.ClipError, "pip install"):
            tr.run_whisper(Path("x.mp4"), Path("w"), strict=True)

    def test_window_is_clip_relative(self):
        cues = tr.window(tr.parse_subtitles(SRT), 2.0, 4.0)
        self.assertAlmostEqual(cues[0]["start"], 0.0)
        self.assertAlmostEqual(cues[1]["end"], 2.0)


class PickerTests(unittest.TestCase):
    def test_silences_found(self):
        analysis = fake_analysis()
        self.assertEqual(len(analysis["silences"]), 15)
        self.assertAlmostEqual(analysis["silences"][0][0], 6.2, places=1)

    def test_constant_audio_has_no_silence(self):
        self.assertEqual(signals.find_silences([-20.0] * 500), [])

    def test_candidates_are_ranked_and_distinct(self):
        picks = picker.find_candidates(fake_analysis(), count=4, min_len=12, max_len=30)
        self.assertGreaterEqual(len(picks), 3)
        self.assertEqual([p["rank"] for p in picks], list(range(1, len(picks) + 1)))
        self.assertTrue(all(12 <= p["duration"] <= 30.5 for p in picks))
        self.assertEqual(sorted((p["score"] for p in picks), reverse=True), [p["score"] for p in picks])
        for i, a in enumerate(picks):
            for b in picks[i + 1:]:
                overlap = min(a["end"], b["end"]) - max(a["start"], b["start"])
                self.assertLess(overlap, 0.36 * min(a["duration"], b["duration"]) + 0.3)
        best = picks[0]                                   # the loud stretch should win
        self.assertTrue(best["start"] < 64 and best["end"] > 40)

    def test_short_video(self):
        picks = picker.find_candidates(fake_analysis(10.0), count=3)
        self.assertEqual(len(picks), 1)
        self.assertLessEqual(picks[0]["end"], 10.0)

    def test_topic_shift_detected(self):
        cooking = ["chop the onion and garlic finely", "heat the pan with olive oil",
                   "fry the onion until the garlic browns", "stir the sauce in the pan slowly",
                   "season the sauce with salt and pepper", "serve the pasta with the sauce"]
        money = ["invest your savings in index funds", "compound interest grows your savings",
                 "index funds beat most stock pickers", "keep investing through market drops",
                 "interest on debt destroys your savings", "funds with low fees grow faster"]
        cues = [{"start": i * 3.0, "end": i * 3.0 + 2.8, "text": text}
                for i, text in enumerate(cooking + money)]
        shifts = picker.topic_shifts(cues)
        self.assertEqual(max(range(len(shifts)), key=shifts.__getitem__), 6)

    def test_snap_moves_to_pause(self):
        start, end = picker.snap(fake_analysis(), [], 8.4, 29.9)
        self.assertAlmostEqual(start, 8.0 - 0.08, delta=0.15)
        self.assertAlmostEqual(end, 30.2 + 0.18, delta=0.15)


class RenderMathTests(unittest.TestCase):
    def test_output_size(self):
        self.assertEqual(render.output_size("9:16", 1080, 1920, 1080), (1080, 1920))
        self.assertEqual(render.output_size("16:9", 720, 1920, 1080), (1280, 720))
        self.assertEqual(render.output_size("1:1", 1080, 1920, 1080), (1080, 1080))
        self.assertEqual(render.output_size("source", 720, 1920, 1080), (1280, 720))

    def test_keep_segments_and_remap(self):
        keeps = render.keep_segments([[5.0, 7.0], [12.0, 12.3]], 0.0, 20.0)
        self.assertEqual(keeps, [(0.0, 5.18), (6.82, 20.0)])
        remap = render.remapper(keeps)
        self.assertAlmostEqual(remap(3.0), 3.0)
        self.assertAlmostEqual(remap(6.0), 5.18)            # inside the removed pause
        self.assertAlmostEqual(remap(10.0), 10.0 - 1.64)

    def test_no_pause_means_no_edit(self):
        self.assertEqual(render.keep_segments([[5.0, 5.4]], 0.0, 20.0), [])

    def test_options_merge(self):
        opts = render.Options().merged({"fit": "blur", "start": 3, "captions": None})
        self.assertEqual((opts.fit, opts.captions), ("blur", "bold"))


class CameraTests(unittest.TestCase):
    def test_camera_is_calm_and_follows(self):
        times = [i / 5 for i in range(1, 51)]
        targets = [0.2 + 0.01 * ((i % 3) - 1) for i in range(25)] + [0.8] * 25
        path = reframe.camera_path(times, targets, [])
        early = [p for t, p in path if t < 4.5]
        self.assertLess(max(early) - min(early), 0.03)      # jitter inside the dead zone
        self.assertGreater(path[-1][1], 0.6)                # moved toward the new position
        steps = [abs(b[1] - a[1]) for a, b in zip(path, path[1:])]
        self.assertLessEqual(max(steps), 0.35 / 5 + 1e-9)   # speed limited

    def test_cut_jumps(self):
        times = [i / 5 for i in range(1, 21)]
        targets = [0.2] * 10 + [0.9] * 10
        path = reframe.camera_path(times, targets, [times[10]])
        self.assertAlmostEqual(path[-1][1], 0.9)

    def test_expression(self):
        self.assertEqual(reframe.expression([(0, 100.0), (5, 100.2)]), "100")
        expr = reframe.expression([(0.0, 0.0), (2.0, 200.0), (4.0, 200.0)])
        self.assertIn("lerp(0,200,(t-0.000)/2.000)", expr)
        pts = [(i * 0.2, 10.0 * i) for i in range(50)]
        self.assertEqual(len(reframe.simplify(pts, 1.0)), 2)


class CaptionTests(unittest.TestCase):
    def test_chunks_break_on_sentences(self):
        words = captions.word_times([{"start": 0, "end": 4, "text": "It never is. The real problem is showing up"}])
        chunks = captions.chunk_words(words, 3, 16)
        self.assertEqual([w for w, _, _ in chunks[0]], ["It", "never", "is."])
        self.assertTrue(all(len(c) <= 3 for c in chunks))
        self.assertAlmostEqual(words[-1][2], 4.0)

    def test_ass_has_highlight_title_and_stress(self):
        ass = captions.build_ass([{"start": 0, "end": 2, "text": "I lost 3 clients"}], 1080, 1920,
                                 title="The {lesson}", total=10)
        self.assertIn("PlayResX: 1080", ass)
        self.assertIn("Title,,0,0,0,,{\\fad(150,300)}The (lesson)", ass)
        self.assertIn("&H0000E5FF", ass)
        self.assertIn("&H0078E650&}3", ass)                # the number is stressed
        self.assertEqual(ass.count("Dialogue:"), 5)

    def test_power_words(self):
        self.assertTrue(captions.is_power_word("NEVER,"))
        self.assertTrue(captions.is_power_word("$40k"))
        self.assertFalse(captions.is_power_word("the"))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class EndToEndTests(unittest.TestCase):
    def test_full_pipeline(self):
        tmp = Path(tempfile.mkdtemp(prefix="clipswami-test-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        video = tmp / "demo.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=0x202830:s=640x360:r=25:d=30",
            "-f", "lavfi", "-i", "color=c=orange:s=80x140:r=25:d=30",
            "-f", "lavfi", "-i",
            "aevalsrc='if(lt(mod(t,6),4.5),0.3*sin(2*PI*220*t)*(0.6+0.4*sin(19*t)),0)':s=44100:d=30",
            "-filter_complex", "[0:v][1:v]overlay=x='if(lt(t,15),60,480)':y=110[v]",
            "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", str(video)], check=True)
        (tmp / "demo.srt").write_text(
            "\n".join(f"{i + 1}\n00:00:{i * 6:02d},100 --> 00:00:{i * 6 + 4:02d},400\n"
                      f"Here is why step {i + 1} never fails for you.\n" for i in range(5)))

        def cli(*args):
            return subprocess.run([sys.executable, "-m", "clipswami", *args], cwd=tmp,
                                  capture_output=True, text=True,
                                  env={"PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                                       "PATH": __import__("os").environ["PATH"]})

        proc = cli("auto", "demo.mp4", "-n", "1", "--min", "8", "--max", "14", "--res", "360",
                   "--preset", "ultrafast", "--progress")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        manifest = json.loads((tmp / "clips" / "demo" / "manifest.json").read_text())
        clip = manifest["clips"][0]
        self.assertNotIn("error", clip)
        self.assertTrue(clip["captions"])
        out = ff.probe(clip["file"])
        self.assertEqual((out["width"], out["height"]), (360, 640))
        self.assertTrue(out["has_audio"])
        self.assertAlmostEqual(out["duration"], clip["duration"], delta=0.3)
        self.assertTrue(Path(clip["thumbnail"]).is_file())

        # Tracking: the subject sits at the far left, so the crop must not be centred.
        analysis = json.loads((tmp / ".clipswami" / "demo" / "analysis.json").read_text())
        x_expr, _ = reframe.plan_crop(str(video), 16.0, 8.0, 640, 360, 202, 360)
        self.assertGreater(float(x_expr), 350)
        self.assertEqual(len(analysis["silences"]), 5)

        proc = cli("review", "clips/demo")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(list((tmp / "clips" / "demo" / "review").glob("*.jpg")))


if __name__ == "__main__":
    unittest.main()
