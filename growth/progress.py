"""Turn the engine's log into something worth reporting mid-render.

A batch runs for twenty minutes and says nothing until it finishes. Elapsed
time alone does not answer the only question worth asking while waiting, which
is whether it is nearly done or barely started.

The engine already prints where it is. This reads those lines and keeps a
phase, a count where one is countable, and a rough share of the work done. The
share is an estimate built from measured phase durations, not a real
measurement of remaining work, and it is reported as approximate for that
reason. The phase and the scene count are exact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Weights are the share of wall clock each phase took in a measured 91-second
# illustrated render: roughly two minutes of setup, three of scene drawing and
# twelve of assembly and final render. They decide only how the percentage
# advances, never what is reported as fact.
# Labels are chat-facing and written in the operator's language, unlike the
# rest of this file.
PHASES: tuple[tuple[str, str, float], ...] = (
    ("script", "✍️ escrevendo o roteiro", 0.05),
    ("terms", "🎬 escolhendo as cenas", 0.03),
    ("audio", "🎙️ gravando a narração", 0.06),
    ("subtitle", "💬 sincronizando as legendas", 0.04),
    ("materials", "🎨 desenhando as cenas", 0.22),
    ("combining", "🧩 montando o vídeo", 0.35),
    ("rendering", "🎞️ render final", 0.25),
)
_ORDER = [name for name, _, _ in PHASES]
_LABELS = {name: label for name, label, _ in PHASES}
_WEIGHTS = {name: weight for name, _, weight in PHASES}

# Below this share, extrapolating elapsed time gives a number so wide it is
# worse than saying nothing: at 3% done, a few seconds of jitter moves the
# estimate by ten minutes.
MIN_FRACTION_FOR_ETA = 0.08

# Markers the engine prints. Order matters: the first match wins, and
# "generating video script" must be tested before "generating video".
_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("script", re.compile(r"generating video script")),
    ("terms", re.compile(r"generating video terms")),
    ("audio", re.compile(r"generating audio")),
    ("subtitle", re.compile(r"generating subtitle")),
    ("materials", re.compile(r"downloading videos (from|with)")),
    ("combining", re.compile(r"combining video:|starting clip merging")),
    ("rendering", re.compile(r"generating video: \d|generating video: \d+ x \d+")),
)
# One of these is printed per generated scene, which makes that phase the only
# one with an exact count.
_SCENE_DONE = re.compile(r"image material rendered:")


@dataclass
class Progress:
    """Where a render is, as far as its log has said."""

    phase: str = ""
    scenes_done: int = 0
    scenes_total: int = 0

    @property
    def label(self) -> str:
        return _LABELS.get(self.phase, "starting")

    @property
    def fraction(self) -> float:
        """Approximate share of the work done, from measured phase weights."""
        if not self.phase:
            return 0.0
        done = 0.0
        for name in _ORDER:
            if name == self.phase:
                break
            done += _WEIGHTS[name]
        within = 0.0
        if self.phase == "materials" and self.scenes_total:
            within = min(self.scenes_done / self.scenes_total, 1.0)
        return min(done + _WEIGHTS[self.phase] * within, 0.99)

    def eta_minutes(self, elapsed_seconds: float) -> float | None:
        """Minutes left, extrapolated from the share done.

        None while too early to say anything honest, which is better than a
        confident wrong number in a chat someone is watching.
        """
        share = self.fraction
        if elapsed_seconds <= 0 or share < MIN_FRACTION_FOR_ETA:
            return None
        return elapsed_seconds * (1 - share) / share / 60

    def describe(self, elapsed_seconds: float | None = None) -> str:
        """One line for a chat message."""
        if not self.phase:
            return "🚀 começando"
        text = f"{self.label} — ~{self.fraction * 100:.0f}%"
        if self.phase == "materials" and self.scenes_total:
            text += f" ({self.scenes_done}/{self.scenes_total} cenas)"
        if elapsed_seconds is not None:
            eta = self.eta_minutes(elapsed_seconds)
            text += (
                f" · faltam ~{eta:.0f} min" if eta is not None else " · calculando ⏱"
            )
        return text


@dataclass
class ProgressTracker:
    """Feed it log lines; ask it where the render is."""

    scenes_total: int = 0
    progress: Progress = field(default_factory=Progress)

    def __post_init__(self) -> None:
        self.progress.scenes_total = self.scenes_total

    def feed(self, line: str) -> bool:
        """Consume one line. Returns True when the phase changed."""
        if _SCENE_DONE.search(line):
            self.progress.scenes_done += 1
            return False
        for name, pattern in _MARKERS:
            if pattern.search(line):
                if name == self.progress.phase:
                    return False
                # The engine revisits earlier phases for a second video in a
                # batch; only move forward so the share never goes backwards.
                if self.progress.phase and _ORDER.index(name) < _ORDER.index(
                    self.progress.phase
                ):
                    return False
                self.progress.phase = name
                return True
        return False
