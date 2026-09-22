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
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests
from loguru import logger

from growth.configure import load_app_config
from growth.doctor import FAIL as CHECK_FAIL
from growth.doctor import OK as CHECK_OK
from growth.niche import NicheError, load_all_niches, load_niche
from growth.plan import PlanError, create_plan, scene_count
from growth.produce import REPO_ROOT, ProduceError, produce
from growth.progress import ProgressTracker
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
# Rejections in a row before giving up. A couple of retries cover a webhook
# being removed as the bot starts; beyond that the cause is not transient.
MAX_REJECTIONS = 3
# How often to speak up unprompted while a render runs, and how often to check
# whether it is time to. The final render phase can go twelve minutes without
# printing a single line, and silence that long reads as a crash.
HEARTBEAT_SECONDS = 300
HEARTBEAT_TICK = 15
# Scenes between updates while images are being drawn. One message per scene
# would be twenty in seven minutes; the phase change alone would report 0/20
# and then say nothing until the next phase.
SCENES_PER_REPORT = 5
# The checks call three providers; a slow one must not hang the thread.
DOCTOR_TIMEOUT = 120


class BotError(RuntimeError):
    """Raised when the bot cannot start or cannot reach Telegram."""


# Phases worth interrupting someone for. The four before these take about
# two minutes together, so announcing each would be noise.
NOTABLE_PHASES = frozenset({"materials", "combining", "rendering"})

# The engine records one of these on a failed task. Naming the stage in the
# chat is the difference between a fixable message and a shrug: there is no
# log file to fall back on, since the only loguru sink is the terminal and the
# bot runs the engine quietly.
STAGE_NAMES: dict[str, str] = {
    "preflight": "conferência da configuração",
    "script": "escrita do roteiro",
    "terms": "escolha das cenas",
    "audio": "gravação da narração",
    "materials": "geração das imagens",
    "video": "montagem do vídeo",
    "pipeline": "execução da tarefa",
    "runtime": "execução da tarefa",
    "unknown": "etapa não identificada",
}
# Room for the heading and a couple of records inside Telegram's 4096 limit.
MAX_ERROR_CHARS = 700
MAX_FAILURES_SHOWN = 3


@dataclass(frozen=True)
class _Check:
    """One row of the doctor's json output."""

    name: str
    status: str
    detail: str
    fix: str = ""


@dataclass
class RenderJob:
    """One batch, tracked so /status can answer while it runs."""

    niche_id: str
    count: int
    chat_id: int
    tracker: ProgressTracker = field(default_factory=ProgressTracker)
    started_at: float = field(default_factory=time.monotonic)
    # When the chat last heard anything, so the heartbeat stays quiet while
    # phase changes are already carrying the news.
    last_report: float = field(default_factory=time.monotonic)
    # Scene count at the last update, so one is never sent twice.
    scenes_reported: int = 0
    # Set by the log watcher, cleared by the thread that does the talking.
    report_pending: bool = False
    # Rendering is done; the files are being uploaded, which also takes a while.
    delivering: bool = False

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def elapsed_minutes(self) -> float:
        return self.elapsed_seconds / 60


