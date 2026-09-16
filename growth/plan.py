"""Turn a niche pack into a batch of distinct video briefs.

The engine renders whatever subject it is handed. What decides whether a
channel earns or gets flagged is upstream of rendering: whether each video
makes a different, specific point. This module is that upstream step. It asks
the configured LLM for N briefs, forces a different editorial angle on each,
rotates voices, and refuses topics the niche has already covered.

Output is two files: a plan (rich, for humans and for the ledger) and a
manifest (strict VideoParams, for `cli.py --batch-file`).
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from growth.niche import MAX_SCRIPT_PROMPT, Niche, load_niche

STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage" / "growth"
HISTORY_DIR = STORAGE_DIR / "history"
PLANS_DIR = STORAGE_DIR / "plans"

# How many past subjects to show the model as "already covered".
HISTORY_LOOKBACK = 60
# Enum values from VideoParams.VideoTransitionMode; the manifest is parsed as
# raw VideoParams, so it needs the value, not the CLI's dashed spelling.
_TRANSITIONS = ("Shuffle", "FadeIn", "FadeOut", "SlideIn", "SlideOut")


class PlanError(RuntimeError):
    """Raised when briefs cannot be generated or parsed."""


@dataclass(slots=True)
class Brief:
    """One video, before it is rendered."""

    subject: str
    angle: str
    hook: str
    key_points: list[str]
    search_terms: list[str]
    call_to_action: str
    voice_name: str = ""
    index: int = 0

    def script_prompt(self, niche: Niche) -> str:
        """Pack the brief into VideoParams.video_script_prompt.

        This is what makes two videos in the same niche different from each
        other, so it carries the angle, the hook and the specific points
        rather than just the subject line.
        """
        points = "\n".join(f"- {p}" for p in self.key_points)
        banned = ", ".join(f'"{p}"' for p in niche.banned_phrases[:8])
        parts = [
            f"Editorial angle: {self.angle}",
            f"Open with this idea (rephrase it, do not read it verbatim): {self.hook}",
            f"Cover these specific points:\n{points}",
            f"End with this action: {self.call_to_action}",
        ]
        if niche.script_guidance:
            parts.append(niche.script_guidance.strip())
        if banned:
            parts.append(f"Never use these phrasings: {banned}.")
        prompt = "\n\n".join(parts)
        if len(prompt) > MAX_SCRIPT_PROMPT:
            prompt = prompt[: MAX_SCRIPT_PROMPT - 3].rstrip() + "..."
        return prompt


def _history_path(niche_id: str) -> Path:
    return HISTORY_DIR / f"{niche_id}.jsonl"


def load_history(niche_id: str, limit: int = HISTORY_LOOKBACK) -> list[str]:
    """Subjects already planned for this niche, newest last."""
    path = _history_path(niche_id)
    if not path.is_file():
        return []
    subjects: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            subject = str(record.get("subject", "")).strip()
            if subject:
                subjects.append(subject)
    return subjects[-limit:]


def append_history(niche_id: str, briefs: list[Brief]) -> None:
    """Record planned subjects so later batches do not repeat them."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with _history_path(niche_id).open("a", encoding="utf-8") as handle:
        for brief in briefs:
            handle.write(
                json.dumps(
                    {"subject": brief.subject, "angle": brief.angle, "planned_at": stamp},
                    ensure_ascii=False,
                )
                + "\n"
            )


def _normalize(text: str) -> str:
    """Loose key for near-duplicate detection across batches."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def build_prompt(niche: Niche, count: int, history: list[str]) -> str:
    """Ask for N briefs, each on a different assigned angle."""
    angles = [niche.angles[i % len(niche.angles)] for i in range(count)]
    angle_lines = "\n".join(f"{i + 1}. {a}" for i, a in enumerate(angles))
    pains = "\n".join(f"- {p}" for p in niche.pain_points)
    seeds = "\n".join(f"- {s}" for s in niche.seed_topics)
    covered = (
        "\n".join(f"- {s}" for s in history[-30:])
        if history
        else "- nothing yet, this is the first batch"
    )
    return f"""You are a content strategist for a faceless short-form video channel.

