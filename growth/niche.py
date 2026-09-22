"""Niche pack loading and validation.

A niche pack is a TOML file describing one content vertical: its economics
(CPM), its editorial voice, the video defaults it renders with, and how it
is meant to make money. Packs are data, not code, so adding a vertical is a
file, not a patch.
"""

from __future__ import annotations

import difflib
import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

# The engine's background-music formats, from the one module that defines them,
# so a pack and a render never disagree about what counts as audio. Unlike
# app.services.voice this import is cheap and self-contained: app.services.bgm
# pulls in stdlib plus app.utils only, never moviepy, openai or config.toml, so
# `growth niches` still runs with no engine config.
from app.services.bgm import SUPPORTED_BGM_EXTENSIONS

NICHES_DIR = Path(__file__).resolve().parent.parent / "niches"

# The engine's voice catalogue. app/services/voice.py reads this same file
# (_load_azure_voices) and, for an Edge TTS voice, hands the name straight to
# edge-tts, so these are the names that exist at render time. It is read here
# as plain JSON rather than through app.services.voice, which pulls in moviepy,
# openai and config.toml; `growth niches` must run with no engine config.
VOICES_DATA_FILE = (
    Path(__file__).resolve().parent.parent
    / "app" / "services" / "data" / "azure_voices.json"
)
# Prefixes app/services/voice.py routes away from edge-tts (is_azure_v1_voice).
# Those providers resolve names against their own service, so a pack using one
# is passed through unchecked rather than judged against the Azure list.
_OTHER_VOICE_PROVIDERS = (
    "siliconflow:",
    "gemini:",
    "mimo:",
    "minimax:",
    "elevenlabs:",
    "chatterbox:",
    "kokoro:",
    "fish_audio:",
    "voxcpm:",
)
# voice.is_no_voice: the explicit "render silent" sentinels.
_NO_VOICE_NAMES = {"no-voice", "none"}
# Parsed once per process; eight packs must not mean eight reads.
_voice_names_cache: frozenset[str] | None = None

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
# VideoParams.video_music_prompt ceiling. A pack over it builds a manifest the
# engine rejects at submission, long after the pack itself looked fine.
MAX_MUSIC_PROMPT = 2000

# The built-in songs. A pack's [music].mood names a subfolder here, which is
# what stops a ghost-story video and a finance video sharing one generic loop.
MUSIC_DIR = Path(__file__).resolve().parent.parent / "resource" / "songs"
# The AI music services the engine can actually call (app/services/task.py
# _VIDEO_MUSIC_PROVIDERS). Any other name reaches the render as an unknown
# bgm_type and quietly degrades to a random built-in track.
_VALID_MUSIC_PROVIDERS = {"sonilo", "elevenlabs"}
# A mood becomes a path segment under MUSIC_DIR, so it must be one plain folder
# name. The character class alone rejects separators; traversal and dotfiles
# are checked separately because "." and "-" are legitimate inside a name.
_SAFE_MOOD = re.compile(r"[A-Za-z0-9._-]+")


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
    # How long the narration is expected to run. A generating source needs one
    # term per clip to cover it; 0 leaves the stock default of a few terms.
    target_seconds: int = 0
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
class MusicStyle:
    """Background music picked for this vertical rather than for all of them.

    Either a ``mood`` -- a subfolder of resource/songs holding tracks that suit
    the pack -- or a ``prompt`` handed to one of the engine's AI music
    providers. Declaring both is useful: the mood is what a render falls back
    to when the provider is not configured.
    """

    mood: str = ""
    prompt: str = ""
    provider: str = ""

    @property
    def uses_ai(self) -> bool:
        """True when the pack asks a provider to generate its music."""
        return bool(self.provider and self.prompt)


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
    music: MusicStyle = field(default_factory=MusicStyle)

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


def known_voice_names() -> frozenset[str]:
    """Every voice name the engine ships, cached for the process.

    Empty when the catalogue cannot be read. Callers treat that as "cannot
    verify": one unreadable data file must not stop every pack from loading.
    """
    global _voice_names_cache
    if _voice_names_cache is None:
        try:
            with VOICES_DATA_FILE.open("r", encoding="utf-8") as handle:
                entries = json.load(handle)
            _voice_names_cache = frozenset(
                str(entry["name"])
                for entry in entries
                if isinstance(entry, dict) and entry.get("name")
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                f"cannot read the voice list at {VOICES_DATA_FILE}: {exc}; "
                "pack voices will not be checked"
            )
            _voice_names_cache = frozenset()
    return _voice_names_cache


def unknown_voice_names(names: Iterable[str]) -> list[str]:
    """The given voices the engine has no entry for, in the order given."""
    known = known_voice_names()
    if not known:
        return []
    unknown = []
    for name in names:
        if name.lower() in _NO_VOICE_NAMES or name.startswith(_OTHER_VOICE_PROVIDERS):
            continue
        # voice.parse_voice_name: the gender suffix is a label, not part of
        # the name edge-tts is asked for.
        bare = name.replace("-Female", "").replace("-Male", "").strip()
        if bare not in known:
            unknown.append(name)
    return unknown


