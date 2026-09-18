"""Set API keys in config.toml without hand-editing TOML.

config.toml is 600 lines of commented examples, and the keys that matter sit
on three of them. Editing by hand goes wrong in small ways that produce a
parse error or, worse, a silently ignored value: a missing bracket around the
Pexels list, a smart quote pasted from a browser, the key written under the
wrong provider.

This rewrites just the lines it is told to, as text, so every comment and
unrelated setting in the file survives.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.toml"
# Every key this sets lives in the [app] table.
TARGET_SECTION = "app"


class ConfigError(ValueError):
    """Raised when the config cannot be read or a value is unusable."""


def _toml_string(value: str) -> str:
    """Quote a value as a TOML basic string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_value(value: str | list[str]) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_toml_string(item) for item in value) + "]"
    return _toml_string(value)


def clean_key(raw: str, field: str) -> str:
    """Strip what copy-paste adds to an API key.

    Browsers and chat clients hand over smart quotes, wrapping quotes and
    stray whitespace. Each of those is accepted by TOML and then rejected by
    the provider, which reports it as an invalid key rather than a typo.
    """
    value = raw.strip().strip("“”‘’\"'").strip()
    if not value:
        raise ConfigError(f"{field}: the key is empty")
    if any(char.isspace() for char in value):
        raise ConfigError(f"{field}: the key contains whitespace; check the paste")
    return value


def set_app_values(text: str, updates: dict[str, str | list[str]]) -> str:
    """Rewrite the given keys inside [app], leaving the rest of the file alone."""
    if not updates:
        return text

    lines = text.splitlines()
    section = ""
    written: set[str] = set()
    out: list[str] = []
    app_end = -1

    for line in lines:
        header = re.match(r"^[ \t]*\[([^\]]+)\][ \t]*$", line)
        if header:
            # Remember where [app] ended so missing keys can be appended there.
            if section == TARGET_SECTION and app_end == -1:
                app_end = len(out)
            section = header.group(1).strip()
            out.append(line)
            continue

        if section == TARGET_SECTION:
            assignment = re.match(r"^[ \t]*([A-Za-z0-9_]+)[ \t]*=", line)
            if assignment:
                key = assignment.group(1)
                if key in updates:
                    if key in written:
                        # A duplicate would shadow the value we just wrote.
                        continue
                    out.append(f"{key} = {_toml_value(updates[key])}")
                    written.add(key)
                    continue
        out.append(line)

    if section == TARGET_SECTION and app_end == -1:
        app_end = len(out)

    missing = [key for key in updates if key not in written]
    if missing:
        insert_at = app_end if app_end != -1 else len(out)
        block = [f"{key} = {_toml_value(updates[key])}" for key in missing]
        out[insert_at:insert_at] = block

    return "\n".join(out) + "\n"


def mask(value: str) -> str:
    """Show enough of a key to recognise it, never enough to use it."""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 8}{value[-4:]}"


def apply_updates(
    updates: dict[str, str | list[str]], config_path: Path | None = None
) -> Path:
    """Back up config.toml, rewrite the given keys, and verify it still parses."""
    import tomllib

    path = config_path or CONFIG_PATH
    if not path.is_file():
        raise ConfigError(
            f"{path} does not exist; run deploy/bootstrap-ubuntu.sh or "
            "cp config.example.toml config.toml"
        )
    original = path.read_text(encoding="utf-8")
    updated = set_app_values(original, updates)

    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"the result would not be valid TOML: {exc}") from exc

    backup = path.with_suffix(f".toml.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    path.write_text(updated, encoding="utf-8")
    # The file now holds credentials; keep it to the owner.
    path.chmod(0o600)
    return backup


def build_updates(
    pexels: str | None = None,
    pixabay: str | None = None,
    provider: str | None = None,
    provider_key: str | None = None,
    provider_model: str | None = None,
    niche: str | None = None,
    image_key: str | None = None,
    telegram_token: str | None = None,
    telegram_users: list[str] | None = None,
) -> dict[str, str | list[str]]:
    """Validate the requested changes and map them to config keys."""
    updates: dict[str, str | list[str]] = {}

    if pexels:
        updates["pexels_api_keys"] = [clean_key(pexels, "pexels")]
    if pixabay:
        updates["pixabay_api_keys"] = [clean_key(pixabay, "pixabay")]

    if (provider_key or provider_model) and not provider:
        raise ConfigError(
            "--llm-key and --llm-model need --llm to say which provider they belong to"
        )

    if provider:
        from app.models.llm_provider import LLM_PROVIDERS

        name = provider.strip().lower()
        if name not in LLM_PROVIDERS:
            known = ", ".join(sorted(LLM_PROVIDERS))
            raise ConfigError(f"unknown llm provider {provider!r}; known: {known}")
        updates["llm_provider"] = name
        if provider_key:
            updates[f"{name}_api_key"] = clean_key(provider_key, name)
        if provider_model:
            # Registry defaults can point at a model the account cannot call,
            # so an explicit name has to be settable without editing the file.
            updates[f"{name}_model_name"] = clean_key(provider_model, f"{name} model")

    if telegram_token:
        updates["telegram_bot_token"] = clean_key(telegram_token, "telegram token")
    if telegram_users:
        ids: list[str] = []
        for value in telegram_users:
            cleaned = str(value).strip()
            try:
                # Stored as strings so the TOML writer stays one code path;
                # the bot parses them back to ints.
                ids.append(str(int(cleaned)))
            except ValueError as exc:
                raise ConfigError(
                    f"telegram user id must be a number, got {cleaned!r}"
                ) from exc
        updates["telegram_allowed_users"] = ids

    if image_key and not niche:
        raise ConfigError("--image-key needs --niche to say which style it serves")

    if niche:
        # The engine reads image settings from global config rather than from
        # the task, so a pack that wants generated visuals has to publish its
        # style here before a batch runs.
        from growth.niche import load_niche

        pack = load_niche(niche.strip())
        if not pack.images.configured:
            raise ConfigError(
                f"niche {pack.id!r} does not declare [images]; only packs that "
                "generate their visuals need this"
            )
        updates["openai_image_base_url"] = pack.images.base_url
        updates["openai_image_model"] = pack.images.model
        if pack.images.size:
            updates["openai_image_size"] = pack.images.size
        if pack.images.prompt_template:
            updates["openai_image_prompt_template"] = pack.images.prompt_template
        if image_key:
            updates["openai_image_api_keys"] = [clean_key(image_key, "image")]

    if not updates:
        raise ConfigError(
            "nothing to set; pass --pexels, --llm, --llm-key, --llm-model, "
            "--niche or --telegram-token"
        )
    return updates
