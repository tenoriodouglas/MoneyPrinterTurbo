import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from growth.progress import SECONDS_PER_SCENE, Progress, ProgressTracker

# Lines as the engine actually prints them, trimmed of the loguru prefix.
# Each was verified against app/services/task.py and app/services/material.py.
SCRIPT = "generating video script"
TERMS = "generating video terms"
AUDIO = "generating audio"
SUBTITLE = "generating subtitle"
MATERIALS = "downloading videos from the Internet"
LOCAL_MATERIALS = "preprocess local materials"
SCENE = "image material rendered: /tmp/task/1.mp4"
COMBINING = "combining video: 1 => /tmp/task/combined-1.mp4"
RENDERING = "generating video: 1080 x 1920"


class Clock:
    """A hand-wound monotonic clock, so a twenty-minute render takes no time."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _tracker(scenes_total: int = 0) -> tuple[ProgressTracker, Clock]:
    clock = Clock()
    return ProgressTracker(scenes_total=scenes_total, now=clock), clock


class TestPhaseDetection(unittest.TestCase):
    def test_the_script_line_is_not_read_as_the_render_line(self):
        """Both start with "generating video"; only the digit after the colon
        tells them apart, and reading it wrong reports 75% at second one."""
        tracker, _ = _tracker()
        tracker.feed(SCRIPT)
        self.assertEqual(tracker.snapshot().phase, "script")

    def test_each_marker_moves_the_phase_forward(self):
        tracker, _ = _tracker()
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
            self.assertEqual(tracker.snapshot().phase, expected)

    def test_local_material_also_counts_as_the_drawing_stage(self):
        """A pack using stock or uploaded footage logs no download. Without
        this the stage is skipped and the percentage jumps."""
        tracker, _ = _tracker()
        self.assertTrue(tracker.feed(LOCAL_MATERIALS))
        self.assertEqual(tracker.snapshot().phase, "materials")

    def test_an_unrecognised_line_changes_nothing(self):
        tracker, _ = _tracker()
        tracker.feed(AUDIO)
        self.assertFalse(tracker.feed("some unrelated log line"))
        self.assertEqual(tracker.snapshot().phase, "audio")

    def test_a_repeated_marker_is_not_a_change(self):
        tracker, _ = _tracker()
        self.assertTrue(tracker.feed(AUDIO))
        self.assertFalse(tracker.feed(AUDIO))

    def test_the_second_video_of_a_batch_does_not_rewind_the_phase(self):
        """The engine starts over for each task in a batch. A percentage that
        fell from 75 back to 5 would read as a crash and a restart."""
        tracker, _ = _tracker()
        tracker.feed(RENDERING)
        self.assertFalse(tracker.feed(SCRIPT))
        self.assertEqual(tracker.snapshot().phase, "rendering")


class TestSceneCount(unittest.TestCase):
    """Drawing is the only stage with an exact count, because the engine
    prints one line per generated image."""

    def test_scenes_are_counted(self):
        tracker, _ = _tracker(scenes_total=3)
        tracker.feed(MATERIALS)
        tracker.feed(SCENE)
        tracker.feed(SCENE)
        self.assertEqual(tracker.snapshot().scenes_done, 2)

    def test_the_count_is_shown_with_its_total(self):
        tracker, _ = _tracker(scenes_total=21)
        tracker.feed(MATERIALS)
        tracker.feed(SCENE)
        self.assertIn("(1/21 cenas)", tracker.snapshot().describe())

    def test_an_unknown_total_reports_no_count(self):
        """Without a total, "3 scenes done" answers nothing worth asking."""
        tracker, _ = _tracker()
        tracker.feed(MATERIALS)
        tracker.feed(SCENE)
        self.assertNotIn("cenas)", tracker.snapshot().describe())

    def test_a_stage_with_no_count_still_advances_on_its_clock(self):
        """Stock footage prints no per-image line. Time has to carry the
        estimate, or the stage sits frozen for its whole duration."""
        tracker, clock = _tracker()
        tracker.feed(MATERIALS)
        before = tracker.snapshot().fraction
        clock.advance(120)
        self.assertGreater(tracker.snapshot().fraction, before)

    def test_more_scenes_means_a_longer_estimate(self):
        short, _ = _tracker(scenes_total=5)
        long, _ = _tracker(scenes_total=25)
        short.feed(MATERIALS)
        long.feed(MATERIALS)
        difference = (
            long.snapshot().remaining_seconds - short.snapshot().remaining_seconds
        )
        self.assertAlmostEqual(difference, 20 * SECONDS_PER_SCENE, delta=1)


class TestTheEstimateConverges(unittest.TestCase):
    """The point of the whole module. A share-of-work model freezes during the
    two stages that print nothing, and there the estimate GROWS with elapsed
    time — it told you twenty-two minutes remained when six did."""

    def _run(self, scenes: int = 21):
        """Walk a render at its measured pace, sampling every 30 seconds."""
        tracker, clock = _tracker(scenes_total=scenes)
        samples: list[tuple[float, float]] = []

        def tick(seconds: float) -> None:
            for _ in range(max(int(seconds // 15), 1)):
                clock.advance(seconds / max(int(seconds // 15), 1))
                snap = tracker.snapshot()
                samples.append((snap.elapsed, snap.remaining_seconds))

        for line, seconds in (
            (SCRIPT, 35),
            (TERMS, 20),
            (AUDIO, 40),
            (SUBTITLE, 25),
        ):
            tracker.feed(line)
            tick(seconds)
        tracker.feed(MATERIALS)
        for _ in range(scenes):
            tick(SECONDS_PER_SCENE)
            tracker.feed(SCENE)
        tracker.feed(COMBINING)
        tick(420)
        tracker.feed(RENDERING)
        tick(300)
        return samples

    def test_the_time_remaining_never_grows(self):
        samples = self._run()
        self.assertGreater(len(samples), 60)
        for (_, earlier), (_, later) in zip(samples, samples[1:]):
            # Allow a second of slack: a stage ending early hands its unspent
            # budget back, which is a correction, not a regression.
            self.assertLessEqual(later, earlier + 1, "the estimate went up")

    def test_the_percentage_never_falls(self):
        tracker, clock = _tracker(scenes_total=8)
        seen = 0.0
        for line in (SCRIPT, TERMS, AUDIO, SUBTITLE, MATERIALS, COMBINING, RENDERING):
            tracker.feed(line)
            for _ in range(4):
                clock.advance(30)
                now = tracker.snapshot().fraction
                self.assertGreaterEqual(now, seen)
                seen = now

    def test_it_ends_close_to_done_rather_than_minutes_out(self):
        """The old model stopped at 75% and still claimed seven minutes left
        at the moment the video appeared."""
        samples = self._run()
        _, remaining = samples[-1]
        self.assertLess(remaining / 60, 2.0)

    def test_the_estimate_starts_near_the_measured_total(self):
        """docs/HOSPEDAGEM.md measures 21 scenes at about 21 minutes."""
        tracker, _ = _tracker(scenes_total=21)
        tracker.feed(SCRIPT)
        self.assertAlmostEqual(tracker.snapshot().remaining_seconds / 60, 21, delta=2)


class TestSlowMachines(unittest.TestCase):
    """The budgets came from one 4-vCPU box. A free-tier ARM VPS is slower at
    everything, so an early stage running long predicts the later ones will."""

    def test_a_slow_start_stretches_the_remaining_estimate(self):
        fast, fast_clock = _tracker(scenes_total=8)
        slow, slow_clock = _tracker(scenes_total=8)
        for tracker, clock, pace in ((fast, fast_clock, 1), (slow, slow_clock, 3)):
            tracker.feed(SCRIPT)
            clock.advance(35 * pace)
            tracker.feed(TERMS)
            clock.advance(20 * pace)
            tracker.feed(AUDIO)
        self.assertGreater(
            slow.snapshot().remaining_seconds, fast.snapshot().remaining_seconds * 1.5
        )

    def test_one_stalled_stage_does_not_multiply_everything(self):
        """A single hung network call must not turn twenty minutes into two
        hours; the factor is clamped."""
        tracker, clock = _tracker(scenes_total=8)
        tracker.feed(SCRIPT)
        clock.advance(35 * 50)
        tracker.feed(TERMS)
        baseline, _ = _tracker(scenes_total=8)
        baseline.feed(TERMS)
        self.assertLessEqual(
            tracker.snapshot().remaining_seconds,
            baseline.snapshot().remaining_seconds * 4,
        )


class TestDescribe(unittest.TestCase):
    def test_before_any_marker_it_says_it_is_starting(self):
        self.assertIn("começando", Progress().describe())

    def test_the_share_is_marked_approximate(self):
        """The budgets come from one measured render, not from counting the
        remaining work; the tilde is the honest part of the sentence."""
        tracker, _ = _tracker()
        tracker.feed(COMBINING)
        self.assertIn("~", tracker.snapshot().describe())

    def test_the_time_left_is_shown_by_default(self):
        tracker, _ = _tracker()
        tracker.feed(COMBINING)
        self.assertIn("faltam", tracker.snapshot().describe())

    def test_the_time_left_can_be_left_out(self):
        tracker, _ = _tracker()
        tracker.feed(COMBINING)
        self.assertNotIn("faltam", tracker.snapshot().describe(with_eta=False))

    def test_it_never_claims_zero_minutes_left_while_working(self):
        """"faltam ~0 min" followed by four more minutes is worse than a
        vaguer number that stays true."""
        tracker, clock = _tracker(scenes_total=1)
        tracker.feed(RENDERING)
        clock.advance(60 * 60)
        self.assertNotIn("faltam ~0 min", tracker.snapshot().describe())

    def test_it_never_claims_to_be_finished(self):
        """The last seconds of muxing print nothing, and a bar sitting at 100%
        invites a "why is it stuck" message every time."""
        tracker, clock = _tracker(scenes_total=1)
        tracker.feed(RENDERING)
        clock.advance(60 * 60)
        self.assertLess(tracker.snapshot().fraction, 1.0)
        self.assertNotIn("100%", tracker.snapshot().describe())


class TestSnapshotConsistency(unittest.TestCase):
    def test_a_snapshot_does_not_change_under_the_reader(self):
        """/status reads this from another thread while the render writes to
        it. A line whose label and percentage disagree reads as a bug."""
        tracker, _ = _tracker(scenes_total=4)
        tracker.feed(MATERIALS)
        tracker.feed(SCENE)
        snapshot = tracker.snapshot()
        tracker.feed(COMBINING)
        tracker.feed(SCENE)
        self.assertEqual(snapshot.phase, "materials")
        self.assertEqual(snapshot.scenes_done, 1)


if __name__ == "__main__":
    unittest.main()
