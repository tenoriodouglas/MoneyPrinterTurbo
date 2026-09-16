#!/usr/bin/env bash
# Prepare a fresh Ubuntu/Debian host (x86 or ARM) to render video batches.
# Idempotent: safe to re-run. Installs nothing outside apt packages and a venv
# inside the repository. It never publishes anything.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] && SUDO="" || SUDO="sudo"

log "Host"
echo "arch:   $(uname -m)"
echo "cores:  $(nproc)"
echo "memory: $(free -h | awk '/^Mem:/ {print $2}')"
echo "disk:   $(df -h "$REPO_DIR" | awk 'NR==2 {print $4 " free"}')"

# Rendering is CPU bound and moviepy peaks near 600 MB per task; below 2 GB the
# host will swap through every render instead of failing outright.
TOTAL_MB="$(free -m | awk '/^Mem:/ {print $2}')"
if [ "$TOTAL_MB" -lt 1800 ]; then
    warn "only ${TOTAL_MB} MB of RAM. A render peaks around 600 MB and this host will swap."
    warn "see docs/HOSPEDAGEM.md: 1 GB free tiers are not usable for this workload."
fi

log "System packages"
$SUDO apt-get update -qq
$SUDO apt-get install -y --no-install-recommends \
    ffmpeg git curl ca-certificates \
    python3 python3-venv python3-dev build-essential

command -v ffmpeg >/dev/null || die "ffmpeg did not install; nothing can render without it"
echo "ffmpeg: $(ffmpeg -version | head -1)"

log "Python environment"
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# A venv created by uv has no pip, so bootstrap one before installing.
"$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1 || \
    "$VENV_DIR/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || \
    die "no pip in $VENV_DIR and ensurepip failed; delete the directory and re-run"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
# Retries because package mirrors time out more often than they fail outright.
"$VENV_DIR/bin/python" -m pip install --quiet --retries 5 --timeout 90 \
    -r "$REPO_DIR/requirements.txt"
echo "python: $("$VENV_DIR/bin/python" --version)"

log "Configuration"
if [ ! -f "$REPO_DIR/config.toml" ]; then
    cp "$REPO_DIR/config.example.toml" "$REPO_DIR/config.toml"
    # The file holds API keys; keep it unreadable to other accounts on the host.
    chmod 600 "$REPO_DIR/config.toml"
    echo "created config.toml from the example"
else
    echo "config.toml already exists, left untouched"
fi

log "Checks"
cd "$REPO_DIR"
"$VENV_DIR/bin/python" -m growth niches

MISSING=0
grep -qE '^\s*pexels_api_keys\s*=\s*\[\s*"' config.toml || { warn "pexels_api_keys is empty in config.toml"; MISSING=1; }
grep -qE '^\s*llm_provider\s*=' config.toml || { warn "llm_provider is not set in config.toml"; MISSING=1; }

cat <<'NEXT'

Setup finished.

Still to do, by hand, in config.toml:
  1. pexels_api_keys = ["..."]   free key from https://www.pexels.com/api/
  2. llm_provider and the matching api key for that provider
  3. subtitle_provider = "edge" is free and needs no model download

Then try one video end to end:
  .venv/bin/python -m growth run ai-tools --count 1

To render every morning without logging in:
  deploy/install-timer.sh ai-tools 3
NEXT

[ "$MISSING" -eq 1 ] && exit 0 || exit 0
