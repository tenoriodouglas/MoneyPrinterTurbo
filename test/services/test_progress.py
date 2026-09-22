import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from growth.progress import Progress, ProgressTracker

# Lines as the engine actually prints them, trimmed of the loguru prefix.
SCRIPT = "generating video script"
TERMS = "generating video terms"
AUDIO = "generating audio"
SUBTITLE = "generating subtitle"
MATERIALS = "downloading videos from the Internet"
SCENE = "image material rendered: /tmp/task/1.mp4"
COMBINING = "combining video: 1 => /tmp/task/combined-1.mp4"
RENDERING = "generating video: 1080 x 1920"


class TestPhaseDetection(unittest.TestCase):
    def test_the_script_line_is_not_read_as_the_render_line(self):
        """Both start with "generating video"; the order of the markers is the
        only thing keeping a render that just started from reporting 75%."""
        tracker = ProgressTracker()
        tracker.feed(SCRIPT)
        self.assertEqual(tracker.progress.phase, "script")

    def test_each_marker_moves_the_phase_forward(self):
        tracker = ProgressTracker()
        for line, expected in (
            (SCRIPT, "script"),
            (TERMS, "terms"),
            (AUDIO, "audio"),
            (SUBTITLE, "subtitle"),
            (MATERIALS, "materials"),
            (COMBINING, "combining"),
            (RENDERING, "rendering"),
        ):
            self.assertTrue(tracker.feed(line), line)
            self.assertEqual(tracker.progress.phase, expected)

    def test_an_unrecognised_line_changes_nothing(self):
        tracker = ProgressTracker()
        tracker.feed(AUDIO)
        self.assertFalse(tracker.feed("some unrelated log line"))
        self.assertEqual(tracker.progress.phase, "audio")

    def test_a_repeated_marker_is_not_a_change(self):
        tracker = ProgressTracker()
        self.assertTrue(tracker.feed(AUDIO))
        self.assertFalse(tracker.feed(AUDIO))

    def test_the_second_video_of_a_batch_does_not_rewind_the_phase(self):
        """The engine starts over for each task in a batch. A percentage that
        jumped from 75 back to 5 would read as a crash and a restart."""
        tracker = ProgressTracker()
        tracker.feed(RENDERING)
        self.assertFalse(tracker.feed(SCRIPT))
        self.assertEqual(tracker.progress.phase, "rendering")


class TestSceneCount(unittest.TestCase):
    """The drawing phase is the only one with an exact count, because the
    engine prints one line per generated image."""

    def test_scenes_are_counted(self):
        tracker = ProgressTracker(scenes_total=3)
        tracker.feed(MATERIALS)
        tracker.feed(SCENE)
        tracker.feed(SCENE)
        self.assertEqual(tracker.progress.scenes_done, 2)

    def test_the_count_is_shown_with_its_total(self):
        tracker = ProgressTracker(scenes_total=21)
        tracker.feed(MATERIALS)
        tracker.feed(SCENE)
        self.assertIn("(1/21 cenas)", tracker.progress.describe())

    def test_scenes_advance_the_share_within_the_phase(self):
        tracker = ProgressTracker(scenes_total=4)
        tracker.feed(MATERIALS)
        start = tracker.progress.fraction
        for _ in range(2):
            tracker.feed(SCENE)
        self.assertGreater(tracker.progress.fraction, start)

    def test_an_unknown_total_reports_no_count(self):
        """Without a total, "3 scenes done" answers nothing worth asking."""
        tracker = ProgressTracker()
        tracker.feed(MATERIALS)
        tracker.feed(SCENE)
        self.assertNotIn("/", tracker.progress.describe())


class TestFraction(unittest.TestCase):
    def test_nothing_seen_yet_is_zero(self):
        self.assertEqual(Progress().fraction, 0.0)

    def test_the_share_never_reaches_one(self):
        """Reporting 100% while ffmpeg is still muxing invites a "why is it
        stuck" message every time."""
        tracker = ProgressTracker()
        tracker.feed(RENDERING)
        self.assertLess(tracker.progress.fraction, 1.0)

    def test_later_phases_report_a_larger_share(self):
        early, late = ProgressTracker(), ProgressTracker()
        early.feed(AUDIO)
        late.feed(COMBINING)
        self.assertGreater(late.progress.fraction, early.progress.fraction)


class TestEtaMinutes(unittest.TestCase):
    def test_too_early_to_say_returns_nothing(self):
        """At 3% done a few seconds of jitter moves the estimate by ten
        minutes, so no number is better than a wrong one."""
        tracker = ProgressTracker()
        tracker.feed(SCRIPT)
        self.assertIsNone(tracker.progress.eta_minutes(30))

    def test_halfway_leaves_about_as_long_as_has_passed(self):
        progress = Progress(phase="combining")
        # script+terms+audio+subtitle+materials = 0.40, so combining starts there.
        self.assertAlmostEqual(progress.fraction, 0.40, places=2)
        self.assertAlmostEqual(progress.eta_minutes(600), 15.0, places=1)

    def test_a_later_phase_leaves_less_time(self):
        elapsed = 900
        combining = Progress(phase="combining").eta_minutes(elapsed)
        rendering = Progress(phase="rendering").eta_minutes(elapsed)
        self.assertLess(rendering, combining)

    def test_no_elapsed_time_yields_no_estimate(self):
        self.assertIsNone(Progress(phase="rendering").eta_minutes(0))


class TestDescribe(unittest.TestCase):
    def test_before_any_marker_it_says_it_is_starting(self):
        self.assertIn("começando", Progress().describe())

    def test_the_share_is_marked_approximate(self):
        """The weights come from one measured render, not from counting the
        remaining work; the tilde is the honest part of the sentence."""
        tracker = ProgressTracker()
        tracker.feed(COMBINING)
        self.assertIn("~", tracker.progress.describe())

    def test_an_eta_is_appended_when_elapsed_time_is_given(self):
        tracker = ProgressTracker()
        tracker.feed(COMBINING)
        self.assertIn("faltam", tracker.progress.describe(600))

    def test_an_estimate_too_early_says_so_instead_of_guessing(self):
        tracker = ProgressTracker()
        tracker.feed(SCRIPT)
        text = tracker.progress.describe(20)
        self.assertNotIn("faltam", text)
        self.assertIn("calculando", text)

    def test_elapsed_time_is_optional(self):
        tracker = ProgressTracker()
        tracker.feed(COMBINING)
        self.assertNotIn("faltam", tracker.progress.describe())


if __name__ == "__main__":
    unittest.main()
