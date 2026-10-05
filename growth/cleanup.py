"""Reclaim the disk a finished video leaves behind.

A render costs far more disk than the video it delivers. The engine keeps a
working directory per task under `storage/tasks/<task_id>` holding
`combined-1.mp4` *and* `final-1.mp4`, plus audio.mp3, subtitle.srt, script.json
and every generated scene image; `growth.produce.collect` then copies the
finished file into `storage/growth/out`. Measured on this checkout, that is
76 MB of working files against 27 MB of delivered video, so deleting only the
delivered copy would leave three quarters of the waste in place.

On a free-tier VPS that gap is the whole budget, so this module removes both:
the working directory of a render, and what a delivered ledger row left behind.

This is the one module in the repo that destroys data, so every path is proven
to sit inside `REMOVABLE_ROOTS` before anything is removed, using the repo's
own containment check (`app.utils.file_security.resolve_path_within_directory`,
which realpaths both sides and compares with commonpath). Anything else -- an
absolute path elsewhere, a `..` escape, a symlink whose target leaves the tree,
a task id that is not a bare UUID -- is refused and logged, never deleted.
A path that is already gone is not an error: deletion is idempotent so that a
caller can retry after a partial failure.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from stat import S_ISDIR
from typing import Any

from loguru import logger

from app.utils import file_security, utils
from growth.produce import OUT_DIR

# The base `utils.task_dir()` joins a task id onto, resolved once so that the
# guards below compare real paths. The base is taken from `utils.storage_dir()`
# rather than by calling `utils.task_dir()` because that helper creates the
# directory as a side effect, which an import of a cleanup module should not do.
TASKS_DIR = (Path(utils.storage_dir()) / "tasks").resolve()

# Where `growth.produce.collect` files the finished videos it hands to the phone.
DELIVERED_DIR = OUT_DIR.resolve()

# The only directories anything may be deleted from. Nothing else: not the
# ledger, not the plans, not the niche packs, not the repo.
REMOVABLE_ROOTS: tuple[Path, ...] = (TASKS_DIR, DELIVERED_DIR)

# A task id becomes a path segment, so it is accepted only in the exact shape
# the engine generates: `utils.get_uuid()` returns `str(uuid4())`, cli.py's
# own `--task-id` validator refuses anything that is not a UUID for precisely
# this reason, and the WebUI normalises through `UUID(...)` too. The literal
# string is matched rather than parsed with `UUID()`, because that constructor
# also accepts `{...}`, `urn:uuid:...` and unhyphenated forms whose canonical
# value no longer names the directory the engine created on disk.
_TASK_ID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _tree_bytes(path: Path) -> int:
    """Apparent bytes held by a file or a directory tree, as `du -sb` counts them.

    Content only: directory inodes hold none and `du -sb` does not count them
    either. Symlinks count as the link itself and are never followed, so a link
    into /etc never inflates the figure (nor, below, gets followed when
    deleting). A missing path holds nothing.
    """
    try:
        info = path.lstat()
    except OSError:
        return 0
    if path.is_symlink() or not path.is_dir():
        return info.st_size

    total = 0
    for parent, directories, files in os.walk(path, followlinks=False):
        # A symlink to a directory is listed among `directories`; counting it
        # here and never walking into it is what keeps the figure honest.
        for name in (*directories, *files):
            try:
                info = os.lstat(os.path.join(parent, name))
            except OSError:
                # Vanished mid-walk, or unreadable: it is not ours to count.
                continue
            if not S_ISDIR(info.st_mode):
                total += info.st_size
    return total


def _resolve_removable(candidate: str | Path, what: str) -> Path | None:
    """Resolve a path and prove it sits strictly inside one of REMOVABLE_ROOTS.

    Returns the resolved real path, or None when it is empty, relative, a
    removable root itself, or outside the tree. Every refusal is logged; the
    caller removes nothing in that case.
    """
    raw = str(candidate).strip()
    if not raw:
        logger.warning(f"cleanup refused an empty {what}")
        return None
    # A relative path means something different from every working directory,
    # which is not a question to guess at right before deleting something.
    if not os.path.isabs(raw):
        logger.warning(f"cleanup refused a relative {what}: {raw!r}")
        return None

    for root in REMOVABLE_ROOTS:
        try:
            # The repo's containment check: it realpaths both sides, so "..",
            # duplicate separators and symlinks are collapsed before the
            # comparison, and a symlink whose target escapes the root fails
            # here. require_file=False lets an already-deleted path resolve,
            # which is what keeps deletion idempotent.
            resolved = Path(
                file_security.resolve_path_within_directory(
                    str(root), raw, require_file=False
                )
            )
        except ValueError:
            continue
        if resolved == root:
            # Containment accepts the root itself; wiping a whole root is never
            # what a per-task or per-row purge meant to do.
            logger.warning(f"cleanup refused to remove the root itself: {resolved}")
            return None
        return resolved

    roots = ", ".join(str(root) for root in REMOVABLE_ROOTS)
    logger.warning(f"cleanup refused a {what} outside [{roots}]: {raw}")
    return None


def _remove(path: Path) -> int:
    """Delete one already-validated path and return the bytes it freed.

    A path that is already gone frees nothing and raises nothing, so a caller
    retrying after a partial failure sees the same result as a clean run.
    """
    freed = _tree_bytes(path)
    try:
        if path.is_symlink():
            # `path` is a realpath, so this only catches a link that appeared
            # after the check. Drop the link, never what it points at.
            path.unlink()
        elif path.is_dir():
            # rmtree unlinks symlinks inside the tree instead of descending
            # into them, so nothing outside the root is reachable from here.
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            return 0
    except OSError as exc:
        # Report and carry on: one unreadable file must not abort the purge of
        # everything else, and the caller can retry what is still there.
        logger.error(f"cleanup could not remove {path}: {exc}")
        return 0

    # A silent delete is indistinguishable from a bug when someone later asks
    # where a file went, so say what went and how much it was worth.
    logger.info(f"cleanup freed {freed} bytes: {path}")
    return freed


def purge_task(task_id: str) -> int:
    """Remove one render task's working directory. Returns bytes freed.

    This is the big win: the per-task directory holds both the intermediate
    `combined-*.mp4` and the final render, plus the audio, subtitles, script
    and scene images. Returns 0 -- without raising -- when the id is refused or
    the directory is already gone.
    """
    task_id = (task_id or "").strip()
    if not _TASK_ID_PATTERN.fullmatch(task_id):
        # Covers an empty or missing id, "../..", and anything else that would
        # mean something other than a directory name under storage/tasks.
        logger.warning(f"cleanup refused a task id that is not a UUID: {task_id!r}")
        return 0

    # The id is proven to be a bare UUID and cannot escape, but the assembled
    # path still goes through containment: storage/tasks may itself be a link,
    # and the entry under it may be one too.
    target = _resolve_removable(TASKS_DIR / task_id, "task directory")
    if target is None:
        return 0
    return _remove(target)


def purge_delivered(record: dict[str, Any]) -> int:
    """Remove what a delivered ledger row left behind. Returns bytes freed.

    A row names the collected copies under storage/growth/out in `files` and
    the render that produced them in `task_id`; both go. The row's
    `subtitle_path` lives inside that task directory and goes with it.

    Whether the video actually reached the phone is the caller's judgement --
    this only removes what the row names, and only from the removable roots.
    """
    freed = 0
    for entry in record.get("files") or []:
        delivered = _resolve_removable(entry, "delivered file")
        if delivered is not None:
            freed += _remove(delivered)

    freed += purge_task(str(record.get("task_id") or ""))
    return freed


def disk_report() -> dict[str, int]:
    """Bytes currently held under each removable root, keyed by root path.

    Deletes nothing, so a caller can report what is being used -- and what a
    purge would be worth -- before touching anything.
    """
    return {str(root): _tree_bytes(root) for root in REMOVABLE_ROOTS}