def _voice_suggestion(name: str) -> str:
    """Closest catalogue entries to a rejected voice, as a message suffix."""
    bare = name.replace("-Female", "").replace("-Male", "").strip()
    close = difflib.get_close_matches(bare, sorted(known_voice_names()), n=3, cutoff=0.5)
    return f" (did you mean {', '.join(close)}?)" if close else ""


def _is_safe_mood(mood: str) -> bool:
    """Whether a mood is a single folder name that is safe to join to a path."""
    return bool(
        _SAFE_MOOD.fullmatch(mood) and ".." not in mood and not mood.startswith(".")
    )


def mood_tracks(mood: str) -> list[str]:
    """Track filenames inside resource/songs/<mood>/, sorted.

    Empty when the mood is empty, the folder is missing, or it holds no
    supported audio. The mood is re-checked here rather than trusted: this is a
    public helper and it is what turns the name into a filesystem path.
    """
    if not _is_safe_mood(mood):
        return []
    try:
        entries = list((MUSIC_DIR / mood).iterdir())
    except OSError:
        return []
    return sorted(
        entry.name
        for entry in entries
        if entry.is_file() and entry.suffix.lower() in SUPPORTED_BGM_EXTENSIONS
    )


def _build_video_defaults(raw: dict[str, Any]) -> VideoDefaults:
    defaults = VideoDefaults()
    aspect = raw.get("aspect", defaults.aspect)
    if aspect not in _VALID_ASPECTS:
        raise NicheError(f"[video].aspect must be one of {sorted(_VALID_ASPECTS)}")
    voices = raw.get("voice_names", list(defaults.voice_names))
    if not isinstance(voices, list) or not all(isinstance(v, str) for v in voices):
        raise NicheError("[video].voice_names must be a list of strings")
    # Rotating voices across a batch keeps a channel from sounding like one
    # template read N times.
    voice_names = tuple(v.strip() for v in voices if v.strip())
    # A name the engine does not have only fails at the audio stage, minutes
    # into a render, as a provider error that never says "voice".
    unknown = unknown_voice_names(voice_names)
    if unknown:
        detail = ", ".join(f"{name!r}{_voice_suggestion(name)}" for name in unknown)
        raise NicheError(f"[video].voice_names has unknown voices: {detail}")
    return VideoDefaults(
        aspect=aspect,
        voice_names=voice_names,
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
        target_seconds=int(raw.get("target_seconds", defaults.target_seconds)),
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


def _build_music_style(raw: dict[str, Any], source: str) -> MusicStyle:
    music = MusicStyle(
        mood=str(raw.get("mood", "")).strip(),
        prompt=str(raw.get("prompt", "")).strip(),
        # Normalised because the engine looks the provider up by exact name.
        provider=str(raw.get("provider", "")).strip().lower(),
    )
    if music.mood and not _is_safe_mood(music.mood):
        raise NicheError(
            f"[music].mood must be one folder name under {MUSIC_DIR}, using only "
            f"letters, digits, '.', '_' and '-'; got {music.mood!r}"
        )
    if music.provider and music.provider not in _VALID_MUSIC_PROVIDERS:
        raise NicheError(
            f"[music].provider must be one of {sorted(_VALID_MUSIC_PROVIDERS)}; "
            f"got {music.provider!r}"
        )
    # A provider with nothing to generate from does not fail the render: it
    # falls through to a random built-in track, so the pack looks fine and the
    # videos sound generic. That is worth refusing to load over.
    if music.provider and not music.prompt:
        raise NicheError(f"[music].provider {music.provider!r} needs a prompt")
    if len(music.prompt) > MAX_MUSIC_PROMPT:
        raise NicheError(f"[music].prompt exceeds {MAX_MUSIC_PROMPT} characters")
    # An unfilled mood folder is a job to do, not a broken pack: the owner adds
    # the tracks later, and one empty folder must not drop the pack out of
    # every listing. Same degrade as an unreadable voice catalogue above.
    if music.mood and not mood_tracks(music.mood):
        logger.warning(
            f"{source}: [music].mood is {music.mood!r} but {MUSIC_DIR / music.mood} "
            "is missing or holds no supported audio; renders fall back to the "
            "built-in songs until tracks are added"
        )
    return music


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
            music=_build_music_style(data.get("music", {}), source),
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
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        # Reaches a chat as a reply, so it must be a NicheError like the rest.
        raise NicheError(f"{path}: {exc}") from exc
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
        # Parsing is inside the guard too: a pack with a typo in its TOML is
        # just as broken as one that fails validation, and listing the others
        # is more useful than refusing to list any.
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
            packs.append(parse_niche(data, source=str(path)))
        except (NicheError, tomllib.TOMLDecodeError, OSError) as exc:
            logger.warning(f"skipping niche pack {path.name}: {exc}")
            continue
    return sorted(packs, key=lambda n: n.score, reverse=True)
