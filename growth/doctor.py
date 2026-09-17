"""Preflight checks for a machine that is about to render.

Most of what goes wrong on a first run goes wrong quietly: a key that was
pasted with a space in it, a provider that is reachable but rejects the model
name, a font the packs reference that is not on disk. Each of those surfaces
several minutes into a render, as a failure that does not name its cause. This
runs every one of them in a few seconds instead, before a batch is started.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_PYTHON = {(3, 11), (3, 12), (3, 13)}
# Network probes are diagnostics, not work: none of them should be able to
# hang the command.
NETWORK_TIMEOUT = 30
MIN_FREE_GB = 5

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


def _check_materials() -> Check:
    from app.config import config
    from app.models.schema import VideoAspect
    from app.services import material

    source = str(config.app.get("video_source", "pexels")).strip() or "pexels"
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


_LOCAL_CHECKS = (_check_python, _check_ffmpeg, _check_disk, _check_config, _check_fonts)
_NETWORK_CHECKS = (_check_llm, _check_materials, _check_voice)


def run_checks(skip_network: bool = False) -> list[Check]:
    checks = list(_LOCAL_CHECKS)
    if not skip_network:
        checks += list(_NETWORK_CHECKS)
    results: list[Check] = []
    for check in checks:
        try:
            results.append(check())
        except Exception as exc:  # a broken check must not hide the others
            results.append(Check(check.__name__, FAIL, f"check itself failed: {exc}"[:150]))
    return results
