#!/usr/bin/env bash
# Create or update C:\Users\<you>\.wslconfig from inside WSL, so the distro
# gets enough memory to install and render.
#
# Usage: deploy/wsl-configure-memory.sh [--memory 8GB] [--swap 2GB] [--dry-run]
#
# Writing the file from here avoids the two ways doing it by hand goes wrong:
# Notepad saving it as .wslconfig.txt, and it landing somewhere other than the
# Windows user profile. An existing file is backed up and merged, never
# replaced, because it is the user's file and may hold unrelated settings.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MERGE_AWK="$SCRIPT_DIR/lib/wslconfig-merge.awk"

MEMORY=""
SWAP="2GB"
DRY_RUN=0

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok() { printf '\033[32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[33m  warning\033[0m %s\n' "$*" >&2; }
die() { printf '\n\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --memory) MEMORY="${2:?--memory needs a value like 8GB}"; shift 2 ;;
        --swap) SWAP="${2:?--swap needs a value like 2GB}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

[ -f "$MERGE_AWK" ] || die "missing $MERGE_AWK"

grep -qiE 'microsoft|wsl' /proc/sys/kernel/osrelease 2>/dev/null \
    || die "this only applies to WSL; on a plain Linux host memory is not capped this way"

log "Locating the Windows user profile"
windows_home() {
    local raw=""
    # cmd.exe warns and ignores the working directory unless it is a Windows
    # drive, so ask from /mnt/c.
    if command -v cmd.exe >/dev/null 2>&1; then
        raw="$(cd /mnt/c 2>/dev/null && cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r\n')" || true
    fi
    if [ -z "$raw" ] && command -v powershell.exe >/dev/null 2>&1; then
        raw="$(powershell.exe -NoProfile -Command 'Write-Output $env:USERPROFILE' 2>/dev/null | tr -d '\r\n')" || true
    fi
    [ -n "$raw" ] || return 1
    wslpath -u "$raw" 2>/dev/null
}

WIN_HOME="$(windows_home || true)"
if [ -z "$WIN_HOME" ] || [ ! -d "$WIN_HOME" ]; then
    die "could not find the Windows user profile.
Windows interop may be disabled in this distro. Create the file by hand in
PowerShell instead - the command is printed in docs/HOSPEDAGEM.md."
fi
CONFIG="$WIN_HOME/.wslconfig"
ok "profile: $WIN_HOME"

log "Sizing"
TOTAL_GB=""
if command -v powershell.exe >/dev/null 2>&1; then
    BYTES="$(powershell.exe -NoProfile -Command '(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory' 2>/dev/null | tr -cd '0-9')" || true
    case "${BYTES:-}" in
        ''|*[!0-9]*) ;;
        *) TOTAL_GB=$(( BYTES / 1024 / 1024 / 1024 )) ;;
    esac
fi

if [ -z "$MEMORY" ]; then
    if [ -z "$TOTAL_GB" ] || [ "$TOTAL_GB" -lt 2 ]; then
        warn "could not read the machine's total RAM; defaulting to 4GB"
        warn "pass --memory <size> if that is wrong for this machine"
        MEMORY="4GB"
    else
        # Half the machine: enough for a render, and it leaves Windows its own
        # half. WSL only reserves what it actually uses.
        HALF=$(( TOTAL_GB / 2 ))
        [ "$HALF" -lt 4 ] && HALF=4
        [ "$HALF" -gt 16 ] && HALF=16
        if [ "$TOTAL_GB" -le 4 ]; then
            warn "this machine has ${TOTAL_GB} GB of RAM in total, which is tight for rendering"
        fi
        MEMORY="${HALF}GB"
        ok "machine has ${TOTAL_GB} GB; giving WSL ${MEMORY}"
    fi
else
    ok "using requested memory: $MEMORY"
fi

log "Current .wslconfig"
if [ -f "$CONFIG" ]; then
    echo "--- $CONFIG"
    sed 's/\r$//' "$CONFIG" | sed 's/^/    /'
    echo "---"
else
    echo "    (no .wslconfig yet)"
fi

if [ -f "$CONFIG" ]; then
    MERGED="$(sed 's/\r$//' "$CONFIG" | awk -f "$MERGE_AWK" \
        -v memory="$MEMORY" -v swap="$SWAP" -v processors="")"
else
    MERGED="$(awk -f "$MERGE_AWK" \
        -v memory="$MEMORY" -v swap="$SWAP" -v processors="" </dev/null)"
fi

log "New .wslconfig"
printf '%s\n' "$MERGED" | sed 's/^/    /'

if [ "$DRY_RUN" -eq 1 ]; then
    warn "--dry-run: nothing was written"
    exit 0
fi

if [ -f "$CONFIG" ]; then
    BACKUP="$CONFIG.bak.$(date +%Y%m%d-%H%M%S)"
    cp "$CONFIG" "$BACKUP"
    ok "backed up to $(basename "$BACKUP")"
fi

# CRLF: this is a Windows-side file and some editors there mangle lone LFs.
printf '%s\n' "$MERGED" | sed 's/$/\r/' > "$CONFIG"
ok "wrote $CONFIG"

cat <<'NEXT'

Now apply it. In PowerShell (not here):

    wsl --shutdown

That stops every running distro, so close anything you care about first. Then
reopen this distro and confirm the new limit:

    free -h

"total" should now match what was written above. After that, continue the
setup from inside the distro:

    cd ~/MoneyPrinterTurbo && deploy/wsl-setup.sh
NEXT
