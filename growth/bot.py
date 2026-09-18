"""Drive the pipeline from Telegram, and receive the videos there.

A render takes twenty minutes on a small server and produces a file you have
to watch before deciding anything. Doing that over SSH means being at a
computer; doing it over Telegram means a phone. The bot starts batches,
reports what the engine is doing, and sends each finished video to the chat
that asked for it.

It polls rather than serving a webhook, so the host needs no open port, no
domain and no certificate. That matters on a free-tier VPS, where opening a
port is the fiddliest part of the setup.
"""

from __future__ import annotations

import html
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests
from loguru import logger

from growth.niche import NicheError, load_all_niches, load_niche
from growth.plan import PlanError, create_plan
from growth.produce import ProduceError, produce
from growth.review import FAIL, review_all

API_ROOT = "https://api.telegram.org"
# Bots may upload at most 50 MB. A 90-second vertical video lands near 24 MB,
# so the ceiling is only reached by long-form cuts.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# Long-poll window. Telegram holds the request open until an update arrives.
POLL_TIMEOUT = 30
# Uploading tens of megabytes over a home-grade uplink is not quick.
UPLOAD_TIMEOUT = 600
# Back-off after a network failure, so a flapping connection does not spin.
RETRY_DELAY = 5


class BotError(RuntimeError):
    """Raised when the bot cannot start or cannot reach Telegram."""


@dataclass
class RenderJob:
    """One batch, tracked so /status can answer while it runs."""

    niche_id: str
    count: int
    chat_id: int
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_minutes(self) -> float:
        return (time.monotonic() - self.started_at) / 60


class TelegramClient:
    """The slice of the Bot API this needs, over plain HTTP."""

    def __init__(self, token: str):
        if not token.strip():
            raise BotError("the telegram bot token is empty")
        self._base = f"{API_ROOT}/bot{token.strip()}"

    def _call(self, method: str, timeout: int = 30, **payload: Any) -> dict[str, Any]:
        response = requests.post(f"{self._base}/{method}", data=payload, timeout=timeout)
        body = response.json()
        if not body.get("ok"):
            # The description names the cause; the token must never be echoed.
            raise BotError(f"{method}: {body.get('description', 'unknown error')}")
        return body.get("result", {})

    def get_me(self) -> dict[str, Any]:
        return self._call("getMe")

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": POLL_TIMEOUT}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", timeout=POLL_TIMEOUT + 15, **payload)
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str) -> None:
        self._call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")

    def send_video(self, chat_id: int, path: Path, caption: str = "") -> None:
        """Upload a rendered video, or say why it could not be uploaded."""
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            self.send_message(
                chat_id,
                f"{html.escape(path.name)} is {size / 1024 / 1024:.0f} MB, over the "
                f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB a bot may upload.\n"
                f"It is on the server at <code>{html.escape(str(path))}</code>",
            )
            return
        with path.open("rb") as handle:
            response = requests.post(
                f"{self._base}/sendVideo",
                data={
                    "chat_id": chat_id,
                    "caption": caption[:1024],
                    "supports_streaming": True,
                },
                files={"video": (path.name, handle, "video/mp4")},
                timeout=UPLOAD_TIMEOUT,
            )
        body = response.json()
        if not body.get("ok"):
            raise BotError(f"sendVideo: {body.get('description', 'unknown error')}")


