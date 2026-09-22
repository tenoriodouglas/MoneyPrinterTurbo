"""Turn the engine's log into something worth reporting mid-render.

A batch runs for twenty minutes and says nothing until it finishes. Elapsed
time alone does not answer the only question worth asking while waiting, which
is whether it is nearly done or barely started.

The engine already prints which stage it is in. This reads those lines and
models the rest from measured stage *durations* rather than fixed shares of
the whole. The difference matters: two of the stages take twelve minutes
between them and print almost nothing, so a share-based model freezes the
percentage there and makes the time remaining grow instead of shrink. With
durations the estimate keeps moving through a silent stage, and it converges.

Everything reported is marked approximate except the stage name and the scene
count, which are read straight from the log and are exact.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable

# Seconds each stage took in a measured illustrated render, from
# docs/HOSPEDAGEM.md: two minutes of setup, twenty-one seconds per generated
# scene, twelve minutes of assembly and final render.
# Drawing is the one stage whose cost scales with the scene count; the rest
# scale with the length of the video, which the pack fixes. That is why it is
# budgeted per scene and the others are not.
SECONDS_PER_SCENE = 21
# Labels are chat-facing and written in the operator's language, unlike the
# rest of this file.
PHASES: tuple[tuple[str, str, int], ...] = (
    ("script", "✍️ escrevendo o roteiro", 35),
    ("terms", "🎬 escolhendo as cenas", 20),
    ("audio", "🎙️ gravando a narração", 40),
    ("subtitle", "💬 sincronizando as legendas", 25),
    ("materials", "🎨 desenhando as cenas", 0),
    ("combining", "🧩 montando o vídeo", 420),
    ("rendering", "🎞️ render final", 300),
)
_ORDER = [name for name, _, _ in PHASES]
_LABELS = {name: label for name, label, _ in PHASES}
_BUDGET = {name: seconds for name, _, seconds in PHASES}
# Drawing has no fixed budget; without a known scene count, assume the pack's
# usual size rather than budgeting nothing for a stage that takes minutes.
DEFAULT_SCENES = 20

# A slower machine is slow at everything, so one stage running long predicts
# the next will too. The factor is clamped: a single stalled network call
# should not triple every remaining estimate.
MIN_SPEED, MAX_SPEED = 0.5, 4.0

# Markers the engine prints, verified against app/services/task.py and
# app/services/material.py. Order matters: "generating video script" must be
# tested before the render marker.
_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("script", re.compile(r"generating video script")),
    ("terms", re.compile(r"generating video terms")),
    ("audio", re.compile(r"generating audio")),
    ("subtitle", re.compile(r"generating subtitle")),
    # The third alternative covers local and uploaded material, which never
    # logs a download and would otherwise skip this stage entirely.
    (
        "materials",
        re.compile(r"downloading videos (from|with)|preprocess local materials"),
    ),
    ("combining", re.compile(r"combining video:|starting clip merging")),
    ("rendering", re.compile(r"generating video: \d")),
)
# Printed once per image the engine generates, which makes drawing the only
# stage with an exact count. Only the generating sources print it; for stock
# footage the count stays zero and the stage falls back to its time budget.
_SCENE_DONE = re.compile(r"image material rendered:")


@dataclass(frozen=True)
class Progress:
    """Where a render is, as far as its log and the clock have said.

    A snapshot, so a phase change landing mid-message cannot produce a line
    whose label, percentage and scene count disagree with each other.
    """

    phase: str = ""
    scenes_done: int = 0
    scenes_total: int = 0
    # Seconds since this stage started, and since the batch did.
    in_phase: float = 0.0
    elapsed: float = 0.0
    speed: float = 1.0
    # Highest share reported so far. A machine revealing itself to be slow
    # stretches the remaining estimate, which would otherwise pull the
    # percentage down; a bar that goes backwards reads as a bug.
    floor: float = 0.0

    @property
    def label(self) -> str:
        return _LABELS.get(self.phase, "🚀 começando")

    def _budget(self, phase: str) -> float:
        """Seconds this stage is expected to take on this machine."""
        if phase == "materials":
            scenes = self.scenes_total or DEFAULT_SCENES
            return scenes * SECONDS_PER_SCENE * self.speed
        return _BUDGET.get(phase, 0) * self.speed

    @property
    def _done_share(self) -> float:
        """How far into the current stage, from its exact count or its clock."""
        budget = self._budget(self.phase)
        if self.phase == "materials" and self.scenes_total:
            return min(self.scenes_done / self.scenes_total, 1.0)
        if budget <= 0:
            return 0.0
        return min(self.in_phase / budget, 1.0)

    @property
    def remaining_seconds(self) -> float:
        """Seconds left, from the budget of this stage and the ones after it."""
        if not self.phase:
            return sum(self._budget(name) for name in _ORDER)
        index = _ORDER.index(self.phase)
        here = self._budget(self.phase) * (1 - self._done_share)
        later = sum(self._budget(name) for name in _ORDER[index + 1 :])
        return here + later

    @property
    def fraction(self) -> float:
        """Approximate share of the work done, by time rather than by stage."""
        total = self.elapsed + self.remaining_seconds
        share = self.elapsed / total if total > 0 else 0.0
        # Never a flat 100%: the last seconds of muxing print nothing, and a
        # bar that sits at 100% invites a "why is it stuck" message every time.
        return min(max(share, self.floor), 0.99)

    def eta_minutes(self) -> float:
        """Minutes left, never below one.

        "faltam ~0 min" followed by four more minutes of waiting is worse than
        a vaguer number that stays true.
        """
        return max(self.remaining_seconds / 60, 1.0)

    def describe(self, with_eta: bool = True) -> str:
        """One line for a chat message."""
        if not self.phase:
            return "🚀 começando"
        text = f"{self.label} — ~{self.fraction * 100:.0f}%"
        if self.phase == "materials" and self.scenes_total:
            text += f" ({self.scenes_done}/{self.scenes_total} cenas)"
        if with_eta:
            text += f" · faltam ~{self.eta_minutes():.0f} min"
        return text


@dataclass
class ProgressTracker:
    """Feed it log lines; ask it where the render is.

    Only feed() mutates state, and produce calls it from the single thread
    reading the engine's output. snapshot() allocates a new Progress, so any
    other thread reads a consistent picture without a lock.
    """

    scenes_total: int = 0
    now: Callable[[], float] = time.monotonic
    _phase: str = ""
    _scenes_done: int = 0
    _started_at: float = field(default=0.0)
    _phase_started_at: float = field(default=0.0)
    # Budget of the stages already finished, and what they really cost, which
    # together say how much slower than the reference machine this one is.
    _budget_spent: float = 0.0
    _budget_planned: float = 0.0
    _peak: float = 0.0

    def __post_init__(self) -> None:
        self._started_at = self._phase_started_at = self.now()

    @property
    def _speed(self) -> float:
        if self._budget_planned <= 0:
            return 1.0
        return min(max(self._budget_spent / self._budget_planned, MIN_SPEED), MAX_SPEED)

    def snapshot(self) -> Progress:
        moment = self.now()
        progress = Progress(
            phase=self._phase,
            scenes_done=self._scenes_done,
            scenes_total=self.scenes_total,
            in_phase=moment - self._phase_started_at,
            elapsed=moment - self._started_at,
            speed=self._speed,
            floor=self._peak,
        )
        # A float store, so a reader on another thread sees either the old
        # peak or the new one, never a torn value.
        self._peak = progress.fraction
        return progress

    def feed(self, line: str) -> bool:
        """Consume one line. Returns True when the stage changed."""
        if _SCENE_DONE.search(line):
            self._scenes_done += 1
            return False
        for name, pattern in _MARKERS:
            if not pattern.search(line):
                continue
            if name == self._phase:
                return False
            # The engine starts over for each task in a batch; only move
            # forward, so the percentage never falls back and looks like a
            # crash and a restart.
            if self._phase and _ORDER.index(name) < _ORDER.index(self._phase):
                return False
            self._close_phase()
            self._phase = name
            return True
        return False

    def _close_phase(self) -> None:
        """Record what the finished stage cost against what it was budgeted."""
        if not self._phase:
            return
        moment = self.now()
        planned = _BUDGET.get(self._phase, 0)
        if self._phase == "materials":
            planned = (self.scenes_total or DEFAULT_SCENES) * SECONDS_PER_SCENE
        if planned > 0:
            self._budget_spent += moment - self._phase_started_at
            self._budget_planned += planned
        self._phase_started_at = moment

    # Kept so callers can read progress without reaching into the tracker.
    @property
    def progress(self) -> Progress:
        return self.snapshot()
