import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from growth import bot as bot_module
from growth.bot import BotError, GrowthBot, load_settings, poll_forever

OWNER = 111
STRANGER = 222


class FakeClient:
    """Records what would have been sent, instead of calling Telegram."""

    def __init__(self, updates=None):
        self.messages: list[tuple[int, str]] = []
        self.videos: list[tuple[int, Path, str]] = []
        self._updates = list(updates or [])

    def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    def send_video(self, chat_id, path, caption=""):
        self.videos.append((chat_id, path, caption))

    def get_updates(self, offset):
        if not self._updates:
            raise StopIteration
        return self._updates.pop(0)

    def texts_to(self, chat_id):
        return " ".join(text for target, text in self.messages if target == chat_id)


def _message(text, chat_id=OWNER, sender_id=None):
    """A private chat carries the same id in both places; a group does not."""
    return {
        "chat": {"id": chat_id},
        "from": {"id": OWNER if sender_id is None else sender_id},
        "text": text,
    }


def _wait_idle(bot, timeout=10):
    """Block until the render thread has finished and freed the queue."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with bot._lock:
            if bot._job is None:
                return True
        time.sleep(0.01)
    return False


def _bot(client=None, runner=None, allowed=None):
    return GrowthBot(
        client or FakeClient(),
        allowed if allowed is not None else {OWNER},
        runner=runner or (lambda niche_id, count, **_: {"succeeded": 0, "total": 0, "records": []}),
    )


class TestAccessControl(unittest.TestCase):
    """A bot token is discoverable and anyone can message it. A render spends
    api quota and an hour of cpu, so only the owner may start one."""

    def test_a_stranger_cannot_start_a_render(self):
        client = FakeClient()
        started = []
        bot = _bot(client, runner=lambda n, c, **_: started.append(n) or {})
        bot.handle(_message("/run ufo-sightings", chat_id=STRANGER, sender_id=STRANGER))
        self.assertEqual(started, [])

    def test_a_stranger_is_told_the_id_to_allow(self):
        client = FakeClient()
        _bot(client).handle(_message("/run ufo-sightings", chat_id=STRANGER, sender_id=STRANGER))
        self.assertIn(str(STRANGER), client.texts_to(STRANGER))

    def test_a_group_member_cannot_drive_the_bot(self):
        """A group's chat id belongs to the group. Authorising the conversation
        instead of the sender would hand it to every member."""
        group = -100123
        client = FakeClient()
        started = []
        bot = _bot(client, runner=lambda n, c, **_: started.append(n) or {})
        bot.handle(_message("/run ufo-sightings", chat_id=group, sender_id=STRANGER))
        self.assertEqual(started, [])

    def test_the_owner_is_served_even_from_a_group(self):
        group = -100123
        client = FakeClient()
        _bot(client).handle(_message("/niches", chat_id=group, sender_id=OWNER))
        self.assertIn("ufo-sightings", client.texts_to(group))

    def test_a_message_with_no_sender_is_refused(self):
        """Channel posts carry no author to authorise."""
        client = FakeClient()
        started = []
        bot = _bot(client, runner=lambda n, c, **_: started.append(n) or {})
        bot.handle({"chat": {"id": OWNER}, "text": "/run ufo-sightings"})
        self.assertEqual(started, [])

    def test_an_empty_allowlist_answers_no_one(self):
        client = FakeClient()
        bot = _bot(client, allowed=set())
        bot.handle(_message("/niches"))
        self.assertNotIn("RPM", client.texts_to(OWNER))

    def test_the_owner_is_served(self):
        client = FakeClient()
        _bot(client).handle(_message("/niches"))
        self.assertIn("ufo-sightings", client.texts_to(OWNER))


class TestCommands(unittest.TestCase):
    def test_start_lists_the_commands(self):
        client = FakeClient()
        _bot(client).handle(_message("/start"))
        self.assertIn("/run", client.texts_to(OWNER))

    def test_unknown_command_points_at_help(self):
        client = FakeClient()
        _bot(client).handle(_message("/nonsense"))
        self.assertIn("/help", client.texts_to(OWNER))

    def test_group_suffix_is_stripped_from_commands(self):
        """In groups Telegram appends @botname to every command."""
        client = FakeClient()
        _bot(client).handle(_message("/niches@growthbot"))
        self.assertIn("ufo-sightings", client.texts_to(OWNER))

    def test_run_without_a_niche_shows_usage(self):
        client = FakeClient()
        _bot(client).handle(_message("/run"))
        self.assertIn("/niches", client.texts_to(OWNER))

    def test_run_with_an_unknown_niche_is_refused(self):
        client = FakeClient()
        started = []
        bot = _bot(client, runner=lambda n, c, **_: started.append(n) or {})
        bot.handle(_message("/run not-a-pack"))
        self.assertEqual(started, [])
        self.assertIn("unknown niche", client.texts_to(OWNER))

    def test_status_is_idle_before_anything_runs(self):
        client = FakeClient()
        _bot(client).handle(_message("/status"))
        self.assertIn("parado", client.texts_to(OWNER))

    def test_a_non_numeric_count_is_rejected(self):
        client = FakeClient()
        _bot(client).handle(_message("/run ufo-sightings many"))
        self.assertIn("número", client.texts_to(OWNER))

    def test_empty_and_non_text_messages_are_ignored(self):
        client = FakeClient()
        bot = _bot(client)
        bot.handle({"chat": {"id": OWNER}})
        bot.handle({"text": "/niches"})
        self.assertEqual(client.messages, [])


class TestRenderQueue(unittest.TestCase):
    def test_a_render_runs_and_its_video_is_sent(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"x")
            client = FakeClient()

            def runner(niche_id, count, **_):
                return {
                    "succeeded": 1,
                    "total": 1,
                    "records": [
                        {
                            "status": "succeeded",
                            "files": [str(video)],
                            "caption": "a caption",
                        }
                    ],
                }

            bot = _bot(client, runner=runner)
            bot.handle(_message("/run ufo-sightings"))
            self.assertTrue(_wait_idle(bot))

            self.assertEqual(len(client.videos), 1)
            self.assertEqual(client.videos[0][2], "a caption")

    def test_only_one_batch_runs_at_a_time(self):
        """Two renders on two cores would take longer than the two in sequence."""
        client = FakeClient()
        release = threading.Event()
        def blocking(niche_id, count, **_):
            release.wait(timeout=5)
            return {"succeeded": 0, "total": 0, "records": []}

        bot = _bot(client, runner=blocking)
        bot.handle(_message("/run ufo-sightings"))
        bot.handle(_message("/run ufo-sightings"))
        release.set()
        self.assertIn("Já estou fazendo", client.texts_to(OWNER))

    def test_a_failing_render_reports_and_frees_the_queue(self):
        client = FakeClient()

        def runner(niche_id, count, **_):
            raise bot_module.ProduceError("ffmpeg died")

        bot = _bot(client, runner=runner)
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))

        self.assertIn("ffmpeg died", client.texts_to(OWNER))
        client.messages.clear()
        bot.handle(_message("/status"))
        self.assertIn("parado", client.texts_to(OWNER))

    def test_a_renderer_returning_nonsense_is_reported_not_swallowed(self):
        """The worker runs on its own thread; an escape there would leave the
        chat waiting for a message that never arrives."""
        client = FakeClient()
        bot = _bot(client, runner=lambda n, c, **_: "not a dict")
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        self.assertIn("not a result", client.texts_to(OWNER))

    def test_a_missing_file_is_not_sent(self):
        client = FakeClient()
        _bot(client)._send_video(OWNER, Path("/nonexistent.mp4"), "")
        self.assertEqual(client.videos, [])


def _fast_heartbeat():
    """Collapse the five-minute wait so a test can watch it fire."""
    return patch.multiple(bot_module, HEARTBEAT_SECONDS=0, HEARTBEAT_TICK=0.01)


def _wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestProgressReporting(unittest.TestCase):
    """A render runs for twenty minutes. Silence for that long, in a chat, is
    indistinguishable from a crash."""

    def test_reaching_a_long_phase_is_announced(self):
        client = FakeClient()
        release = threading.Event()

        def runner(niche_id, count, on_line=None):
            on_line("downloading videos from the Internet\n")
            release.wait(timeout=5)
            return {"succeeded": 0, "total": 0, "records": []}

        bot = _bot(client, runner=runner)
        with _fast_heartbeat():
            bot.handle(_message("/run ufo-sightings"))
            found = _wait_for(lambda: "desenhando as cenas" in client.texts_to(OWNER))
            release.set()
            self.assertTrue(_wait_idle(bot))
        self.assertTrue(found)

    def test_the_short_opening_phases_are_not_announced(self):
        """The four stages before the drawing take about two minutes together;
        a message for each would be noise, not news."""
        client = FakeClient()

        def runner(niche_id, count, on_line=None):
            for line in ("generating video script\n", "generating audio\n"):
                on_line(line)
            return {"succeeded": 0, "total": 0, "records": []}

        bot = _bot(client, runner=runner)
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        self.assertNotIn("roteiro", client.texts_to(OWNER))

    def test_the_watcher_never_sends_on_the_engines_own_thread(self):
        """on_line runs on the thread draining the engine's stdout. A slow
        Telegram there stops that pipe being read, which stalls the render and
        eats into its own timeout."""
        client = FakeClient()
        release = threading.Event()
        during_watch: list[int] = []

        def runner(niche_id, count, on_line=None):
            before = len(client.messages)
            for _ in range(20):
                on_line("image material rendered: /tmp/1.mp4\n")
            on_line("combining video: 1 => /tmp/a.mp4\n")
            during_watch.append(len(client.messages) - before)
            release.wait(timeout=5)
            return {"succeeded": 0, "total": 0, "records": []}

        bot = _bot(client, runner=runner)
        # The heartbeat is left asleep, so anything sent here came from watch.
        bot.handle(_message("/run ufo-sightings"))
        release.set()
        self.assertTrue(_wait_idle(bot))
        self.assertEqual(during_watch, [0])

    def test_a_quiet_stage_still_gets_a_heartbeat(self):
        """The final render goes twelve minutes without printing a line. Only
        a clock notices that, so this fires with no log line at all."""
        client = FakeClient()
        release = threading.Event()

        def runner(niche_id, count, on_line=None):
            on_line("generating audio\n")  # not a notable phase: no report
            release.wait(timeout=5)
            return {"succeeded": 0, "total": 0, "records": []}

        bot = _bot(client, runner=runner)
        with _fast_heartbeat():
            bot.handle(_message("/run ufo-sightings"))
            beat = _wait_for(lambda: "já faz" in client.texts_to(OWNER))
            release.set()
            self.assertTrue(_wait_idle(bot))
        self.assertTrue(beat, "the heartbeat never fired")

    def test_a_delivered_report_resets_the_heartbeat_clock(self):
        """Without the reset the heartbeat fires every tick, which is a
        message every fifteen seconds for the rest of the render."""
        client = FakeClient()
        bot = _bot(client)
        job = bot_module.RenderJob(niche_id="n", count=1, chat_id=OWNER)
        job.last_report = 0.0
        bot._progress_report(job)
        self.assertGreater(job.last_report, 0.0)

    def test_a_lost_message_does_not_buy_five_more_minutes_of_silence(self):
        """Resetting the clock on the attempt would let one failed send
        suppress the next report, which is the silence this exists to fix."""
        client = FakeClient()
        client.send_message = unittest.mock.Mock(side_effect=BotError("network"))
        bot = _bot(client)
        job = bot_module.RenderJob(niche_id="n", count=1, chat_id=OWNER)
        job.last_report = 0.0
        bot._progress_report(job)
        self.assertEqual(job.last_report, 0.0)

    def test_the_heartbeat_survives_an_unexpected_failure(self):
        """This thread dying silently produces exactly the silence it exists
        to prevent, and nothing restarts it."""
        client = FakeClient()
        calls = {"n": 0}

        def flaky(chat_id, text):
            # The first call is the /run acknowledgement; the failure belongs
            # to the heartbeat, which is what this is about.
            calls["n"] += 1
            if calls["n"] == 2:
                raise ValueError("an api body that was not an object")
            client.messages.append((chat_id, text))

        client.send_message = flaky
        release = threading.Event()
        bot = _bot(client, runner=lambda n, c, **_: release.wait(timeout=5) or {})
        with _fast_heartbeat():
            bot.handle(_message("/run ufo-sightings"))
            survived = _wait_for(lambda: calls["n"] >= 4)
            release.set()
            self.assertTrue(_wait_idle(bot))
        self.assertTrue(survived, "the heartbeat died on the first failure")

    def test_scenes_are_reported_in_batches_not_one_by_one(self):
        """Twenty scenes take seven minutes. One message each is spam; the
        stage change alone reports 0/20 and then goes quiet."""
        client = FakeClient()
        release = threading.Event()

        def runner(niche_id, count, on_line=None):
            on_line("downloading videos from the Internet\n")
            for _ in range(bot_module.SCENES_PER_REPORT):
                on_line("image material rendered: /tmp/1.mp4\n")
            release.wait(timeout=5)
            return {"succeeded": 0, "total": 0, "records": []}

        bot = _bot(client, runner=runner)
        with _fast_heartbeat():
            bot.handle(_message("/run ufo-sightings"))
            counted = _wait_for(
                lambda: f"{bot_module.SCENES_PER_REPORT}/" in client.texts_to(OWNER)
            )
            release.set()
            self.assertTrue(_wait_idle(bot))
        self.assertTrue(counted)

    def test_the_scene_total_is_the_number_the_plan_asks_for(self):
        """Counted off in the chat, so a denominator guessed with a different
        formula shows "16/15 cenas" on every render."""
        from growth.niche import load_niche
        from growth.plan import scene_count

        client = FakeClient()
        release = threading.Event()
        bot = _bot(client, runner=lambda n, c, **_: release.wait(timeout=5) or {})
        bot.handle(_message("/run ufo-sightings"))
        with bot._lock:
            total = bot._job.tracker.scenes_total
        release.set()
        self.assertTrue(_wait_idle(bot))
        self.assertEqual(total, scene_count(load_niche("ufo-sightings")))

    def test_status_reports_the_stage_while_the_render_runs(self):
        client = FakeClient()
        seen = threading.Event()
        release = threading.Event()

        def runner(niche_id, count, on_line=None):
            on_line("combining video: 1 => /tmp/a.mp4\n")
            seen.set()
            release.wait(timeout=5)
            return {"succeeded": 0, "total": 0, "records": []}

        bot = _bot(client, runner=runner)
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(seen.wait(timeout=5))
        client.messages.clear()
        bot.handle(_message("/status"))
        release.set()
        self.assertIn("montando o vídeo", client.texts_to(OWNER))

    def test_status_reports_elapsed_and_remaining_in_minutes(self):
        """Passing seconds where minutes are expected, or the reverse, is a
        silent sixty-fold error that every message would then carry."""
        client = FakeClient()
        release = threading.Event()

        def runner(niche_id, count, on_line=None):
            on_line("combining video: 1 => /tmp/a.mp4\n")
            release.wait(timeout=5)
            return {"succeeded": 0, "total": 0, "records": []}

        bot = _bot(client, runner=runner)
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_for(lambda: bot._job and bot._job.tracker.snapshot().phase))
        with bot._lock:
            bot._job.started_at -= 600  # ten minutes ago
        client.messages.clear()
        bot.handle(_message("/status"))
        release.set()
        self.assertTrue(_wait_idle(bot))
        text = client.texts_to(OWNER)
        self.assertIn("rodando há 10 min", text)
        self.assertRegex(text, r"faltam ~\d+ min")

    def test_status_says_when_it_is_uploading_rather_than_rendering(self):
        """/status claiming a render in progress, after the chat was told it
        finished, makes the bot contradict itself for the whole upload."""
        client = FakeClient()
        bot = _bot(client)
        job = bot_module.RenderJob(niche_id="ufo-sightings", count=1, chat_id=OWNER)
        job.delivering = True
        bot._job = job
        bot.handle(_message("/status"))
        bot._job = None
        self.assertIn("enviando os vídeos", client.texts_to(OWNER))

    def test_a_render_that_produced_nothing_still_says_so(self):
        """Otherwise the last thing the chat hears is a progress line, and the
        user waits for a video that is never coming."""
        client = FakeClient()
        bot = _bot(client, runner=lambda n, c, **_: {"succeeded": 0, "total": 2, "records": []})
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        self.assertIn("nenhum dos 2", client.texts_to(OWNER))

    def test_a_successful_batch_reports_how_many_came_out(self):
        client = FakeClient()
        bot = _bot(
            client,
            runner=lambda n, c, **_: {"succeeded": 1, "total": 2, "records": []},
        )
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        self.assertIn("1 de 2", client.texts_to(OWNER))

    def test_the_start_acknowledgement_arrives_before_any_progress(self):
        """Started after the thread, it can land below a stage report and read
        as though the bot answered out of order."""
        client = FakeClient()
        release = threading.Event()

        def runner(niche_id, count, on_line=None):
            on_line("combining video: 1 => /tmp/a.mp4\n")
            release.wait(timeout=5)
            return {"succeeded": 0, "total": 0, "records": []}

        bot = _bot(client, runner=runner)
        with _fast_heartbeat():
            bot.handle(_message("/run ufo-sightings"))
            _wait_for(lambda: "montando" in client.texts_to(OWNER))
            release.set()
            self.assertTrue(_wait_idle(bot))
        self.assertIn("Beleza", client.messages[0][1])

    def test_a_thread_that_cannot_start_does_not_wedge_the_queue(self):
        """Out of threads, the job would otherwise stay set forever: every
        later /run refused, /status showing a render that is not running."""
        client = FakeClient()
        bot = _bot(client)
        with patch.object(
            bot_module.threading, "Thread", side_effect=RuntimeError("can't start new thread")
        ):
            try:
                bot.handle(_message("/run ufo-sightings"))
            except RuntimeError:
                pass
        bot._run_job(bot._job) if bot._job else None
        with bot._lock:
            self.assertIsNone(bot._job)


class TestFailureIsExplained(unittest.TestCase):
    """The engine records the stage and the reason on every failed task, and
    the bot used to print neither. It told the operator to read a log that
    does not exist: the only loguru sink is the terminal, and the bot runs the
    engine quietly."""

    def _failed(self, stage="preflight", error="missing openai_image_base_url"):
        return {
            "succeeded": 0,
            "total": 1,
            "records": [{"status": "failed", "failed_stage": stage, "error": error}],
        }

    def test_the_stage_is_named_in_the_chat(self):
        client = FakeClient()
        bot = _bot(client, runner=lambda n, c, **_: self._failed())
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        self.assertIn("conferência da configuração", client.texts_to(OWNER))

    def test_the_engines_own_reason_is_repeated_verbatim(self):
        client = FakeClient()
        bot = _bot(client, runner=lambda n, c, **_: self._failed())
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        self.assertIn("missing openai_image_base_url", client.texts_to(OWNER))

    def test_it_no_longer_points_at_a_log_that_does_not_exist(self):
        client = FakeClient()
        bot = _bot(client, runner=lambda n, c, **_: self._failed())
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        self.assertNotIn("log do servidor", client.texts_to(OWNER))

    def test_an_unknown_stage_is_shown_rather_than_dropped(self):
        client = FakeClient()
        bot = _bot(client, runner=lambda n, c, **_: self._failed(stage="brand-new"))
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        self.assertIn("brand-new", client.texts_to(OWNER))

    def test_a_long_reason_is_truncated_to_fit_telegram(self):
        client = FakeClient()
        bot = _bot(client, runner=lambda n, c, **_: self._failed(error="x" * 5000))
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        for _, text in client.messages:
            self.assertLess(len(text), 4096)

    def test_an_error_with_a_tag_in_it_is_escaped(self):
        """Telegram drops the whole message on a stray tag, and this is the
        message that must never be lost."""
        client = FakeClient()
        bot = _bot(client, runner=lambda n, c, **_: self._failed(error="got <Response [401]>"))
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        self.assertIn("&lt;Response", client.texts_to(OWNER))

    def test_many_failures_are_capped(self):
        client = FakeClient()
        result = {
            "succeeded": 0,
            "total": 9,
            "records": [
                {"status": "failed", "failed_stage": "script", "error": f"reason {i}"}
                for i in range(9)
            ],
        }
        bot = _bot(client, runner=lambda n, c, **_: result)
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        text = client.texts_to(OWNER)
        self.assertIn(f"mais {9 - bot_module.MAX_FAILURES_SHOWN}", text)


class TestRunPreflight(unittest.TestCase):
    """A pack that draws its own scenes needs config the engine only checks
    after the batch starts — so the chat was promised twenty minutes and told
    five seconds later that nothing came out."""

    def test_a_pack_missing_its_image_config_is_refused_before_starting(self):
        client = FakeClient()
        started = []
        bot = _bot(client, runner=lambda n, c, **_: started.append(n) or {})
        with patch("app.services.material.is_openai_image_enabled", return_value=False):
            bot.handle(_message("/run ufo-sightings"))
        self.assertEqual(started, [])
        self.assertIn("growth config --niche", client.texts_to(OWNER))

    def test_the_refusal_names_the_command_that_fixes_it(self):
        client = FakeClient()
        bot = _bot(client)
        with patch("app.services.material.is_openai_image_enabled", return_value=False):
            bot.handle(_message("/run ufo-sightings"))
        self.assertIn("ufo-sightings", client.texts_to(OWNER))

    def test_a_configured_pack_starts_normally(self):
        client = FakeClient()
        started = []
        bot = _bot(client, runner=lambda n, c, **_: started.append(n) or {})
        with patch("app.services.material.is_openai_image_enabled", return_value=True):
            bot.handle(_message("/run ufo-sightings"))
            self.assertTrue(_wait_idle(bot))
        self.assertEqual(started, ["ufo-sightings"])


class TestDoctorCommand(unittest.TestCase):
    """Every failure hit today was one of these checks, and each cost a trip
    to a terminal the owner explicitly did not want to use."""

    def _check(self, name, status, detail="fine", fix=""):
        from growth.doctor import Check

        return Check(name=name, status=status, detail=detail, fix=fix)

    def _wait_for_second_message(self, client):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if len(client.messages) >= 2:
                return True
            time.sleep(0.01)
        return False

    def test_it_reports_every_check(self):
        from growth.doctor import OK

        client = FakeClient()
        bot = _bot(client)
        checks = [self._check("python", OK), self._check("ffmpeg", OK)]
        with patch.object(bot_module, "run_checks", return_value=checks):
            bot.handle(_message("/doctor"))
            self.assertTrue(self._wait_for_second_message(client))
        text = client.texts_to(OWNER)
        self.assertIn("python", text)
        self.assertIn("ffmpeg", text)

    def test_a_failure_carries_its_fix(self):
        from growth.doctor import FAIL, OK

        client = FakeClient()
        bot = _bot(client)
        checks = [
            self._check("materials", FAIL, "flux returned no image", "check the api key"),
            self._check("voice", OK),
        ]
        with patch.object(bot_module, "run_checks", return_value=checks):
            bot.handle(_message("/doctor"))
            self.assertTrue(self._wait_for_second_message(client))
        text = client.texts_to(OWNER)
        self.assertIn("check the api key", text)
        self.assertIn("1 check(s) com falha", text)

    def test_all_green_says_it_is_ready(self):
        from growth.doctor import OK

        client = FakeClient()
        bot = _bot(client)
        with patch.object(bot_module, "run_checks", return_value=[self._check("python", OK)]):
            bot.handle(_message("/doctor"))
            self.assertTrue(self._wait_for_second_message(client))
        self.assertIn("pode mandar /run", client.texts_to(OWNER))

    def test_an_unknown_niche_is_refused_without_running_checks(self):
        client = FakeClient()
        bot = _bot(client)
        with patch.object(bot_module, "run_checks") as checks:
            bot.handle(_message("/doctor not-a-pack"))
        checks.assert_not_called()

    def test_a_crash_in_the_checks_still_answers_the_chat(self):
        """It runs on its own thread; an escape there leaves the chat waiting
        for a reply that never comes."""
        client = FakeClient()
        bot = _bot(client)
        with patch.object(bot_module, "run_checks", side_effect=RuntimeError("boom")):
            bot.handle(_message("/doctor"))
            self.assertTrue(self._wait_for_second_message(client))
        self.assertIn("boom", client.texts_to(OWNER))


class TestLastCommand(unittest.TestCase):
    def test_it_announces_the_send_and_then_sends(self):
        client = FakeClient()
        bot = _bot(client)
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"x")
            item = unittest.mock.MagicMock(path=video, subject="a subject")
            with patch.object(bot_module, "review_all", return_value=[item]):
                bot.handle(_message("/last 1"))
        self.assertIn("Enviando", client.texts_to(OWNER))
        self.assertEqual(len(client.videos), 1)

    def test_nothing_produced_yet_is_said_plainly(self):
        client = FakeClient()
        bot = _bot(client)
        with patch.object(bot_module, "review_all", return_value=[]):
            bot.handle(_message("/last"))
        self.assertIn("Ainda não produzi", client.texts_to(OWNER))

    def test_an_empty_reply_sends_no_message(self):
        """Telegram rejects an empty sendMessage; /last answers with videos."""
        client = FakeClient()
        bot = _bot(client)
        with patch.object(bot, "_cmd_status", return_value=""):
            bot.handle(_message("/status"))
        self.assertEqual(client.messages, [])


class TestUploadFailureNotice(unittest.TestCase):
    def test_the_reason_is_escaped_before_it_is_sent(self):
        """The message explaining why a video did not arrive is the worst one
        to lose, and Telegram drops any message with a stray tag in it."""
        client = FakeClient()
        client.send_video = unittest.mock.Mock(
            side_effect=BotError("bad <Response [400]>")
        )
        bot = _bot(client)
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"x")
            bot._send_video(OWNER, video, "")
        text = client.texts_to(OWNER)
        self.assertIn("&lt;Response", text)
        self.assertIn("v.mp4", text)


class TestHtmlSafety(unittest.TestCase):
    """Every reply is sent with parse_mode=HTML. Telegram rejects the whole
    message if it carries a stray < or &, and a rejected message is silence:
    the user is told nothing at all."""

    def _pack(self, **overrides):
        pack = unittest.mock.MagicMock()
        pack.id = overrides.get("id", "ufo-sightings")
        pack.economics.rpm_range = (3.0, 8.0)
        pack.economics.competition = overrides.get("competition", "low")
        return pack

    def test_a_pack_id_with_angle_brackets_is_escaped(self):
        client = FakeClient()
        bot = _bot(client)
        with patch.object(bot_module, "load_all_niches", return_value=[self._pack(id="ai <tools>")]):
            bot.handle(_message("/niches"))
        self.assertNotIn("<tools>", client.texts_to(OWNER))
        self.assertIn("&lt;tools&gt;", client.texts_to(OWNER))

    def test_a_competition_value_with_an_ampersand_is_escaped(self):
        """Nothing constrains this field; it is free text from a TOML file."""
        client = FakeClient()
        bot = _bot(client)
        with patch.object(
            bot_module, "load_all_niches", return_value=[self._pack(competition="high & rising")]
        ):
            bot.handle(_message("/niches"))
        self.assertIn("&amp; rising", client.texts_to(OWNER))

    def test_the_busy_reply_escapes_the_running_niche(self):
        client = FakeClient()
        release = threading.Event()
        bot = _bot(client, runner=lambda n, c, **_: release.wait(timeout=5) or {})
        bot.handle(_message("/run ufo-sightings"))
        with bot._lock:
            bot._job.niche_id = "a<b"
        bot.handle(_message("/run ufo-sightings"))
        release.set()
        self.assertIn("a&lt;b", client.texts_to(OWNER))

    def test_a_handler_returning_nothing_sends_no_message(self):
        """Telegram rejects an empty sendMessage; /last replies by sending
        videos, not text."""
        client = FakeClient()
        bot = _bot(client)
        with patch.object(bot, "_cmd_status", return_value=""):
            bot.handle(_message("/status"))
        self.assertEqual(client.messages, [])


class TestUploadLimit(unittest.TestCase):
    def test_a_file_over_the_limit_is_reported_with_its_path(self):
        """Bots may upload 50 MB; a long-form cut can exceed it."""
        client = bot_module.TelegramClient("token")
        sent: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            big = Path(temp) / "big.mp4"
            big.write_bytes(b"0")
            with patch.object(Path, "stat") as stat:
                stat.return_value.st_size = bot_module.MAX_UPLOAD_BYTES + 1
                with patch.object(client, "send_message", side_effect=lambda c, t: sent.append(t)):
                    client.send_video(OWNER, big)
        self.assertIn(str(temp), sent[0])
        self.assertIn("big.mp4", sent[0])


class TestTelegramClient(unittest.TestCase):
    """The fake client in the other tests stands in for this one, so nothing
    here was exercised until a real poll crashed on the first call."""

    def _response(self, payload):
        response = unittest.mock.MagicMock()
        response.json.return_value = payload
        return response

    def test_get_updates_sends_the_long_poll_window_as_a_parameter(self):
        client = bot_module.TelegramClient("token")
        with patch("growth.bot.requests.post") as post:
            post.return_value = self._response({"ok": True, "result": []})
            client.get_updates(offset=7)
        kwargs = post.call_args.kwargs
        # The api parameter and the http deadline are different things: the
        # request has to outlast the poll it is asking Telegram to hold open.
        self.assertEqual(kwargs["data"]["timeout"], bot_module.POLL_TIMEOUT)
        self.assertEqual(kwargs["data"]["offset"], 7)
        self.assertGreater(kwargs["timeout"], bot_module.POLL_TIMEOUT)

    def test_get_updates_omits_the_offset_on_the_first_poll(self):
        client = bot_module.TelegramClient("token")
        with patch("growth.bot.requests.post") as post:
            post.return_value = self._response({"ok": True, "result": []})
            client.get_updates(offset=None)
        self.assertNotIn("offset", post.call_args.kwargs["data"])

    def test_a_non_list_result_does_not_reach_the_caller(self):
        client = bot_module.TelegramClient("token")
        with patch("growth.bot.requests.post") as post:
            post.return_value = self._response({"ok": True, "result": {}})
            self.assertEqual(client.get_updates(None), [])

    def test_an_api_error_names_the_method_and_hides_the_token(self):
        client = bot_module.TelegramClient("secret-token")
        with patch("growth.bot.requests.post") as post:
            post.return_value = self._response(
                {"ok": False, "description": "Unauthorized"}
            )
            with self.assertRaises(BotError) as context:
                client.get_me()
        message = str(context.exception)
        self.assertIn("getMe", message)
        self.assertIn("Unauthorized", message)
        self.assertNotIn("secret-token", message)

    def test_send_message_posts_the_text_to_the_chat(self):
        client = bot_module.TelegramClient("token")
        with patch("growth.bot.requests.post") as post:
            post.return_value = self._response({"ok": True, "result": {}})
            client.send_message(OWNER, "hello")
        data = post.call_args.kwargs["data"]
        self.assertEqual(data["chat_id"], OWNER)
        self.assertEqual(data["text"], "hello")

    def test_an_empty_token_is_refused_at_construction(self):
        with self.assertRaises(BotError):
            bot_module.TelegramClient("   ")


class TestWebhookConflict(unittest.TestCase):
    """Telegram refuses getUpdates while a webhook is registered, and a token
    that was ever pointed at one keeps it until it is deleted."""

    def test_a_registered_webhook_is_reported_and_removed_at_startup(self):
        client = unittest.mock.MagicMock()
        client.get_me.return_value = {"username": "bot"}
        client.get_webhook_info.return_value = {"url": "https://example.test/hook"}
        with patch.object(bot_module, "TelegramClient", return_value=client):
            with patch.object(bot_module, "poll_forever"):
                bot_module.run({"telegram_bot_token": "t", "telegram_allowed_users": [1]})
        client.delete_webhook.assert_called_once()

    def test_no_webhook_means_nothing_is_deleted(self):
        client = unittest.mock.MagicMock()
        client.get_me.return_value = {"username": "bot"}
        client.get_webhook_info.return_value = {}
        with patch.object(bot_module, "TelegramClient", return_value=client):
            with patch.object(bot_module, "poll_forever"):
                bot_module.run({"telegram_bot_token": "t", "telegram_allowed_users": [1]})
        client.delete_webhook.assert_not_called()

    def test_repeated_rejections_stop_instead_of_looping_forever(self):
        """Retrying cannot clear a webhook or a second poller, so a loop that
        only logs would hide the problem behind identical lines."""
        client = FakeClient()
        calls = {"n": 0}

        def rejected(offset):
            calls["n"] += 1
            raise BotError("getUpdates: Conflict: can't use getUpdates method while webhook is active")

        client.get_updates = rejected
        with patch.object(bot_module, "RETRY_DELAY", 0):
            poll_forever(_bot(client), client, stop=threading.Event())
        self.assertEqual(calls["n"], bot_module.MAX_REJECTIONS)

    def test_a_successful_poll_forgets_earlier_rejections(self):
        """A webhook cleared mid-flight should not count against a later one."""
        client = FakeClient()
        stop = threading.Event()
        calls = {"n": 0}

        def flaky(offset):
            calls["n"] += 1
            if calls["n"] < bot_module.MAX_REJECTIONS:
                raise BotError("Conflict: webhook is active")
            if calls["n"] > bot_module.MAX_REJECTIONS + 2:
                stop.set()
            return []

        client.get_updates = flaky
        with patch.object(bot_module, "RETRY_DELAY", 0):
            poll_forever(_bot(client), client, stop=stop)
        self.assertGreater(calls["n"], bot_module.MAX_REJECTIONS)


class TestSettings(unittest.TestCase):
    def test_a_missing_token_names_botfather(self):
        with self.assertRaises(BotError) as context:
            load_settings({})
        self.assertIn("BotFather", str(context.exception))

    def test_user_ids_are_parsed_to_numbers(self):
        _, users = load_settings(
            {"telegram_bot_token": "t", "telegram_allowed_users": ["1", 2]}
        )
        self.assertEqual(users, {1, 2})

    def test_unparseable_ids_are_skipped_not_fatal(self):
        _, users = load_settings(
            {"telegram_bot_token": "t", "telegram_allowed_users": ["7", "abc"]}
        )
        self.assertEqual(users, {7})

    def test_a_single_id_need_not_be_a_list(self):
        _, users = load_settings({"telegram_bot_token": "t", "telegram_allowed_users": 5})
        self.assertEqual(users, {5})

    def test_the_older_key_name_still_works(self):
        """In a private chat it holds the same number, so a config written
        before the rename keeps working."""
        _, users = load_settings(
            {"telegram_bot_token": "t", "telegram_allowed_chats": ["9"]}
        )
        self.assertEqual(users, {9})


class TestPolling(unittest.TestCase):
    def test_a_network_failure_does_not_end_the_loop(self):
        client = FakeClient()
        stop = threading.Event()
        calls = {"n": 0}

        def flaky(offset):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ConnectionError("no route to host")
            stop.set()
            return []

        client.get_updates = flaky
        with patch.object(bot_module, "RETRY_DELAY", 0):
            poll_forever(_bot(client), client, stop=stop)
        self.assertGreaterEqual(calls["n"], 2)

    def test_a_bad_message_does_not_end_the_loop(self):
        client = FakeClient()
        stop = threading.Event()
        bot = _bot(client)
        updates = [[{"update_id": 1, "message": _message("/niches")}]]

        def once(offset):
            if updates:
                return updates.pop(0)
            stop.set()
            return []

        client.get_updates = once
        with patch.object(bot, "handle", side_effect=RuntimeError("boom")):
            with patch.object(bot_module, "RETRY_DELAY", 0):
                poll_forever(bot, client, stop=stop)
        self.assertTrue(stop.is_set())


if __name__ == "__main__":
    unittest.main()