class GrowthBot:
    """Command handling and the one-batch-at-a-time render queue."""

    def __init__(
        self,
        client: TelegramClient,
        allowed_chats: set[int],
        runner: Callable[..., dict[str, Any]] | None = None,
    ):
        self.client = client
        self.allowed_chats = allowed_chats
        # Injected so the render can be exercised without spending twenty
        # minutes and an api quota.
        self._runner = runner or self._render
        self._job: RenderJob | None = None
        self._lock = threading.Lock()

    # -- rendering ---------------------------------------------------------

    def _render(self, niche_id: str, count: int) -> dict[str, Any]:
        plan = create_plan(niche_id, count=count)
        return produce(Path(plan["plan_file"]).parent, quiet=True)

    def _run_job(self, job: RenderJob) -> None:
        """Render, then report. Runs on its own thread, so nothing here may
        raise: an escaping exception would leave the chat waiting forever for
        a message that never comes."""
        try:
            result = self._runner(job.niche_id, job.count)
            self._report(job, result)
        except (PlanError, ProduceError, NicheError) as exc:
            self._say(job.chat_id, f"❌ render failed: {html.escape(str(exc))}")
        except Exception as exc:  # a crash must not take the bot down with it
            logger.exception("render job failed")
            self._say(job.chat_id, f"❌ unexpected failure: {html.escape(str(exc))}")
        finally:
            with self._lock:
                self._job = None

    def _report(self, job: RenderJob, result: Any) -> None:
        if not isinstance(result, dict):
            raise BotError(f"the renderer returned {type(result).__name__}, not a result")
        succeeded = result.get("succeeded", 0)
        total = result.get("total", 0)
        self._say(
            job.chat_id,
            f"✅ rendered {succeeded}/{total} in {job.elapsed_minutes:.0f} min",
        )
        for record in result.get("records", []):
            if record.get("status") != "succeeded":
                continue
            for file_path in record.get("files", []):
                self._send_video(job.chat_id, Path(file_path), record.get("caption", ""))

    def _send_video(self, chat_id: int, path: Path, caption: str) -> None:
        if not path.is_file():
            return
        try:
            self.client.send_video(chat_id, path, caption)
        except (BotError, requests.RequestException) as exc:
            self._say(chat_id, f"⚠️ could not send {html.escape(path.name)}: {exc}")

    def _say(self, chat_id: int, text: str) -> None:
        try:
            self.client.send_message(chat_id, text)
        except (BotError, requests.RequestException) as exc:
            logger.warning(f"could not reply to {chat_id}: {exc}")

    # -- commands ----------------------------------------------------------

    def _cmd_start(self, chat_id: int, _: list[str]) -> str:
        return (
            "<b>Growth bot</b>\n\n"
            "/niches — packs and what they earn\n"
            "/run &lt;niche&gt; [count] — render a batch\n"
            "/status — what is running now\n"
            "/review — check recent videos before posting\n"
            "/last [n] — resend recent videos\n\n"
            f"This chat id is <code>{chat_id}</code>."
        )

    def _cmd_niches(self, _: int, __: list[str]) -> str:
        packs = load_all_niches()
        if not packs:
            return "no niche packs found"
        lines = ["<b>Packs</b>, best estimated return first:"]
        for pack in packs:
            low, high = pack.economics.rpm_range
            lines.append(
                f"<code>{pack.id}</code> — RPM ${low:.2f}-${high:.2f}, "
                f"{pack.economics.competition} competition"
            )
        return "\n".join(lines)

    def _cmd_run(self, chat_id: int, args: list[str]) -> str:
        if not args:
            return "usage: /run &lt;niche&gt; [count] — see /niches"
        niche_id = args[0]
        try:
            niche = load_niche(niche_id)
        except NicheError as exc:
            return f"❌ {html.escape(str(exc))}"

        count = 1
        if len(args) > 1:
            try:
                count = max(1, min(int(args[1]), 5))
            except ValueError:
                return "count must be a number"

        with self._lock:
            if self._job is not None:
                return (
                    f"⏳ already rendering {self._job.niche_id}, "
                    f"{self._job.elapsed_minutes:.0f} min in. One at a time."
                )
            job = RenderJob(niche_id=niche.id, count=count, chat_id=chat_id)
            self._job = job

        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
        return (
            f"▶️ rendering {count} × <code>{niche.id}</code>.\n"
            f"Roughly {20 * count} minutes; the videos arrive here when done."
        )

    def _cmd_status(self, _: int, __: list[str]) -> str:
        with self._lock:
            job = self._job
        if job is None:
            return "idle"
        return (
            f"⏳ {job.niche_id}, {job.count} video(s), "
            f"running for {job.elapsed_minutes:.0f} min"
        )

    def _cmd_review(self, _: int, args: list[str]) -> str:
        limit = 5
        if args:
            try:
                limit = max(1, min(int(args[0]), 20))
            except ValueError:
                pass
        reviews = review_all(limit=limit)
        if not reviews:
            return "nothing produced yet"
        lines = []
        for item in reviews:
            mark = "❌" if item.status == FAIL else "✅"
            lines.append(
                f"{mark} {html.escape(item.subject[:48])} — "
                f"{item.duration:.0f}s, {item.words} words"
            )
            for _level, message in item.issues:
                lines.append(f"   ↳ {html.escape(message)}")
        return "\n".join(lines)

    def _cmd_last(self, chat_id: int, args: list[str]) -> str:
        count = 1
        if args:
            try:
                count = max(1, min(int(args[0]), 5))
            except ValueError:
                pass
        reviews = review_all(limit=count)
        if not reviews:
            return "nothing produced yet"
        for item in reviews[-count:]:
            self._send_video(chat_id, item.path, item.subject)
        return f"sending {min(count, len(reviews))} video(s)"

    COMMANDS: dict[str, str] = {
        "/start": "_cmd_start",
        "/help": "_cmd_start",
        "/niches": "_cmd_niches",
        "/run": "_cmd_run",
        "/status": "_cmd_status",
        "/review": "_cmd_review",
        "/last": "_cmd_last",
    }

    def handle(self, message: dict[str, Any]) -> None:
        """Dispatch one incoming message."""
        chat_id = int(message.get("chat", {}).get("id", 0))
        text = str(message.get("text", "")).strip()
        if not chat_id or not text:
            return

        # Anyone can find a bot and message it. An unlisted chat is told its
        # id so the owner can allow it, and nothing else happens: a render
        # spends api quota and an hour of someone else's cpu.
        if chat_id not in self.allowed_chats:
            logger.warning(f"ignoring message from unlisted chat {chat_id}")
            self._say(
                chat_id,
                "This bot only answers its owner.\n"
                f"Your chat id is <code>{chat_id}</code>.",
            )
            return

        parts = text.split()
        # Telegram appends @botname to commands in groups.
        command = parts[0].split("@", 1)[0].lower()
        handler_name = self.COMMANDS.get(command)
        if handler_name is None:
            self._say(chat_id, "unknown command — try /help")
            return
        reply = getattr(self, handler_name)(chat_id, parts[1:])
        if reply:
            self._say(chat_id, reply)


