"""Check produced videos against the rules that decide whether they can earn.

A finished render is not the same thing as a publishable one. TikTok pays only
on videos over a minute, so a 55-second file is work thrown away, and it looks
identical to a good one in a file listing. The packs also forbid phrasings that
mark a script as templated; a model follows that instruction most of the time,
not all of it.

Both are cheap to verify after the fact and expensive to discover later.

The checks read the file itself, which a delivered video no longer is: the box
is small and finished renders are deleted once they reach the phone. So a
verdict measured at render time is kept in the ledger row, and a review falls
back to it when the file is gone.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from growth.niche import Niche, NicheError, load_niche
from growth.produce import LEDGER_PATH

# TikTok's Creator Rewards programme only pays on videos longer than a minute.
TIKTOK_MIN_SECONDS = 60.0
# Speech below this rate usually means the script ran short and the render was
# padded out, which reads as slow rather than as substantial.
MIN_WORDS_PER_MINUTE = 100
# Mean luma below this is a frame with no picture in it. Limited-range video
# bottoms out at 16, and the darkest night scene measured in an illustrated
# pack sits at 53, so the gap is wide.
BLACK_LEVEL = 24.0
# One frame every this many seconds, sampled in a single decode pass.
SAMPLE_EVERY_SECONDS = 8
# One blank frame could be a transition; a quarter of them is a broken render.
MAX_BLACK_FRACTION = 0.25

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(slots=True)
class VideoReview:
    subject: str
    niche_id: str
    path: Path
    duration: float = 0.0
    width: int = 0
    height: int = 0
    words: int = 0
    black: float = 0.0
    issues: list[tuple[str, str]] = field(default_factory=list)
    # Empty while the numbers above were measured from the file just now; an
    # ISO timestamp once they come from a ledger row, saying when they were
    # true. A viewer needs that to read "60s, no black frames" correctly.
    reviewed_at: str = ""

    @property
    def status(self) -> str:
        if any(level == FAIL for level, _ in self.issues):
            return FAIL
        if any(level == WARN for level, _ in self.issues):
            return WARN
        return OK

    @property
    def words_per_minute(self) -> float:
        if self.duration <= 0:
            return 0.0
        return self.words / (self.duration / 60)

    def as_dict(self) -> dict[str, Any]:
        """Flatten the verdict into JSON a ledger row can carry.

        The dict only exists to outlive the file, so it is stamped with the
        moment of measurement unless it already carries one.
        """
        return {
            "subject": self.subject,
            "niche_id": self.niche_id,
            "path": str(self.path),
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "words": self.words,
            "black": self.black,
            "issues": [[level, message] for level, message in self.issues],
            "reviewed_at": self.reviewed_at or datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VideoReview:
        """Rebuild a verdict stored by as_dict().

        Anything that is not shaped like one raises rather than being read
        past: dropping a damaged issue would turn a failure into a pass.
        """
        issues = data.get("issues") or []
        if not isinstance(issues, (list, tuple)) or any(
            not isinstance(entry, (list, tuple)) or len(entry) != 2 for entry in issues
        ):
            raise ValueError("issues is not a list of (level, message) pairs")
        return cls(
            subject=str(data.get("subject", "")),
            niche_id=str(data.get("niche_id", "")),
            path=Path(str(data.get("path", ""))),
            duration=float(data.get("duration") or 0.0),
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            words=int(data.get("words") or 0),
            black=float(data.get("black") or 0.0),
            issues=[(str(level), str(message)) for level, message in issues],
            reviewed_at=str(data.get("reviewed_at", "")),
        )


def probe(path: Path) -> tuple[float, int, int]:
    """Return duration and frame size, or zeros if the file cannot be read."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-show_entries", "format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(result.stdout)
        stream = (data.get("streams") or [{}])[0]
        duration = float(data.get("format", {}).get("duration", 0) or 0)
        return duration, int(stream.get("width", 0)), int(stream.get("height", 0))
    except (subprocess.SubprocessError, OSError, ValueError, json.JSONDecodeError):
        return 0.0, 0, 0


def black_fraction(path: Path, duration: float) -> float:
    """Share of the running time that carries no picture.

    A generating source stops when its prompts run out rather than when the
    narration is covered, so a video can come out the right length with its
    tail black. Duration, orientation and script all still look correct, which
    is why this has to be measured.

    blackdetect scans the whole file in one decode pass, which is both faster
    and more truthful than sampling frames: sampling reports whatever moments
    it happens to land on, and seeking to a timestamp lands on a keyframe near
    it rather than on it.
    """
    if duration <= 0:
        return 0.0
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(path), "-an",
                "-vf", f"fps=1/{SAMPLE_EVERY_SECONDS},signalstats,"
                       "metadata=print:key=lavfi.signalstats.YAVG",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=300, check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return 0.0
    levels = [float(value) for value in re.findall(r"YAVG=([0-9.]+)", result.stderr)]
    if not levels:
        return 0.0
    return sum(1 for level in levels if level < BLACK_LEVEL) / len(levels)