class TelegramClient:
    """The slice of the Bot API this needs, over plain HTTP."""

    def __init__(self, token: str):
        if not token.strip():
            raise BotError("the telegram bot token is empty")
        self._base = f"{API_ROOT}/bot{token.strip()}"

    def _call(
        self, method: str, *, http_timeout: int = 30, **payload: Any
    ) -> dict[str, Any]:
        """Call one Bot API method.

        The HTTP timeout is named apart from the payload because getUpdates
        takes a Telegram parameter also called ``timeout``: sharing the name
        made the two collide as soon as the first poll ran.
        """
        response = requests.post(
            f"{self._base}/{method}", data=payload, timeout=http_timeout
        )
        body = response.json()
        # A proxy or captive portal can answer with valid JSON that is not an
        # object. Left alone, .get() would raise AttributeError past every
        # handler that expects a BotError, and kill the thread that called it.
        if not isinstance(body, dict):
            raise BotError(f"{method}: the api returned {type(body).__name__}, not an object")
        if not body.get("ok"):
            # The description names the cause; the token must never be echoed.
            raise BotError(f"{method}: {body.get('description', 'unknown error')}")
        return body.get("result", {})

    def get_me(self) -> dict[str, Any]:
        return self._call("getMe")

    def get_webhook_info(self) -> dict[str, Any]:
        result = self._call("getWebhookInfo")
        return result if isinstance(result, dict) else {}

    def delete_webhook(self) -> None:
        """Telegram refuses getUpdates while a webhook is registered."""
        self._call("deleteWebhook")

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": POLL_TIMEOUT}
        if offset is not None:
            payload["offset"] = offset
        # Wait longer than the long poll itself, or every poll would time out.
        result = self._call(
            "getUpdates", http_timeout=POLL_TIMEOUT + 15, **payload
        )
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str) -> None:
        self._call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")

    def send_video(self, chat_id: int, path: Path, caption: str = "") -> None:
        """Upload a rendered video, or say why it could not be uploaded."""
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            self.send_message(
                chat_id,
                f"📦 {html.escape(path.name)} tem {size / 1024 / 1024:.0f} MB e o "
                f"telegram só deixa um bot enviar {MAX_UPLOAD_BYTES // 1024 // 1024} MB.\n"
                f"Ele está no servidor em <code>{html.escape(str(path))}</code>",
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
        allowed_users: set[int],
        runner: Callable[..., dict[str, Any]] | None = None,
    ):
        self.client = client
        self.allowed_users = allowed_users
        # Injected so the render can be exercised without spending twenty
        # minutes and an api quota.
        self._runner = runner or self._render
        self._job: RenderJob | None = None
        self._lock = threading.Lock()

    # -- rendering ---------------------------------------------------------

    def _render(
        self, niche_id: str, count: int, on_line: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        # Planning runs in this process, unlike the render, which is a fresh
        # subprocess. Without this the bot would keep calling the provider
        # configured when it started, however many times the file changed.
        plan = create_plan(niche_id, count=count, app_config=load_app_config())
        return produce(
            Path(plan["plan_file"]).parent, quiet=True, on_line=on_line
        )

    def _progress_report(self, job: RenderJob) -> None:
        """Tell the chat where the render is, and reset the heartbeat clock.

        The clock is reset only on a message that actually went out. Resetting
        on the attempt would let one failed send buy another five minutes of
        silence, which is the thing this is here to prevent.
        """
        progress = job.tracker.snapshot()
        sent = self._say(
            job.chat_id,
            f"⏳ {progress.describe()}\n🕐 já faz {job.elapsed_minutes:.0f} min",
        )
        if sent:
            job.last_report = time.monotonic()

    def _heartbeat(self, job: RenderJob, done: threading.Event) -> None:
        """Do all the talking for a running render, on a thread of its own.

        Two reasons it, and not the log watcher, sends every update. A stage
        can go twelve minutes without printing a line, and only a clock
        notices that. And the watcher runs on the thread draining the engine's
        stdout: a slow Telegram there stops that pipe being read, which stalls
        the engine and eats into its own timeout.
        """
        while not done.wait(HEARTBEAT_TICK):
            try:
                due = time.monotonic() - job.last_report >= HEARTBEAT_SECONDS
                if job.report_pending or due:
                    job.report_pending = False
                    self._progress_report(job)
            except Exception:  # this thread dying is the silence it prevents
                logger.exception("progress heartbeat failed")

    def _run_job(self, job: RenderJob) -> None:
        """Render, then report. Runs on its own thread, so nothing here may
        raise: an escaping exception would leave the chat waiting forever for
        a message that never comes."""
        def watch(line: str) -> None:
            """Note what the log said. Never sends: see _heartbeat."""
            if job.tracker.feed(line):
                if job.tracker.snapshot().phase in NOTABLE_PHASES:
                    job.report_pending = True
                return
            # Not a stage change, so the only news is another few scenes.
            drawn = job.tracker.snapshot().scenes_done
            if drawn >= job.scenes_reported + SCENES_PER_REPORT:
                job.scenes_reported = drawn
                job.report_pending = True

        done = threading.Event()
        try:
            # Started inside the guard: if the machine is out of threads, the
            # finally below still frees the queue. Leaving it outside would
            # wedge the bot on a job that is not running.
            threading.Thread(
                target=self._heartbeat, args=(job, done), daemon=True
            ).start()
            result = self._runner(job.niche_id, job.count, on_line=watch)
            done.set()
            job.delivering = True
            self._report(job, result)
        except (PlanError, ProduceError, NicheError) as exc:
            self._say(
                job.chat_id,
                f"❌ O render falhou 😕\n<code>{html.escape(str(exc))}</code>",
            )
        except Exception as exc:  # a crash must not take the bot down with it
            logger.exception("render job failed")
            self._say(
                job.chat_id,
                f"💥 Algo inesperado quebrou:\n<code>{html.escape(str(exc))}</code>",
            )
        finally:
            done.set()
            with self._lock:
                self._job = None

    def _report(self, job: RenderJob, result: Any) -> None:
        if not isinstance(result, dict):
            raise BotError(f"the renderer returned {type(result).__name__}, not a result")
        succeeded = result.get("succeeded", 0)
        total = result.get("total", 0)
        if succeeded:
            self._say(
                job.chat_id,
                f"✅ <b>Prontinho!</b> {succeeded} de {total} vídeo(s) em "
                f"{job.elapsed_minutes:.0f} min.\n📤 Mandando agora…",
            )
        else:
            self._say(
                job.chat_id,
                f"😕 Terminei em {job.elapsed_minutes:.0f} min e nenhum dos "
                f"{total} vídeo(s) saiu.\n\n" + self._why_it_failed(result),
            )
        for record in result.get("records", []):
            if record.get("status") != "succeeded":
                continue
            for file_path in record.get("files", []):
                self._send_video(job.chat_id, Path(file_path), record.get("caption", ""))

    def _missing_config(self, niche: Any) -> str:
        """Why this pack cannot render yet, or an empty string if it can.

        Local only, no network: this blocks the reply to /run, and the point
        is to answer in a second rather than promise twenty minutes and fail
        in five. The engine's own preflight is reused rather than restated,
        so the two cannot drift apart.
        """
        if niche.video.video_source != "openai_image":
            return ""
        from app.services.material import is_openai_image_enabled

        # From disk, not from the process: app/config reads config.toml once at
        # import, so a bot running since before the fix would refuse forever
        # and the only escape would be a restart.
        if is_openai_image_enabled(load_app_config()):
            return ""
        pack = html.escape(niche.id)
        return (
            f"⚙️ O pack <code>{pack}</code> desenha as próprias cenas, mas o "
            "endereço e o modelo de imagem ainda não estão no config.\n\n"
            "Rode uma vez no servidor:\n"
            f"<code>python -m growth config --niche {pack}</code>\n\n"
            "🔑 Se o provedor de texto também não estiver configurado, resolve "
            "os dois de uma vez:\n"
            f"<code>python -m growth config --llm pollinations --llm-key "
            f"&lt;sua-chave&gt; --niche {pack}</code>\n\n"
            "Depois manda <code>/doctor</code> aqui mesmo pra conferir.\n"
            "✅ Não precisa reiniciar o bot."
        )

    def _why_it_failed(self, result: dict[str, Any]) -> str:
        """Report the stage and reason the engine recorded, not a shrug.

        Both already travel in every record. Telling the operator to read a
        log was worse than useless: the only loguru sink is the terminal, so
        under the bot the engine's own output goes nowhere.
        """
        failed = [
            record
            for record in result.get("records", [])
            if record.get("status") != "succeeded"
        ]
        if not failed:
            return "Não consegui recuperar o motivo. Rode <code>python -m growth doctor</code>."

        lines: list[str] = []
        for record in failed[:MAX_FAILURES_SHOWN]:
            stage = str(record.get("failed_stage") or "unknown")
            reason = str(record.get("error") or "").strip() or "sem detalhe"
            if len(reason) > MAX_ERROR_CHARS:
                reason = reason[:MAX_ERROR_CHARS] + "…"
            lines.append(
                f"🧩 Parou em: <b>{html.escape(STAGE_NAMES.get(stage, stage))}</b>\n"
                f"<code>{html.escape(reason)}</code>"
            )
        if len(failed) > MAX_FAILURES_SHOWN:
            lines.append(f"…e mais {len(failed) - MAX_FAILURES_SHOWN} com falha.")
        lines.append("Depois de ajustar, <code>/doctor</code> confere sem gastar render.")
        return "\n\n".join(lines)

    def _send_video(self, chat_id: int, path: Path, caption: str) -> None:
        if not path.is_file():
            return
        try:
            self.client.send_video(chat_id, path, caption)
        except (BotError, requests.RequestException) as exc:
            self._say(
                chat_id,
                f"⚠️ Não consegui enviar {html.escape(path.name)}: {html.escape(str(exc))}",
            )

    def _say(self, chat_id: int, text: str) -> bool:
        """Send, and say whether it arrived. Callers decide what a loss costs."""
        try:
            self.client.send_message(chat_id, text)
            return True
        except (BotError, requests.RequestException) as exc:
            logger.warning(f"could not reply to {chat_id}: {exc}")
            return False

    # -- commands ----------------------------------------------------------

    def _cmd_start(self, chat_id: int, _: list[str]) -> str:
        return (
            "👋 <b>Oi! Eu faço os vídeos pra você.</b>\n\n"
            "🎬 /run &lt;nicho&gt; [quantos] — começar um lote\n"
            "📊 /status — o que estou fazendo agora\n"
            "🩺 /doctor [nicho] — conferir se está tudo configurado\n"
            "🗂 /niches — os packs e quanto rendem\n"
            "🔍 /review — conferir os vídeos antes de postar\n"
            "📤 /last [n] — reenviar os vídeos mais recentes\n\n"
            "Comece com <code>/run ufo-sightings</code> 🛸\n"
            "Vou avisando o progresso por aqui e mando o vídeo quando ficar pronto 😉"
        )

    def _cmd_niches(self, _: int, __: list[str]) -> str:
        packs = load_all_niches()
        if not packs:
            return "🤔 Não achei nenhum pack de nicho."
        lines = ["🗂 <b>Packs disponíveis</b>, maior retorno estimado primeiro:\n"]
        for pack in packs:
            low, high = pack.economics.rpm_range
            lines.append(
                f"• <code>{html.escape(pack.id)}</code>\n"
                f"   💰 RPM ${low:.2f}-${high:.2f} · 🥊 concorrência "
                f"{html.escape(pack.economics.competition)}"
            )
        lines.append("\nPara usar: <code>/run &lt;nicho&gt;</code>")
        return "\n".join(lines)

    def _cmd_run(self, chat_id: int, args: list[str]) -> str:
        if not args:
            return "🤔 Me diz qual nicho: <code>/run &lt;nicho&gt; [quantos]</code>\nVeja a lista em /niches"
        niche_id = args[0]
        try:
            niche = load_niche(niche_id)
        except NicheError as exc:
            return f"❌ {html.escape(str(exc))}\n\nVeja os nomes válidos em /niches"

        count = 1
        if len(args) > 1:
            try:
                count = max(1, min(int(args[1]), 5))
            except ValueError:
                return "🔢 A quantidade precisa ser um número, tipo <code>/run ufo-sightings 2</code>"

        # Refuse before promising twenty minutes. The engine checks the same
        # thing, but only after the batch has started and the chat has been
        # told to wait.
        blocked = self._missing_config(niche)
        if blocked:
            return blocked

        with self._lock:
            if self._job is not None:
                job = self._job
                return (
                    f"⏳ Calma aí! Já estou fazendo <code>{html.escape(job.niche_id)}</code> "
                    f"há {job.elapsed_minutes:.0f} min.\n"
                    f"{job.tracker.snapshot().describe()}\n\n"
                    "Faço um de cada vez pra não travar a máquina 🙂"
                )
            # One image per scene, and plan decides how many. Asking it keeps
            # the chat's denominator equal to the number really drawn.
            job = RenderJob(
                niche_id=niche.id,
                count=count,
                chat_id=chat_id,
                tracker=ProgressTracker(scenes_total=scene_count(niche)),
            )
            self._job = job

        # Acknowledged before the thread starts: begun first, the render can
        # report a stage above the message that says it started.
        self._say(
            chat_id,
            f"▶️ Beleza! Fazendo {count} vídeo(s) de <code>{html.escape(niche.id)}</code> 🎬\n"
            f"⏱ Leva uns {20 * count} min.\n"
            "Vou te avisando aqui a cada etapa — ou pergunte /status quando quiser 😉",
        )
        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
        return ""

    def _cmd_status(self, _: int, __: list[str]) -> str:
        with self._lock:
            job = self._job
        if job is None:
            return (
                "😴 Tudo parado por aqui, nada rodando.\n"
                "Manda um <code>/run &lt;nicho&gt;</code> que eu começo 🎬"
            )
        if job.delivering:
            return (
                f"📤 <b>{html.escape(job.niche_id)}</b> — render pronto, "
                f"enviando os vídeos agora.\n"
                f"🕐 rodando há {job.elapsed_minutes:.0f} min"
            )
        return (
            f"🎬 <b>{html.escape(job.niche_id)}</b> — {job.count} vídeo(s)\n"
            f"{job.tracker.snapshot().describe()}\n"
            f"🕐 rodando há {job.elapsed_minutes:.0f} min"
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
            return "📭 Ainda não produzi nada. Manda um <code>/run</code> 🎬"
        lines = ["🔍 <b>Últimos vídeos</b>:\n"]
        for item in reviews:
            mark = "❌" if item.status == FAIL else "✅"
            lines.append(
                f"{mark} {html.escape(item.subject[:48])} — "
                f"{item.duration:.0f}s, {item.words} palavras"
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
            return "📭 Ainda não produzi nada. Manda um <code>/run</code> 🎬"
        self._say(chat_id, f"📤 Enviando {min(count, len(reviews))} vídeo(s)…")
        for item in reviews[-count:]:
            self._send_video(chat_id, item.path, item.subject)
        return ""

    def _cmd_doctor(self, chat_id: int, args: list[str]) -> str:
        """Run the preflight checks from the chat.

        Every failure the operator hit today was one of these, and each cost a
        trip to a terminal to find. The network checks take a few seconds, so
        this answers first and reports when it is done.
        """
        niche_id = args[0] if args else None
        if niche_id:
            try:
                load_niche(niche_id)
            except NicheError as exc:
                return f"❌ {html.escape(str(exc))}"

        self._say(chat_id, "🩺 Conferindo tudo, uns 10 segundos…")
        # On its own thread: the checks call out to the network, and blocking
        # the poll loop would stop the bot answering anything meanwhile.
        threading.Thread(
            target=self._run_doctor, args=(chat_id, niche_id), daemon=True
        ).start()
        return ""

    def _run_doctor(self, chat_id: int, niche_id: str | None) -> None:
        """Run the checks in a fresh process, and report what it found.

        A subprocess rather than a direct call, because the checks reach the
        engine's config, which app/config reads from disk once at import. In
        this long-lived process those values are whatever config.toml said at
        startup, so an in-process check would call a config the operator has
        already fixed broken. It also makes the answer identical to running
        the command by hand.
        """
        command = [sys.executable, "-m", "growth", "doctor", "--json"]
        if niche_id:
            command += ["--niche", niche_id]
        try:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=DOCTOR_TIMEOUT,
            )
            checks = [
                _Check(**row) for row in json.loads(completed.stdout.strip().splitlines()[-1])
            ]
        except subprocess.TimeoutExpired:
            self._say(chat_id, "⏰ Os checks demoraram demais. A rede está fora?")
            return
        except Exception as exc:  # never leave the chat waiting on a thread
            logger.exception("doctor failed")
            self._say(chat_id, f"💥 Não consegui conferir:\n<code>{html.escape(str(exc))}</code>")
            return

        marks = {CHECK_OK: "✅", CHECK_FAIL: "❌"}
        lines = []
        for check in checks:
            lines.append(
                f"{marks.get(check.status, '⚠️')} <b>{html.escape(check.name)}</b> — "
                f"{html.escape(check.detail[:120])}"
            )
            if check.status != CHECK_OK and check.fix:
                lines.append(f"   ↳ {html.escape(check.fix)}")
        broken = [c for c in checks if c.status == CHECK_FAIL]
        lines.append(
            "🎬 Tudo pronto, pode mandar /run"
            if not broken
            else f"\n⚠️ {len(broken)} check(s) com falha — corrija antes de gastar um render."
        )
        self._say(chat_id, "\n".join(lines))

    COMMANDS: dict[str, str] = {
        "/start": "_cmd_start",
        "/help": "_cmd_start",
        "/niches": "_cmd_niches",
        "/run": "_cmd_run",
        "/status": "_cmd_status",
        "/doctor": "_cmd_doctor",
        "/review": "_cmd_review",
        "/last": "_cmd_last",
    }

    def handle(self, message: dict[str, Any]) -> None:
        """Dispatch one incoming message."""
        chat_id = int(message.get("chat", {}).get("id", 0))
        sender_id = int((message.get("from") or {}).get("id", 0))
        text = str(message.get("text", "")).strip()
        if not chat_id or not text:
            return

        # Authorise the sender, not the conversation. In a private chat the two
        # ids are the same number, but a group's chat id belongs to the group:
        # allowing that would let every member of it start renders. A message
        # with no sender (a channel post) has nobody to authorise.
        if not sender_id or sender_id not in self.allowed_users:
            logger.warning(
                f"ignoring message from unlisted user {sender_id} in chat {chat_id}"
            )
            self._say(
                chat_id,
                "🔒 Este bot só responde ao dono.\n"
                f"Seu id de usuário no telegram é <code>{sender_id or 'desconhecido'}</code>.",
            )
            return

        parts = text.split()
        # Telegram appends @botname to commands in groups.
        command = parts[0].split("@", 1)[0].lower()
        handler_name = self.COMMANDS.get(command)
        if handler_name is None:
            self._say(chat_id, "🤨 Não conheço esse comando. Tenta /help 🙂")
            return
        reply = getattr(self, handler_name)(chat_id, parts[1:])
        if reply:
            self._say(chat_id, reply)


def poll_forever(bot: GrowthBot, client: TelegramClient, stop: threading.Event | None = None) -> None:
    """Read updates until stopped, surviving transient network failures."""
    stop = stop or threading.Event()
    offset: int | None = None
    rejections = 0
    while not stop.is_set():
        try:
            updates = client.get_updates(offset)
        except requests.RequestException as exc:
            logger.warning(f"telegram unreachable: {exc}")
            stop.wait(RETRY_DELAY)
            continue
        except BotError as exc:
            # A rejection is a configuration problem, not a hiccup: a webhook
            # still registered, or a second poller on the same token. Retrying
            # cannot fix either, so say what to do and stop rather than log the
            # same line every few seconds forever.
            rejections += 1
            logger.error(f"telegram rejected the poll: {exc}")
            if rejections >= MAX_REJECTIONS:
                if "webhook" in str(exc).lower():
                    logger.error(
                        "a webhook is registered for this token. Restart the bot: "
                        "it removes one at startup."
                    )
                else:
                    logger.error(
                        "another process is polling the same token; stop it first. "
                        "Telegram allows only one."
                    )
                return
            stop.wait(RETRY_DELAY)
            continue

        rejections = 0
        for update in updates:
            offset = int(update.get("update_id", 0)) + 1
            message = update.get("message") or update.get("edited_message")
            if message:
                try:
                    bot.handle(message)
                except Exception:  # one bad message must not end the loop
                    logger.exception("failed to handle an update")


def load_settings(app_config: dict[str, Any] | None = None) -> tuple[str, set[int]]:
    """Read the token and the telegram users allowed to drive it."""
    if app_config is None:
        from app.config import config

        app_config = config.app

    token = str(app_config.get("telegram_bot_token", "") or "").strip()
    if not token:
        raise BotError(
            "telegram_bot_token is not set; get one from @BotFather then run "
            "python -m growth config --telegram-token <token>"
        )

    # telegram_allowed_chats is the name this shipped with; in a private chat
    # it holds the same number, so it is still honoured.
    raw = (
        app_config.get("telegram_allowed_users")
        or app_config.get("telegram_allowed_chats")
        or []
    )
    if isinstance(raw, (str, int)):
        raw = [raw]
    users: set[int] = set()
    for value in raw:
        try:
            users.add(int(str(value).strip()))
        except (TypeError, ValueError):
            continue
    return token, users


def run(app_config: dict[str, Any] | None = None) -> int:
    """Start the bot and poll until interrupted."""
    token, allowed = load_settings(app_config)
    client = TelegramClient(token)
    identity = client.get_me()
    name = identity.get("username", "unknown")

    # Telegram refuses getUpdates while a webhook is registered, and a token
    # that was ever pointed at one keeps it until it is deleted. Report the url
    # before removing it, so nothing disappears silently.
    webhook = client.get_webhook_info().get("url", "")
    if webhook:
        logger.warning(f"removing the webhook registered at {webhook}; polling needs it gone")
        client.delete_webhook()

    if not allowed:
        logger.warning(
            "telegram_allowed_users is empty: the bot will answer no one. "
            "Message the bot, then add the user id it replies with."
        )
    logger.info(f"telegram bot @{name} polling; allowed users: {sorted(allowed) or 'none'}")

    bot = GrowthBot(client, allowed)
    try:
        poll_forever(bot, client)
    except KeyboardInterrupt:
        logger.info("stopped")
    return 0