def poll_forever(bot: GrowthBot, client: TelegramClient, stop: threading.Event | None = None) -> None:
    """Read updates until stopped, surviving transient network failures."""
    stop = stop or threading.Event()
    offset: int | None = None
    while not stop.is_set():
        try:
            updates = client.get_updates(offset)
        except requests.RequestException as exc:
            logger.warning(f"telegram unreachable: {exc}")
            stop.wait(RETRY_DELAY)
            continue
        except BotError as exc:
            # A conflicting poller or a webhook still set is a configuration
            # problem that retrying cannot solve.
            logger.error(f"telegram rejected the poll: {exc}")
            stop.wait(RETRY_DELAY)
            continue

        for update in updates:
            offset = int(update.get("update_id", 0)) + 1
            message = update.get("message") or update.get("edited_message")
            if message:
                try:
                    bot.handle(message)
                except Exception:  # one bad message must not end the loop
                    logger.exception("failed to handle an update")


def load_settings(app_config: dict[str, Any] | None = None) -> tuple[str, set[int]]:
    """Read the token and the chats allowed to drive it."""
    if app_config is None:
        from app.config import config

        app_config = config.app

    token = str(app_config.get("telegram_bot_token", "") or "").strip()
    if not token:
        raise BotError(
            "telegram_bot_token is not set; get one from @BotFather then run "
            "python -m growth config --telegram-token <token>"
        )

    raw = app_config.get("telegram_allowed_chats") or []
    if isinstance(raw, (str, int)):
        raw = [raw]
    chats: set[int] = set()
    for value in raw:
        try:
            chats.add(int(str(value).strip()))
        except (TypeError, ValueError):
            continue
    return token, chats


def run(app_config: dict[str, Any] | None = None) -> int:
    """Start the bot and poll until interrupted."""
    token, allowed = load_settings(app_config)
    client = TelegramClient(token)
    identity = client.get_me()
    name = identity.get("username", "unknown")

    if not allowed:
        logger.warning(
            "telegram_allowed_chats is empty: the bot will answer no one. "
            "Message the bot, then add the chat id it replies with."
        )
    logger.info(f"telegram bot @{name} polling; allowed chats: {sorted(allowed) or 'none'}")

    bot = GrowthBot(client, allowed)
    try:
        poll_forever(bot, client)
    except KeyboardInterrupt:
        logger.info("stopped")
    return 0