def read_script(subtitle_path: str | Path) -> str:
    """Recover the spoken words from the subtitle file the render produced."""
    path = Path(subtitle_path)
    if not path.is_file():
        return ""
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        # Skip SRT sequence numbers and timecodes; keep the text.
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return " ".join(lines)


def find_banned(script: str, niche: Niche) -> list[str]:
    return [
        phrase
        for phrase in niche.banned_phrases
        if phrase.strip() and phrase.lower() in script.lower()
    ]


def stored_reviews(record: dict) -> dict[str, VideoReview]:
    """Verdicts a row recorded at render time, keyed by the file each describes.

    A row written before produce started storing them has none, and an entry
    that cannot be read back, or that carries no timestamp to tell it apart
    from a live measurement, is dropped: a deleted file must never read as a
    pass on the strength of a broken record.
    """
    stored: dict[str, VideoReview] = {}
    for entry in record.get("reviews") or []:
        if not isinstance(entry, dict):
            continue
        try:
            review = VideoReview.from_dict(entry)
        except (TypeError, ValueError):
            continue
        if review.reviewed_at:
            stored[str(entry.get("path", ""))] = review
    return stored


def review_record(record: dict) -> list[VideoReview]:
    """Check every file a ledger row produced.

    Files still on disk are measured again. For one that is gone, the verdict
    the row stored under "reviews" is replayed instead, carrying the
    reviewed_at stamp that says it was measured then rather than now; without
    such a verdict the file is reported missing, as before.
    """
    try:
        niche = load_niche(record.get("niche_id", ""))
    except NicheError:
        niche = None

    platforms = record.get("platforms") or (list(niche.platforms) if niche else [])
    script = read_script(record.get("subtitle_path", ""))
    words = len(re.findall(r"\b[\w']+\b", script))
    stored = stored_reviews(record)

    reviews: list[VideoReview] = []
    for file_path in record.get("files", []):
        path = Path(file_path)
        review = VideoReview(
            subject=record.get("subject", ""),
            niche_id=record.get("niche_id", ""),
            path=path,
            words=words,
        )
        if not path.is_file():
            # Deleted after delivery is the normal case on a small box, so
            # answer from what was measured while the file was there.
            kept = stored.get(file_path)
            if kept is not None:
                reviews.append(kept)
                continue
            review.issues.append((FAIL, "the file is missing"))
            reviews.append(review)
            continue

        review.duration, review.width, review.height = probe(path)
        if review.duration <= 0:
            review.issues.append((FAIL, "duration could not be read; the file may be truncated"))
            reviews.append(review)
            continue

        review.black = black_fraction(path, review.duration)
        if review.black > MAX_BLACK_FRACTION:
            review.issues.append((
                FAIL,
                f"{review.black:.0%} of the video has no picture; "
                "the material ran out before the narration did",
            ))

        if "tiktok" in platforms and review.duration < TIKTOK_MIN_SECONDS:
            short_by = TIKTOK_MIN_SECONDS - review.duration
            review.issues.append((
                FAIL,
                f"{review.duration:.0f}s is under the 60s TikTok pays from "
                f"(short by {short_by:.0f}s); raise paragraph_number in the pack",
            ))

        if niche:
            banned = find_banned(script, niche)
            if banned:
                review.issues.append((
                    FAIL,
                    "script uses phrasings the pack forbids: " + ", ".join(banned),
                ))

            expected = niche.video.aspect
            actual = f"{review.width}:{review.height}"
            portrait = review.height > review.width
            if expected == "9:16" and not portrait:
                review.issues.append((FAIL, f"pack expects 9:16 but the file is {actual}"))
            elif expected == "16:9" and portrait:
                review.issues.append((FAIL, f"pack expects 16:9 but the file is {actual}"))

        if not script:
            review.issues.append((WARN, "no subtitle file, so the script could not be checked"))
        elif review.words_per_minute < MIN_WORDS_PER_MINUTE:
            review.issues.append((
                WARN,
                f"{review.words_per_minute:.0f} words per minute is slow; "
                "the video may be padded",
            ))

        reviews.append(review)
    return reviews


def load_ledger(limit: int | None = None, ledger_path: Path | None = None) -> list[dict]:
    path = ledger_path or LEDGER_PATH
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("status") == "succeeded":
            rows.append(record)
    return rows[-limit:] if limit else rows


def review_all(limit: int | None = None, ledger_path: Path | None = None) -> list[VideoReview]:
    reviews: list[VideoReview] = []
    for record in load_ledger(limit, ledger_path):
        reviews.extend(review_record(record))
    return reviews
