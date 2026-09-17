"""Run a plan through the render engine and record what came out.

`cli.py --batch-file` already renders a manifest and prints a JSON summary.
This module drives it, joins the summary back to the briefs that produced it,
and leaves behind two things the engine does not: finished files collected in
one predictable place, and a ledger row per video with the caption, hashtags
and call to action needed to actually post it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from growth.niche import load_niche

REPO_ROOT = Path(__file__).resolve().parent.parent
STORAGE_DIR = REPO_ROOT / "storage" / "growth"
OUT_DIR = STORAGE_DIR / "out"
LEDGER_PATH = STORAGE_DIR / "ledger.jsonl"

# A batch of long videos on a small VPS can run for hours; the engine still
# renders one task at a time, so the ceiling is generous rather than tight.
DEFAULT_TIMEOUT_SECONDS = 6 * 60 * 60


class ProduceError(RuntimeError):
    """Raised when the batch cannot be run or its output cannot be read."""


def _diagnostics(*streams: str | None, lines: int = 12) -> str:
    """Surface the reason a batch was rejected.

    The engine logs to both streams and colourises them, so the useful line is
    easily buried. Keep error lines first, then the tail of whatever is left.
    """
    collected: list[str] = []
    for stream in streams:
        for line in (stream or "").splitlines():
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if clean:
                collected.append(clean)
    errors = [line for line in collected if "ERROR" in line or "error:" in line]
    chosen = errors[-lines:] if errors else collected[-lines:]
    return "\n".join(chosen) or "(no output)"


def _caption(brief: dict[str, Any], niche_hashtags: list[str]) -> str:
    """Platform caption: the hook sells the video, tags do the routing."""
    hook = str(brief.get("hook", "")).strip()
    cta = str(brief.get("call_to_action", "")).strip()
    tags = " ".join(niche_hashtags)
    body = " ".join(part for part in (hook, cta) if part)
    return f"{body}\n\n{tags}".strip()


def _tee_stdout(process: subprocess.Popen, timeout: int) -> tuple[str, str | None]:
    """Echo the engine's output as it arrives and keep it for the summary.

    Reading line by line rather than with communicate() is what makes a render
    visible while it runs; the deadline is enforced per line, so a stalled
    engine is still killed rather than waited on forever.
    """
    deadline = time.monotonic() + timeout
    collected: list[str] = []
    assert process.stdout is not None
    try:
        for line in process.stdout:
            collected.append(line.rstrip("\n"))
            print(line, end="", file=sys.stderr, flush=True)
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(cmd="cli.py", timeout=timeout)
        remaining = max(1, int(deadline - time.monotonic()))
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise ProduceError(f"batch timed out after {timeout}s") from exc
    return "\n".join(collected), None


def run_batch(
    manifest: Path,
    stop_at: str = "video",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    quiet: bool = False,
) -> dict[str, Any]:
    """Invoke the render CLI on a manifest and return its JSON summary.

    quiet captures the engine's log instead of letting it reach the terminal.
    """
    if not manifest.is_file():
        raise ProduceError(f"manifest not found: {manifest}")
    command = [
        sys.executable,
        str(REPO_ROOT / "cli.py"),
        "--batch-file",
        str(manifest),
        "--stop-at",
        stop_at,
    ]
    # The engine installs its own log sink on stdout and prints its JSON
    # summary there too, so stdout has to be captured to find the summary and
    # echoed to keep the render visible. Letting stderr through alone leaves
    # the terminal silent for the length of a render and throws away the log
    # that explains any failure.
    stderr_target = subprocess.PIPE if quiet else None
    try:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=stderr_target,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise ProduceError(f"could not start the render CLI: {exc}") from exc

    if quiet:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise ProduceError(f"batch timed out after {timeout}s") from exc
    else:
        stdout, stderr = _tee_stdout(process, timeout)

    # Exit 1 means some tasks failed but a summary was still printed; exit 2
    # means the manifest was rejected before anything ran and there is none.
    if process.returncode == 2 or not (stdout or "").strip():
        detail = _diagnostics(stderr, stdout)
        if not quiet:
            # Its log went straight to the terminal, so point there instead of
            # claiming there was no output.
            detail = detail if detail != "(no output)" else "see the log above"
        raise ProduceError(
            f"batch did not produce a summary (exit {process.returncode}):\n{detail}"
        )
    try:
        return json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ProduceError(f"could not parse batch summary: {exc}") from exc


def collect(plan_file: Path, summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Join the render summary back to its briefs and file the outputs."""
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    briefs: list[dict[str, Any]] = plan.get("briefs", [])
    niche = load_niche(plan["niche_id"])
    hashtags = list(plan.get("hashtags", []))
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    destination = OUT_DIR / plan["niche_id"] / day
    destination.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for task in summary.get("tasks", []):
        # cli.py numbers batch tasks from 1; briefs are a 0-based list.
        index = int(task.get("index", 0))
        position = index - 1 if index > 0 else 0
        brief = briefs[position] if 0 <= position < len(briefs) else {}
        result = task.get("result") or {}
        videos = result.get("videos") or []

        filed: list[str] = []
        for number, source in enumerate(videos, start=1):
            source_path = Path(source)
            if not source_path.is_file():
                continue
            # Slug keeps the subject readable in a file listing without
            # trusting model output as a path.
            slug = "".join(
                char if char.isalnum() else "-"
                for char in str(brief.get("subject", f"video-{index}")).lower()
            ).strip("-")[:60] or f"video-{index}"
            target = destination / f"{index:02d}-{slug}-{number}.mp4"
            shutil.copy2(source_path, target)
            filed.append(str(target))

        records.append(
            {
                "niche_id": plan["niche_id"],
                "subject": brief.get("subject", ""),
                "angle": brief.get("angle", ""),
                "hook": brief.get("hook", ""),
                "call_to_action": brief.get("call_to_action", ""),
                "caption": _caption(brief, hashtags),
                "hashtags": hashtags,
                "platforms": list(niche.platforms),
                "voice_name": brief.get("voice_name", ""),
                "status": task.get("status", "unknown"),
                "error": task.get("error"),
                "failed_stage": task.get("failed_stage"),
                "task_id": task.get("task_id", ""),
                "files": filed,
                "subtitle_path": result.get("subtitle_path", ""),
                "produced_at": datetime.now(timezone.utc).isoformat(),
                "published": False,
            }
        )
    return records


