"""Niche pack loading and validation.

A niche pack is a TOML file describing one content vertical: its economics
(CPM), its editorial voice, the video defaults it renders with, and how it
is meant to make money. Packs are data, not code, so adding a vertical is a
file, not a patch.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NICHES_DIR = Path(__file__).resolve().parent.parent / "niches"

# Aspect ratios the render engine accepts (app/models/schema.py VideoAspect).
_VALID_ASPECTS = {"9:16", "16:9", "1:1"}
# Distribution targets a pack may declare. Drives aspect/duration defaults
# and the metadata written next to each rendered file.
_VALID_PLATFORMS = {"tiktok", "youtube_shorts", "youtube_long", "instagram_reels"}
# VideoParams.video_script_prompt is capped at 2000 chars; briefs are packed
# into it, so the planner needs the same ceiling.
MAX_SCRIPT_PROMPT = 2000
# VideoParams.custom_system_prompt ceiling.
MAX_SYSTEM_PROMPT = 8000


class NicheError(ValueError):
    """Raised when a pack is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Economics:
    """Ad-revenue profile. Used to rank packs, never to promise earnings."""

    cpm_low: float
    cpm_high: float
    competition: str
    # Share of CPM a creator actually receives on YouTube (~55%).
    revenue_share: float = 0.55

    @property
    def rpm_range(self) -> tuple[float, float]:
        return (
            round(self.cpm_low * self.revenue_share, 2),
            round(self.cpm_high * self.revenue_share, 2),
        )


@dataclass(frozen=True, slots=True)
class VideoDefaults:
    """Render settings applied to every video in the pack."""

    aspect: str = "9:16"
    voice_names: tuple[str, ...] = ()
    voice_rate: float = 1.0
    font_name: str = "BeVietnamPro-Bold.ttf"
    # Word-by-word captions show one short word at a time, so they need a
    # much larger size than a full sentence line would.
    font_size: int = 72
    text_fore_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: float = 3.0
    # Lower-middle of the frame: below the eye line, above the platform UI
    # that overlays the bottom fifth of a vertical video.
    subtitle_position: str = "custom"
    custom_position: float = 62.0
    subtitle_display_mode: str = "word_by_word"
    subtitle_animation: str = "pop_spring"
    clip_duration: int = 4
    transition_mode: str = "shuffle"
    bgm_volume: float = 0.12
    paragraph_number: int = 4
    video_source: str = "pexels"
    # Stock search returns clips for the subject as a whole, so footage for a
    # later point can appear while an earlier one is still being narrated.
    # Matching to the script orders the terms by the narration instead.
    match_materials_to_script: bool = True


@dataclass(frozen=True, slots=True)
class ImageStyle:
    """Generated-visual settings for a pack that does not use stock footage.

    These map to the engine's global ``openai_image_*`` config, which any
    OpenAI-compatible image endpoint satisfies. The prompt template is what
    makes a pack look like one channel rather than a stock-footage reel: the
    same illustration style is applied to every scene the script describes.
    """

    base_url: str = ""
    model: str = ""
    size: str = ""
    prompt_template: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)


@dataclass(frozen=True, slots=True)
class Niche:
    """One content vertical, fully specified."""

    id: str
    name: str
    language: str
    economics: Economics
    platforms: tuple[str, ...]
    audience: str
    pain_points: tuple[str, ...]
    angles: tuple[str, ...]
    system_prompt: str
    script_guidance: str
    banned_phrases: tuple[str, ...]
    seed_topics: tuple[str, ...]
    visual_terms: tuple[str, ...]
    hashtags: tuple[str, ...]
    monetization: dict[str, Any] = field(default_factory=dict)
    video: VideoDefaults = field(default_factory=VideoDefaults)
    images: ImageStyle = field(default_factory=ImageStyle)

    @property
    def score(self) -> float:
        """Midpoint RPM, used only to order packs in listings."""
        low, high = self.economics.rpm_range
        return round((low + high) / 2, 2)


def _require(data: dict[str, Any], section: str, key: str) -> Any:
    try:
        return data[section][key]
    except KeyError as exc:
        raise NicheError(f"missing required field [{section}].{key}") from exc