Niche: {niche.name}
Language: {niche.language}
Audience: {niche.audience}

What this audience struggles with:
{pains}

Themes that fit the channel:
{seeds}

Already covered - do not repeat these or restate them in different words:
{covered}

Produce exactly {count} video briefs. Brief number N must use angle number N
from this list, and the angle must visibly shape the brief:
{angle_lines}

Rules:
- Each brief must make ONE specific, falsifiable point. No topic surveys.
- The {count} briefs must be substantively different from each other. Two
  briefs that would produce similar scripts is a failure.
- Prefer specific mechanisms and numbers over broad themes.
- search_terms are for stock footage lookup: concrete, filmable scenes in
  English, 3 to 5 of them. Never abstract nouns like "success" or "growth".
- call_to_action is one concrete thing to do, never "follow for more".
- Write subject, hook, key_points and call_to_action in {niche.language}.

Return ONLY a JSON array, no prose and no code fence, of {count} objects:
[
  {{
    "subject": "short title, under 90 characters",
    "angle": "the assigned angle for this position",
    "hook": "the opening claim, one sentence, must stop a scroll",
    "key_points": ["3 to 5 specific points the script must make"],
    "search_terms": ["3 to 5 filmable stock footage scenes in English"],
    "call_to_action": "one concrete action"
  }}
]"""


def _strip_fence(text: str) -> str:
    """Drop a ```json fence if the model added one."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_briefs(response: str, niche: Niche, count: int) -> list[Brief]:
    """Parse and validate the model's JSON array into Briefs."""
    cleaned = _strip_fence(response)
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise PlanError("model response did not contain a JSON array of briefs")
    try:
        raw = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PlanError(f"could not parse briefs as JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise PlanError("briefs JSON must be a non-empty array")

    briefs: list[Brief] = []
    seen: set[str] = set()
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject", "")).strip()
        hook = str(item.get("hook", "")).strip()
        if not subject or not hook:
            continue
        key = _normalize(subject)
        if not key or key in seen:
            continue
        seen.add(key)

        points = [str(p).strip() for p in item.get("key_points", []) if str(p).strip()]
        terms = [str(t).strip() for t in item.get("search_terms", []) if str(t).strip()]
        if not points:
            continue
        # A brief without usable footage terms still renders: fall back to the
        # pack's own visual vocabulary rather than dropping the video.
        if not terms:
            terms = list(random.sample(niche.visual_terms, k=min(4, len(niche.visual_terms))))

        briefs.append(
            Brief(
                subject=subject[:90],
                angle=str(item.get("angle", niche.angles[position % len(niche.angles)])),
                hook=hook,
                key_points=points[:5],
                search_terms=terms[:5],
                call_to_action=str(item.get("call_to_action", "")).strip()
                or "Check the one number this video named before you act on it.",
                index=position,
            )
        )

    if not briefs:
        raise PlanError("no valid briefs in model response")
    return briefs[:count]


def assign_voices(briefs: list[Brief], niche: Niche, seed: int | None = None) -> None:
    """Spread the pack's voices across the batch, never twice in a row."""
    voices = list(niche.video.voice_names)
    if not voices:
        return
    rng = random.Random(seed)
    rng.shuffle(voices)
    for position, brief in enumerate(briefs):
        brief.voice_name = voices[position % len(voices)]


def to_manifest_entry(
    brief: Brief,
    niche: Niche,
    seed: int | None = None,
    aspect: str | None = None,
    paragraphs: int | None = None,
) -> dict[str, Any]:
    """Build one `cli.py --batch-file` task.

    Only VideoParams fields may appear here: the CLI rejects unknown keys
    before any task in the batch starts. aspect and paragraphs override the
    pack defaults, which is how the same niche produces 9:16 shorts and a
    longer 16:9 cut without a second pack.
    """
    rng = random.Random(f"{seed}:{brief.subject}" if seed is not None else None)
    video = niche.video
    terms = list(dict.fromkeys(brief.search_terms + list(niche.visual_terms)))[:8]
    entry: dict[str, Any] = {
        "video_subject": brief.subject,
        "video_script_prompt": brief.script_prompt(niche),
        "custom_system_prompt": niche.system_prompt.strip(),
        "video_terms": terms,
        "video_language": niche.language,
        "paragraph_number": paragraphs or video.paragraph_number,
        "video_aspect": aspect or video.aspect,
        "video_source": video.video_source,
        "video_concat_mode": "random",
        # Varying transition and clip length keeps consecutive uploads from
        # sharing an identical visual rhythm.
        "video_transition_mode": rng.choice(_TRANSITIONS),
        "video_clip_duration": rng.choice(
            [video.clip_duration, video.clip_duration + 1]
        ),
        "video_count": 1,
        "voice_name": brief.voice_name or (video.voice_names[0] if video.voice_names else ""),
        "voice_rate": video.voice_rate,
        "bgm_type": "random",
        "bgm_volume": video.bgm_volume,
        "subtitle_enabled": True,
        "subtitle_position": video.subtitle_position,
        "subtitle_display_mode": video.subtitle_display_mode,
        "subtitle_animation": video.subtitle_animation,
        "font_name": video.font_name,
        "font_size": video.font_size,
        "text_fore_color": video.text_fore_color,
        "stroke_color": video.stroke_color,
        "stroke_width": video.stroke_width,
        "n_threads": 2,
    }
    # The batch validator rejects custom_position unless the mode is custom.
    if video.subtitle_position == "custom":
        entry["custom_position"] = video.custom_position
    return entry


def generate_briefs(niche: Niche, count: int, app_config=None) -> list[Brief]:
    """Call the configured LLM and return validated briefs."""
    # Imported lazily so `growth niches` works without LLM configuration.
    from app.services import llm

    history = load_history(niche.id)
    prompt = build_prompt(niche, count, history)
    response = str(llm._generate_response(prompt, app_config=app_config) or "").strip()
    if not response:
        raise PlanError(
            "the LLM returned an empty response; check llm_provider and the api key in config.toml"
        )
    # The engine reports provider failures as a plain "Error: ..." string
    # rather than raising, so an unset api key would otherwise surface here as
    # an unparseable-JSON error.
    if response.startswith("Error:"):
        raise PlanError(response[len("Error:") :].strip())
    briefs = parse_briefs(response, niche, count)

    # The model is told what is already covered, but it is not bound by it.
    known = {_normalize(s) for s in history}
    fresh = [b for b in briefs if _normalize(b.subject) not in known]
    return fresh or briefs


def create_plan(
    niche_id: str,
    count: int,
    out_dir: Path | None = None,
    seed: int | None = None,
    record_history: bool = True,
    aspect: str | None = None,
    paragraphs: int | None = None,
    app_config=None,
) -> dict[str, Any]:
    """Generate briefs and write plan.json plus manifest.jsonl."""
    if count < 1:
        raise PlanError("count must be at least 1")
    niche = load_niche(niche_id)
    briefs = generate_briefs(niche, count, app_config=app_config)
    assign_voices(briefs, niche, seed=seed)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    directory = out_dir or (PLANS_DIR / f"{niche_id}-{stamp}")
    directory.mkdir(parents=True, exist_ok=True)

    entries = [
        to_manifest_entry(b, niche, seed=seed, aspect=aspect, paragraphs=paragraphs)
        for b in briefs
    ]
    manifest_path = directory / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    plan = {
        "niche_id": niche.id,
        "niche_name": niche.name,
        "language": niche.language,
        "platforms": list(niche.platforms),
        "hashtags": list(niche.hashtags),
        "monetization": niche.monetization,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "count": len(briefs),
        "aspect": aspect or niche.video.aspect,
        "briefs": [asdict(b) for b in briefs],
        "manifest": str(manifest_path),
    }
    plan_path = directory / "plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    plan["plan_file"] = str(plan_path)

    if record_history:
        append_history(niche.id, briefs)
    return plan
