#!/usr/bin/env bash
# Prepare a fresh Ubuntu/Debian/Kali host (x86 or ARM) to render video batches.
# Idempotent: safe to re-run. Installs a small set of apt packages and a venv
# inside the repository. It never publishes anything.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_DIR/.venv"

# Every dependency ships wheels for these, and the app runs on them. 3.14 is
# rejected on purpose: pydantic fails to build the schema there, which is the
# default python3 on current Kali.
PINNED_PYTHON="3.11"
SUPPORTED_PYTHON="3.11 3.12 3.13"

# A render peaks near 600 MB, and apt itself cannot unpack large packages in
# much less. Below this the host cannot finish either step.
MIN_RAM_MB=1200

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok() { printf '\033[32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[33m  warning\033[0m %s\n' "$*" >&2; }
die() { printf '\n\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] && SUDO="" || SUDO="sudo"

log "Host"
echo "arch:   $(uname -m)"
echo "cores:  $(nproc)"
echo "memory: $(free -h | awk '/^Mem:/ {print $2}')"
echo "disk:   $(df -h "$REPO_DIR" | awk 'NR==2 {print $4 " free"}')"

TOTAL_MB="$(free -m | awk '/^Mem:/ {print $2}')"
if [ "$TOTAL_MB" -lt "$MIN_RAM_MB" ]; then
    printf '\n\033[31mSTOP: this host has %s MB of RAM. At least %s MB is needed.\033[0m\n' \
        "$TOTAL_MB" "$MIN_RAM_MB" >&2
    cat >&2 <<'RAMFIX'

Installing and rendering both fail below this. The package manager gets killed
mid-unpack by the out-of-memory killer, which leaves a half-configured system.

On WSL, memory is capped by Windows, not by the distro. Create or edit
C:\Users\<you>\.wslconfig:

    [wsl2]
    memory=8GB

then, in PowerShell:

    wsl --shutdown

Reopen the distro, check with `free -h`, and run this script again.

If an earlier run was killed part way through installing packages, do NOT run
"apt --fix-broken install": it retries the same unpack that ran the host out of
memory and fails again. Remove the leftovers instead, which needs no memory:

    sudo dpkg --remove --force-depends build-essential gcc g++ gcc-16 g++-16 \
        gcc-x86-64-linux-gnu g++-x86-64-linux-gnu \
        gcc-16-x86-64-linux-gnu g++-16-x86-64-linux-gnu
    sudo apt-get autoremove -y
    sudo dpkg --audit          # should print nothing

None of those packages are needed: every Python dependency ships a wheel.

RAMFIX
    exit 1
fi
ok "memory: ${TOTAL_MB} MB"

log "Package state"
# An install killed mid-unpack leaves packages half-configured. apt then tends
# to propose --fix-broken, which retries the very unpack that failed.
BROKEN="$(dpkg --audit 2>/dev/null | awk '/^ [a-z0-9]/ {print $1}' | tr '\n' ' ')"
if [ -n "${BROKEN// /}" ]; then
    warn "these packages are half-installed from an interrupted run: $BROKEN"
    warn "if apt fails below, remove them instead of running apt --fix-broken install:"
    warn "    sudo dpkg --remove --force-depends $BROKEN"
    warn "    sudo apt-get autoremove -y"
else
    ok "no half-installed packages"
fi

log "System packages"
# Deliberately small. Every Python dependency installs from a wheel, so no
# compiler, no python3-dev and no build-essential: that set is ~450 MB of
# downloads and is what runs a low-memory host out of RAM.
$SUDO apt-get update -qq
$SUDO apt-get install -y --no-install-recommends ffmpeg git curl ca-certificates

command -v ffmpeg >/dev/null || die "ffmpeg did not install; nothing can render without it"
echo "ffmpeg: $(ffmpeg -version | head -1)"

log "Python"

python_is_supported() {
    local candidate="$1" version
    command -v "$candidate" >/dev/null 2>&1 || return 1
    version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || return 1
    case " $SUPPORTED_PYTHON " in
        *" $version "*) echo "$version"; return 0 ;;
        *) return 1 ;;
    esac
}

UV_BIN="$(command -v uv || true)"
[ -z "$UV_BIN" ] && [ -x "$HOME/.local/bin/uv" ] && UV_BIN="$HOME/.local/bin/uv"

SYSTEM_VERSION="$(python_is_supported python3 || true)"

if [ -z "$UV_BIN" ] && [ -z "$SYSTEM_VERSION" ]; then
    SYSTEM_RAW="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)"
    warn "python3 is $SYSTEM_RAW; this project needs one of: $SUPPORTED_PYTHON"
    if [ "${SKIP_UV_INSTALL:-0}" = "1" ]; then
        die "set up a supported Python yourself, or unset SKIP_UV_INSTALL to let uv fetch $PINNED_PYTHON"
    fi
    echo "  installing uv (astral.sh) to fetch a standalone Python $PINNED_PYTHON"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UV_BIN="$HOME/.local/bin/uv"
    [ -x "$UV_BIN" ] || die "uv installation failed; install a supported Python manually"
fi

if [ ! -d "$VENV_DIR" ]; then
    if [ -n "$UV_BIN" ]; then
        # uv downloads a standalone interpreter, so the distro's python3 version
        # does not matter.
        "$UV_BIN" venv --python "$PINNED_PYTHON" "$VENV_DIR"
    else
        $SUDO apt-get install -y --no-install-recommends python3-venv
        python3 -m venv "$VENV_DIR"
    fi
fi

VENV_VERSION="$("$VENV_DIR/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
case " $SUPPORTED_PYTHON " in
    *" $VENV_VERSION "*) ok "python: $("$VENV_DIR/bin/python" --version)" ;;
    *) die "the venv at $VENV_DIR is Python $VENV_VERSION, which this project does not support ($SUPPORTED_PYTHON). Delete it and re-run." ;;
esac

log "Dependencies"
install_deps() {
    if [ -n "$UV_BIN" ]; then
        UV_HTTP_TIMEOUT=180 "$UV_BIN" pip install --python "$VENV_DIR/bin/python" "$@" \
            -r "$REPO_DIR/requirements.txt"
    else
        "$VENV_DIR/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
        "$VENV_DIR/bin/python" -m pip install --quiet --retries 5 --timeout 90 "$@" \
            -r "$REPO_DIR/requirements.txt"
    fi
}

# Wheels only on the first attempt: if one is missing, fail fast and say so
# rather than starting a source build that needs compilers this script did not
# install.
if ! install_deps --only-binary=:all:; then
    warn "a dependency has no prebuilt wheel for this platform; retrying with source builds"
    warn "if that fails, install compilers: sudo apt-get install -y build-essential python3-dev"
    install_deps
fi
ok "dependencies installed"

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

grep -qE '^\s*pexels_api_keys\s*=\s*\[\s*"' config.toml || warn "pexels_api_keys is empty in config.toml"
grep -qE '^\s*llm_provider\s*=' config.toml || warn "llm_provider is not set in config.toml"

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