def append_ledger(records: list[dict[str, Any]]) -> None:
    """One row per video, so nothing is posted twice or lost."""
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def produce(
    plan_dir: Path,
    stop_at: str = "video",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    quiet: bool = False,
) -> dict[str, Any]:
    """Render a plan directory end to end and record the results."""
    plan_file = plan_dir / "plan.json"
    manifest = plan_dir / "manifest.jsonl"
    if not plan_file.is_file():
        raise ProduceError(f"plan.json not found in {plan_dir}")

    summary = run_batch(manifest, stop_at=stop_at, timeout=timeout, quiet=quiet)
    records = collect(plan_file, summary)
    append_ledger(records)

    succeeded = [r for r in records if r["status"] == "succeeded"]
    # A caption file next to the videos is what makes the batch postable
    # without opening the ledger.
    if succeeded:
        captions = OUT_DIR / json.loads(plan_file.read_text(encoding="utf-8"))["niche_id"]
        captions_file = (
            captions / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "captions.txt"
        )
        captions_file.parent.mkdir(parents=True, exist_ok=True)
        with captions_file.open("a", encoding="utf-8") as handle:
            for record in succeeded:
                files = ", ".join(Path(f).name for f in record["files"]) or "(no file)"
                handle.write(f"=== {files}\n{record['caption']}\n\n")

    return {
        "total": summary.get("total", len(records)),
        "succeeded": len(succeeded),
        "failed": len(records) - len(succeeded),
        "records": records,
    }
