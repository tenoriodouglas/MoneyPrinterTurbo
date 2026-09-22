"""Preflight checks for a machine that is about to render.

Most of what goes wrong on a first run goes wrong quietly: a key that was
pasted with a space in it, a provider that is reachable but rejects the model
name, a font the packs reference that is not on disk. Each of those surfaces
several minutes into a render, as a failure that does not name its cause. This
runs every one of them in a few seconds instead, before a batch is started.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_PYTHON = {(3, 11), (3, 12), (3, 13)}
# Network probes are diagnostics, not work: none of them should be able to
# hang the command.
NETWORK_TIMEOUT = 30
MIN_FREE_GB = 5
# The AI music services a pack may name (app/services/task.py
# _VIDEO_MUSIC_PROVIDERS), mapped to the credential each one's is_enabled()
# reads: config.toml table, key, and the environment variable it accepts
# instead (sonilo.get_api_key, elevenlabs_music.get_api_key). Mirrored rather
# than imported because app.services.task pulls in moviepy and every provider
# SDK, and a local check has to run with no engine configuration at all.
MUSIC_PROVIDER_KEYS = {
    "sonilo": ("app", "sonilo_api_key", "SONILO_API_KEY"),
    "elevenlabs": ("elevenlabs", "api_key", "ELEVENLABS_API_KEY"),
}

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(slots=True)
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def _check_ffmpeg() -> Check:
    binary = shutil.which("ffmpeg")
    if not binary:
        return Check(
            "ffmpeg", FAIL, "not found on PATH",
            "sudo apt-get install -y ffmpeg",
        )
    try:
        out = subprocess.run(
            [binary, "-version"], capture_output=True, text=True, timeout=15, check=True
        )
        return Check("ffmpeg", OK, out.stdout.splitlines()[0][:70])
    except (subprocess.SubprocessError, OSError) as exc:
        return Check("ffmpeg", FAIL, f"found but not runnable: {exc}")


def _check_python() -> Check:
    version = sys.version_info[:2]
    text = f"{version[0]}.{version[1]}"
    if version in SUPPORTED_PYTHON:
        return Check("python", OK, text)
    return Check(
        "python", FAIL, f"{text} is not supported (needs 3.11, 3.12 or 3.13)",
        "delete .venv and re-run deploy/bootstrap-ubuntu.sh",
    )


def _check_disk() -> Check:
    usage = shutil.disk_usage(REPO_ROOT)
    free_gb = usage.free / (1024**3)
    if free_gb < MIN_FREE_GB:
        return Check(
            "disk", WARN, f"{free_gb:.1f} GB free",
            "stock footage and renders need room; clear space or prune storage/",
        )
    return Check("disk", OK, f"{free_gb:.1f} GB free")


def _check_config() -> Check:
    if not (REPO_ROOT / "config.toml").is_file():
        return Check(
            "config.toml", FAIL, "missing",
            "cp config.example.toml config.toml",
        )
    return Check("config.toml", OK, "present")


def _check_fonts() -> Check:
    from growth.niche import load_all_niches

    fonts_dir = REPO_ROOT / "resource" / "fonts"
    missing = sorted(
        {
            niche.video.font_name
            for niche in load_all_niches()
            if not (fonts_dir / niche.video.font_name).is_file()
        }
    )
    if missing:
        return Check(
            "fonts", FAIL, f"packs reference missing fonts: {', '.join(missing)}",
            f"add the file to {fonts_dir} or change font_name in the pack",
        )
    return Check("fonts", OK, "every pack's font is present")


def _check_pack_voices() -> Check:
    """Every voice a pack names must exist in the engine's list.

    The pack files are read here rather than through load_all_niches, which
    drops a pack whose voices are wrong instead of reporting it: the pack that
    needs naming is exactly the one that would be missing from the listing.
    """
    import tomllib

    from growth.niche import (
        NICHES_DIR,
        VOICES_DATA_FILE,
        known_voice_names,
        unknown_voice_names,
    )

    if not known_voice_names():
        return Check(
            "voices", WARN, f"cannot read the voice list at {VOICES_DATA_FILE}",
            f"git checkout -- {VOICES_DATA_FILE}",
        )
    for path in sorted(NICHES_DIR.glob("*.toml")):
        try:
            with path.open("rb") as handle:
                raw = tomllib.load(handle).get("video", {}).get("voice_names", [])
        except (tomllib.TOMLDecodeError, OSError) as exc:
            return Check("voices", FAIL, f"{path.name}: {exc}"[:150], f"fix the TOML in {path}")
        if not isinstance(raw, list):
            return Check(
                "voices", FAIL, f"{path.name}: [video].voice_names must be a list of strings",
                f"edit [video].voice_names in {path}",
            )
        unknown = unknown_voice_names(str(v).strip() for v in raw)
        if unknown:
            return Check(
                "voices", FAIL,
                f"{path.name} names voices that do not exist: {', '.join(unknown)}",
                f"edit [video].voice_names in {path}; the engine's list is {VOICES_DATA_FILE}",
            )
    return Check("voices", OK, "every pack's voices exist")


def _config_tables() -> dict[str, Any]:
    """config.toml as plain tables, empty when it cannot be read.

    Read from disk rather than through app.config, which snapshots the file
    once at import and brings the engine with it. A missing or unparsable file
    is not reported here: _check_config already names that.
    """
    import tomllib

    try:
        # utf-8-sig mirrors app/config: a BOM the engine reads happily is a
        # parse error to bare tomllib.
        return tomllib.loads((REPO_ROOT / "config.toml").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _music_provider_configured(tables: dict[str, Any], provider: str) -> bool:
    """Whether that provider's key is set, in config.toml or the environment."""
    section, key, env_var = MUSIC_PROVIDER_KEYS[provider]
    table = tables.get(section)
    stored = str(table.get(key, "") or "").strip() if isinstance(table, dict) else ""
    return bool(stored or os.getenv(env_var, "").strip())


