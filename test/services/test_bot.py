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
        runner=runner or (lambda niche_id, count: {"succeeded": 0, "total": 0, "records": []}),
    )


class TestAccessControl(unittest.TestCase):
    """A bot token is discoverable and anyone can message it. A render spends
    api quota and an hour of cpu, so only the owner may start one."""

    def test_a_stranger_cannot_start_a_render(self):
        client = FakeClient()
        started = []
        bot = _bot(client, runner=lambda n, c: started.append(n) or {})
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
        bot = _bot(client, runner=lambda n, c: started.append(n) or {})
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
        bot = _bot(client, runner=lambda n, c: started.append(n) or {})
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
        self.assertIn("usage", client.texts_to(OWNER))

    def test_run_with_an_unknown_niche_is_refused(self):
        client = FakeClient()
        started = []
        bot = _bot(client, runner=lambda n, c: started.append(n) or {})
        bot.handle(_message("/run not-a-pack"))
        self.assertEqual(started, [])
        self.assertIn("unknown niche", client.texts_to(OWNER))

    def test_status_is_idle_before_anything_runs(self):
        client = FakeClient()
        _bot(client).handle(_message("/status"))
        self.assertIn("idle", client.texts_to(OWNER))

    def test_a_non_numeric_count_is_rejected(self):
        client = FakeClient()
        _bot(client).handle(_message("/run ufo-sightings many"))
        self.assertIn("number", client.texts_to(OWNER))

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

            def runner(niche_id, count):
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
        def blocking(niche_id, count):
            release.wait(timeout=5)
            return {"succeeded": 0, "total": 0, "records": []}

        bot = _bot(client, runner=blocking)
        bot.handle(_message("/run ufo-sightings"))
        bot.handle(_message("/run ufo-sightings"))
        release.set()
        self.assertIn("already rendering", client.texts_to(OWNER))

    def test_a_failing_render_reports_and_frees_the_queue(self):
        client = FakeClient()

        def runner(niche_id, count):
            raise bot_module.ProduceError("ffmpeg died")

        bot = _bot(client, runner=runner)
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))

        self.assertIn("ffmpeg died", client.texts_to(OWNER))
        client.messages.clear()
        bot.handle(_message("/status"))
        self.assertIn("idle", client.texts_to(OWNER))

    def test_a_renderer_returning_nonsense_is_reported_not_swallowed(self):
        """The worker runs on its own thread; an escape there would leave the
        chat waiting for a message that never arrives."""
        client = FakeClient()
        bot = _bot(client, runner=lambda n, c: "not a dict")
        bot.handle(_message("/run ufo-sightings"))
        self.assertTrue(_wait_idle(bot))
        self.assertIn("not a result", client.texts_to(OWNER))

    def test_a_missing_file_is_not_sent(self):
        client = FakeClient()
        _bot(client)._send_video(OWNER, Path("/nonexistent.mp4"), "")
        self.assertEqual(client.videos, [])


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
        self.assertIn("over the", sent[0])
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
