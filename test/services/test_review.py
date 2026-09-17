import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from growth import review as review_module
from growth.review import FAIL, OK, WARN, find_banned, read_script, review_record
from growth.niche import load_niche

SRT = """1
00:00:00,100 --> 00:00:02,925
Most people think saving money is about cutting coffee

2
00:00:03,788 --> 00:00:04,537
It is not
"""


def _record(**overrides):
    record = {
        "niche_id": "personal-finance",
        "subject": "A subject",
        "platforms": ["tiktok", "youtube_shorts"],
        "files": [],
        "subtitle_path": "",
        "status": "succeeded",
    }
    record.update(overrides)
    return record


class TestReadScript(unittest.TestCase):
    def test_timecodes_and_indexes_are_dropped(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "subtitle.srt"
            path.write_text(SRT, encoding="utf-8")
            script = read_script(path)
        self.assertIn("cutting coffee", script)
        self.assertNotIn("-->", script)
        self.assertNotIn("00:00", script)

    def test_missing_file_gives_an_empty_script(self):
        self.assertEqual(read_script("/nonexistent/subtitle.srt"), "")


class TestBannedPhrases(unittest.TestCase):
    def test_detection_ignores_case(self):
        niche = load_niche("personal-finance")
        script = "Hey guys, IN TODAY'S VIDEO we talk about money."
        found = find_banned(script, niche)
        self.assertTrue(any("today" in phrase.lower() for phrase in found))

    def test_clean_script_reports_nothing(self):
        niche = load_niche("personal-finance")
        self.assertEqual(find_banned("Your savings rate sets the date.", niche), [])


class TestReviewRecord(unittest.TestCase):
    def _review_with_probe(self, record, duration, width=1080, height=1920):
        with patch.object(review_module, "probe", return_value=(duration, width, height)):
            return review_record(record)

    def test_video_under_a_minute_fails_for_tiktok(self):
        """Under 60s the Creator Rewards programme pays nothing at all."""
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"x")
            reviews = self._review_with_probe(_record(files=[str(video)]), 45.0)
        self.assertEqual(reviews[0].status, FAIL)
        self.assertIn("under the 60s", reviews[0].issues[0][1])

    def test_video_over_a_minute_passes_the_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"x")
            srt = Path(temp) / "s.srt"
            srt.write_text(SRT * 20, encoding="utf-8")
            record = _record(files=[str(video)], subtitle_path=str(srt))
            reviews = self._review_with_probe(record, 72.0)
        self.assertEqual(reviews[0].status, OK, reviews[0].issues)

    def test_length_gate_does_not_apply_without_tiktok(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"x")
            srt = Path(temp) / "s.srt"
            srt.write_text(SRT * 20, encoding="utf-8")
            record = _record(
                files=[str(video)], subtitle_path=str(srt), platforms=["youtube_long"]
            )
            reviews = self._review_with_probe(record, 45.0)
        self.assertNotIn(
            "under the 60s", " ".join(msg for _, msg in reviews[0].issues)
        )

    def test_banned_phrase_in_the_script_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"x")
            srt = Path(temp) / "s.srt"
            srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHey guys\n", encoding="utf-8")
            record = _record(files=[str(video)], subtitle_path=str(srt))
            reviews = self._review_with_probe(record, 72.0)
        self.assertEqual(reviews[0].status, FAIL)

    def test_wrong_orientation_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"x")
            srt = Path(temp) / "s.srt"
            srt.write_text(SRT * 20, encoding="utf-8")
            record = _record(files=[str(video)], subtitle_path=str(srt))
            reviews = self._review_with_probe(record, 72.0, width=1920, height=1080)
        self.assertEqual(reviews[0].status, FAIL)
        self.assertIn("9:16", reviews[0].issues[0][1])

    def test_slow_speech_warns_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"x")
            srt = Path(temp) / "s.srt"
            srt.write_text(SRT, encoding="utf-8")  # ~12 words
            record = _record(files=[str(video)], subtitle_path=str(srt))
            reviews = self._review_with_probe(record, 90.0)
        self.assertEqual(reviews[0].status, WARN)

    def test_missing_file_fails(self):
        reviews = review_record(_record(files=["/nonexistent/v.mp4"]))
        self.assertEqual(reviews[0].status, FAIL)
        self.assertIn("missing", reviews[0].issues[0][1])

    def test_unreadable_duration_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"not a video")
            reviews = self._review_with_probe(_record(files=[str(video)]), 0.0)
        self.assertEqual(reviews[0].status, FAIL)
        self.assertIn("truncated", reviews[0].issues[0][1])


class TestProbe(unittest.TestCase):
    @unittest.skipUnless(
        subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0,
        "ffmpeg is not available",
    )
    def test_reads_a_real_file(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            subprocess.run(
                ["ffmpeg", "-f", "lavfi", "-i", "color=c=black:s=320x568:d=2",
                 "-c:v", "libx264", str(video), "-y"],
                capture_output=True, check=True,
            )
            duration, width, height = review_module.probe(video)
        self.assertAlmostEqual(duration, 2.0, delta=0.5)
        self.assertEqual((width, height), (320, 568))

    def test_unreadable_file_returns_zeros(self):
        self.assertEqual(review_module.probe(Path("/nonexistent.mp4")), (0.0, 0, 0))


class TestLoadLedger(unittest.TestCase):
    def test_failed_rows_are_not_reviewed(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp) / "ledger.jsonl"
            ledger.write_text(
                json.dumps(_record(status="failed", subject="bad")) + "\n"
                + json.dumps(_record(status="succeeded", subject="good")) + "\n",
                encoding="utf-8",
            )
            rows = review_module.load_ledger(ledger_path=ledger)
        self.assertEqual([row["subject"] for row in rows], ["good"])

    def test_missing_ledger_is_not_an_error(self):
        self.assertEqual(review_module.load_ledger(ledger_path=Path("/nope.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