def _first_few(items: list[str], limit: int = 3, separator: str = "; ") -> str:
    """Join for a one-line detail: the first few, then a count for the rest."""
    remaining = len(items) - limit
    joined = separator.join(items[:limit])
    return f"{joined} (+{remaining} more)" if remaining > 0 else joined


def _repo_path(path: Path) -> str:
    """Repo-relative where possible: a fix is typed at the repo root."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _packs_label(packs: list[str], limit: int = 2) -> str:
    """Name the packs while there are few enough to be worth naming."""
    return ", ".join(packs) if len(packs) <= limit else f"{len(packs)} packs"


def _check_pack_music() -> Check:
    """Every pack's [music] must resolve to tracks on disk, or to a provider
    that can actually be called.

    The pack files are read directly, like the voices check above and for the
    same reason: a pack whose [music] is malformed is dropped by
    load_all_niches, so the pack worth naming is the one missing from it.

    The two faults get different severities because the renders do. A mood
    folder with nothing in it still produces a video: plan.py falls back to
    bgm_type "random" and the batch ships, sounding like every other channel,
    so it is a warning. A provider is different: plan.py passes it through as
    the task's bgm_type, and app/services/task.py refuses a task whose music
    provider has no key at preflight, before a line of script is written. That
    batch renders nothing at all, which is a failure.
    """
    import tomllib

    from app.services.bgm import SUPPORTED_BGM_EXTENSIONS
    from growth.niche import MUSIC_DIR, NICHES_DIR, MusicStyle, mood_tracks

    formats = "/".join(extension.lstrip(".") for extension in SUPPORTED_BGM_EXTENSIONS)
    tables = _config_tables()
    paid: list[str] = []  # packs that buy their music one video at a time
    filled: set[str] = set()
    fails: list[tuple[str, str]] = []
    # (mood, what is wrong with it) -> the packs that named it. Grouped by mood
    # rather than by pack because both markets ship a pack per vertical: one
    # unfilled folder is otherwise reported eight times, and the folder to go
    # and fill is lost in the repetition.
    silent: dict[tuple[str, str], list[str]] = {}

    for path in sorted(NICHES_DIR.glob("*.toml")):
        try:
            with path.open("rb") as handle:
                raw = tomllib.load(handle).get("music", {})
        except (tomllib.TOMLDecodeError, OSError) as exc:
            return Check("music", FAIL, f"{path.name}: {exc}"[:150], f"fix the TOML in {path}")
        if not isinstance(raw, dict):
            return Check(
                "music", FAIL, f"{path.name}: [music] must be a table",
                f"edit [music] in {path}",
            )
        # Normalised exactly as niche._build_music_style does, so the doctor
        # and the loader never read one pack two ways.
        style = MusicStyle(
            mood=str(raw.get("mood", "")).strip(),
            prompt=str(raw.get("prompt", "")).strip(),
            provider=str(raw.get("provider", "")).strip().lower(),
        )
        pack = path.stem

        if style.provider:
            if style.provider not in MUSIC_PROVIDER_KEYS:
                fails.append((
                    f"{pack}: no music provider is called {style.provider!r}",
                    f"set [music].provider in {path} to one of "
                    f"{', '.join(sorted(MUSIC_PROVIDER_KEYS))}, or remove it",
                ))
            elif not style.uses_ai:
                # niche.py refuses to load a pack like this, which takes it out
                # of every listing without taking it off the render command.
                fails.append((
                    f"{pack}: [music].provider {style.provider} has no prompt to generate from",
                    f"add [music].prompt to {path}, or remove [music].provider",
                ))
            elif not _music_provider_configured(tables, style.provider):
                section, key, env_var = MUSIC_PROVIDER_KEYS[style.provider]
                fails.append((
                    f"{pack}: {style.provider} generates every video's music "
                    "and has no credential",
                    f"set {key} under [{section}] in config.toml "
                    f"(or export {env_var}), or remove [music].provider from {path}",
                ))
            else:
                paid.append(f"{pack} via {style.provider}")
            # A pack on the provider path never reads its mood folder; the
            # folder only matters when the provider is missing, which is the
            # failure above rather than a second warning here.
            continue

        if not style.mood:
            continue
        if mood_tracks(style.mood):
            filled.add(style.mood)
        elif (MUSIC_DIR / style.mood).is_dir():
            silent.setdefault((style.mood, "holds no audio"), []).append(pack)
        else:
            silent.setdefault((style.mood, "has no folder"), []).append(pack)

    if fails:
        detail = _first_few([text for text, _ in fails])
        # Counted rather than left for the next run: fixing the key would
        # otherwise turn the fail into a warning nobody had been told about.
        if silent:
            detail += f"; also {len(silent)} mood folder(s) with no music"
        return Check(
            "music", FAIL, detail[:200],
            _first_few(list(dict.fromkeys(fix for _, fix in fails)), limit=2),
        )
    if silent:
        groups = [
            f"{_repo_path(MUSIC_DIR / mood)} {problem} ({_packs_label(packs)})"
            for (mood, problem), packs in silent.items()
        ]
        detail = f"{_first_few(groups, limit=2)}; those fall back to the built-in songs"
        if paid:
            detail += f"; billed per video: {_first_few(paid, limit=2)}"
        folders = [_repo_path(MUSIC_DIR / mood) for mood, _ in silent]
        fix = f"put {formats} files in {_first_few(folders, limit=3, separator=', ')}"
        if any(problem == "has no folder" for _, problem in silent):
            fix += "; the missing ones have to be created first"
        return Check("music", WARN, detail[:200], fix)
    parts = []
    if paid:
        # Generated music is charged per render, so the owner is told it is on
        # even when nothing is wrong with it.
        parts.append(f"billed per video: {_first_few(paid)}")
    if filled:
        parts.append(f"tracks in {', '.join(sorted(filled))}")
    return Check(
        "music", OK,
        "; ".join(parts)[:200] or "no pack sets [music]; renders use the built-in songs",
    )


def _summarise_provider_error(message: str) -> tuple[str, str]:
    """Reduce a provider error to what the user can act on.

    Providers answer failures with a full JSON dump. Truncating it blindly
    drops the two fields that matter - the model being called and whether the
    plan allows it - and leaves advice that points at the api key instead.
    """
    text = " ".join(str(message).split())
    model = ""
    match = re.search(r"model:\s*([A-Za-z0-9._-]+)", text)
    if match:
        model = match.group(1)

    quota = "RESOURCE_EXHAUSTED" in text or "429" in text or "quota" in text.lower()
    if quota:
        # limit: 0 means the plan has no allowance for that model at all,
        # which is a different problem from having used the allowance up.
        no_allowance = re.search(r"limit:\s*0\b", text) is not None
        detail = f"quota rejected for model {model or 'the configured model'}"
        if no_allowance:
            fix = (
                f"your plan has no free quota for {model or 'this model'}; "
                "switch to one it allows, e.g. "
                "python -m growth config --llm gemini --llm-model gemini-3.1-flash-lite"
            )
        else:
            fix = "the per-minute or daily quota is spent; wait, or use a smaller model"
        return detail, fix

    short = text[:140]
    # The model name is the one detail worth keeping past the cut, whatever
    # kind of failure this is.
    if model and model not in short:
        short = f"{short} (model {model})"
    return short, "check the api key, model name and base url for this provider"


def _check_llm() -> Check:
    from app.config import config
    from app.services import llm

    provider = str(config.app.get("llm_provider", "")).strip()
    if not provider:
        return Check(
            "llm", FAIL, "llm_provider is not set",
            "set llm_provider and its api key in config.toml",
        )
    key_name = f"{provider}_api_key"
    if not str(config.app.get(key_name, "")).strip():
        return Check(
            "llm", FAIL, f"{provider}: {key_name} is empty",
            f"set {key_name} in config.toml",
        )
    try:
        succeeded, error, elapsed = llm.test_connection()
    except Exception as exc:  # any provider SDK may raise its own type
        detail, fix = _summarise_provider_error(str(exc))
        return Check("llm", FAIL, f"{provider}: {detail}", fix)
    if not succeeded:
        detail, fix = _summarise_provider_error(error)
        return Check("llm", FAIL, f"{provider}: {detail}", fix)
    return Check("llm", OK, f"{provider} responded in {elapsed:.1f}s")


def _check_generated_images() -> Check:
    """Generate one real image, because a misconfigured endpoint only shows up
    several minutes into a render otherwise."""
    import tempfile

    from app.config import config
    from app.models.schema import VideoAspect
    from app.services import material

    if not material.is_openai_image_enabled():
        return Check(
            "materials", FAIL, "the image endpoint is not configured",
            "apply a pack's style: python -m growth config --niche <id> --image-key <key>",
        )
    model = str(config.app.get("openai_image_model", "")).strip()
    try:
        with tempfile.TemporaryDirectory() as temp:
            items = material.generate_images_openai(
                search_term="a lone figure in a moonlit field looking up at the sky",
                minimum_duration=3,
                video_aspect=VideoAspect.portrait,
                save_dir=temp,
            )
    except Exception as exc:
        detail, fix = _summarise_provider_error(str(exc))
        return Check("materials", FAIL, f"{model}: {detail}", fix)
    if not items:
        return Check(
            "materials", FAIL, f"{model} returned no image",
            "check openai_image_base_url, the model name and the api key",
        )
    return Check("materials", OK, f"{model} generated {len(items)} image")


def _check_materials(source: str | None = None) -> Check:
    from app.config import config
    from app.models.schema import VideoAspect
    from app.services import material

    source = (source or str(config.app.get("video_source", "pexels"))).strip() or "pexels"
    if source == "openai_image":
        return _check_generated_images()

    searchers = {
        "pexels": ("pexels_api_keys", material.search_videos_pexels),
        "pixabay": ("pixabay_api_keys", material.search_videos_pixabay),
    }
    if source not in searchers:
        return Check("materials", WARN, f"{source}: no preflight for this provider")

    key_name, search = searchers[source]
    keys = config.app.get(key_name) or []
    if not keys:
        return Check(
            "materials", FAIL, f"{key_name} is empty",
            f"free key from https://www.pexels.com/api/ then set {key_name} in config.toml",
        )
    try:
        items = search(
            search_term="city skyline",
            minimum_duration=3,
            video_aspect=VideoAspect.portrait,
        )
    except Exception as exc:
        return Check("materials", FAIL, f"{source}: {exc}".strip()[:150])
    if not items:
        return Check(
            "materials", FAIL, f"{source} returned no clips for a common term",
            "the key is probably rejected; check it at the provider",
        )
    return Check("materials", OK, f"{source} returned {len(items)} clips")


def _check_voice() -> Check:
    import asyncio

    import edge_tts

    async def probe() -> int:
        communicate = edge_tts.Communicate("ok", "en-US-JennyNeural")
        total = 0
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                total += len(chunk["data"])
        return total

    try:
        size = asyncio.run(asyncio.wait_for(probe(), timeout=NETWORK_TIMEOUT))
    except Exception as exc:
        return Check(
            "voice", FAIL, f"edge-tts unreachable: {exc}".strip()[:150],
            "edge-tts needs outbound https; check the network or a proxy's TLS",
        )
    if size <= 0:
        return Check("voice", FAIL, "edge-tts returned no audio")
    return Check("voice", OK, f"edge-tts synthesised {size} bytes")


_LOCAL_CHECKS = (
    _check_python,
    _check_ffmpeg,
    _check_disk,
    _check_config,
    _check_fonts,
    _check_pack_voices,
    _check_pack_music,
)
_NETWORK_CHECKS = (_check_llm, _check_materials, _check_voice)


def run_checks(skip_network: bool = False, niche_id: str | None = None) -> list[Check]:
    """Run every check, optionally against the material source a pack uses.

    A pack sets its own video_source per task, so checking the global default
    would test a provider the batch is never going to call.
    """
    source: str | None = None
    if niche_id:
        from growth.niche import load_niche

        source = load_niche(niche_id).video.video_source

    checks = list(_LOCAL_CHECKS)
    if not skip_network:
        checks += list(_NETWORK_CHECKS)
    results: list[Check] = []
    for check in checks:
        try:
            if check is _check_materials:
                results.append(check(source))
            else:
                results.append(check())
        except Exception as exc:  # a broken check must not hide the others
            results.append(Check(check.__name__, FAIL, f"check itself failed: {exc}"[:150]))
    return results