def _as_tuple(value: Any, section: str, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise NicheError(f"[{section}].{key} must be a list of strings")
    cleaned = tuple(v.strip() for v in value if v.strip())
    if not cleaned:
        raise NicheError(f"[{section}].{key} must not be empty")
    return cleaned


def _build_video_defaults(raw: dict[str, Any]) -> VideoDefaults:
    defaults = VideoDefaults()
    aspect = raw.get("aspect", defaults.aspect)
    if aspect not in _VALID_ASPECTS:
        raise NicheError(f"[video].aspect must be one of {sorted(_VALID_ASPECTS)}")
    voices = raw.get("voice_names", list(defaults.voice_names))
    if not isinstance(voices, list) or not all(isinstance(v, str) for v in voices):
        raise NicheError("[video].voice_names must be a list of strings")
    return VideoDefaults(
        aspect=aspect,
        # Rotating voices across a batch keeps a channel from sounding like
        # one template read N times.
        voice_names=tuple(v.strip() for v in voices if v.strip()),
        voice_rate=float(raw.get("voice_rate", defaults.voice_rate)),
        font_name=str(raw.get("font_name", defaults.font_name)),
        font_size=int(raw.get("font_size", defaults.font_size)),
        text_fore_color=str(raw.get("text_fore_color", defaults.text_fore_color)),
        stroke_color=str(raw.get("stroke_color", defaults.stroke_color)),
        stroke_width=float(raw.get("stroke_width", defaults.stroke_width)),
        subtitle_position=str(raw.get("subtitle_position", defaults.subtitle_position)),
        custom_position=float(raw.get("custom_position", defaults.custom_position)),
        subtitle_display_mode=str(
            raw.get("subtitle_display_mode", defaults.subtitle_display_mode)
        ),
        subtitle_animation=str(
            raw.get("subtitle_animation", defaults.subtitle_animation)
        ),
        clip_duration=int(raw.get("clip_duration", defaults.clip_duration)),
        transition_mode=str(raw.get("transition_mode", defaults.transition_mode)),
        bgm_volume=float(raw.get("bgm_volume", defaults.bgm_volume)),
        paragraph_number=int(raw.get("paragraph_number", defaults.paragraph_number)),
        video_source=str(raw.get("video_source", defaults.video_source)),
        match_materials_to_script=bool(
            raw.get("match_materials_to_script", defaults.match_materials_to_script)
        ),
    )


def _build_image_style(raw: dict[str, Any]) -> ImageStyle:
    style = ImageStyle(
        base_url=str(raw.get("base_url", "")).strip(),
        model=str(raw.get("model", "")).strip(),
        size=str(raw.get("size", "")).strip(),
        prompt_template=str(raw.get("prompt_template", "")).strip(),
    )
    if style.prompt_template and "{term}" not in style.prompt_template:
        raise NicheError("[images].prompt_template must contain the {term} placeholder")
    if (style.base_url or style.model) and not style.configured:
        raise NicheError("[images] needs both base_url and model, or neither")
    return style


def parse_niche(data: dict[str, Any], source: str = "<memory>") -> Niche:
    """Turn raw TOML into a validated Niche, or raise NicheError."""
    try:
        niche_id = str(_require(data, "niche", "id")).strip()
        if not niche_id:
            raise NicheError("[niche].id must not be empty")

        platforms = _as_tuple(_require(data, "niche", "platforms"), "niche", "platforms")
        unknown = set(platforms) - _VALID_PLATFORMS
        if unknown:
            raise NicheError(
                f"[niche].platforms has unsupported values: {sorted(unknown)}"
            )

        economics = Economics(
            cpm_low=float(_require(data, "economics", "cpm_low")),
            cpm_high=float(_require(data, "economics", "cpm_high")),
            competition=str(data["economics"].get("competition", "unknown")),
            revenue_share=float(data["economics"].get("revenue_share", 0.55)),
        )
        if economics.cpm_low > economics.cpm_high:
            raise NicheError("[economics].cpm_low must not exceed cpm_high")

        system_prompt = str(_require(data, "content", "system_prompt")).strip()
        if len(system_prompt) > MAX_SYSTEM_PROMPT:
            raise NicheError(
                f"[content].system_prompt exceeds {MAX_SYSTEM_PROMPT} characters"
            )

        niche = Niche(
            id=niche_id,
            name=str(_require(data, "niche", "name")),
            language=str(data["niche"].get("language", "en")),
            economics=economics,
            platforms=platforms,
            audience=str(_require(data, "audience", "description")),
            pain_points=_as_tuple(
                _require(data, "audience", "pain_points"), "audience", "pain_points"
            ),
            angles=_as_tuple(_require(data, "content", "angles"), "content", "angles"),
            system_prompt=system_prompt,
            script_guidance=str(data["content"].get("script_guidance", "")).strip(),
            banned_phrases=tuple(data["content"].get("banned_phrases", [])),
            seed_topics=tuple(data["content"].get("seed_topics", [])),
            visual_terms=_as_tuple(
                _require(data, "content", "visual_terms"), "content", "visual_terms"
            ),
            hashtags=tuple(data.get("platform", {}).get("hashtags", [])),
            monetization=dict(data.get("monetization", {})),
            video=_build_video_defaults(data.get("video", {})),
            images=_build_image_style(data.get("images", {})),
        )
    except NicheError as exc:
        raise NicheError(f"{source}: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise NicheError(f"{source}: invalid value: {exc}") from exc
    return niche


def load_niche(niche_id: str, niches_dir: Path | None = None) -> Niche:
    """Load one pack by id."""
    directory = niches_dir or NICHES_DIR
    path = directory / f"{niche_id}.toml"
    if not path.is_file():
        available = ", ".join(n.id for n in load_all_niches(directory)) or "none"
        raise NicheError(f"unknown niche {niche_id!r}; available: {available}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    niche = parse_niche(data, source=str(path))
    if niche.id != niche_id:
        raise NicheError(f"{path}: [niche].id is {niche.id!r}, expected {niche_id!r}")
    return niche


def load_all_niches(niches_dir: Path | None = None) -> list[Niche]:
    """Load every valid pack, best RPM first. Broken packs are skipped."""
    directory = niches_dir or NICHES_DIR
    if not directory.is_dir():
        return []
    packs: list[Niche] = []
    for path in sorted(directory.glob("*.toml")):
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        try:
            packs.append(parse_niche(data, source=str(path)))
        except NicheError:
            continue
    return sorted(packs, key=lambda n: n.score, reverse=True)
