#!/usr/bin/env bash
# Prepare a WSL2 (Ubuntu/Debian) environment and run the standard bootstrap.
#
# WSL differs from a plain VPS in three ways that matter for rendering video:
# the Windows drive is mounted over a slow filesystem, systemd is off unless
# enabled, and the distro only runs while Windows keeps it running. This
# script checks all three before handing over to bootstrap-ubuntu.sh.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok() { printf '\033[32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[33m  warning\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

BLOCKERS=0

log "WSL checks"

if ! grep -qiE 'microsoft|wsl' /proc/sys/kernel/osrelease 2>/dev/null; then
    warn "this does not look like WSL; deploy/bootstrap-ubuntu.sh is the script for a plain host"
else
    ok "running under WSL ($(grep -o 'WSL[0-9]*' /proc/sys/kernel/osrelease 2>/dev/null || echo 'WSL'))"
fi

# Rendering writes and re-reads many intermediate clips. Under /mnt the distro
# talks to the Windows filesystem through a translation layer, which is far
# slower for that pattern than the distro's own ext4 disk.
case "$REPO_DIR" in
    /mnt/*)
        warn "the repository is on the Windows drive ($REPO_DIR)."
        warn "Rendering does heavy small-file I/O and this path goes through a"
        warn "translation layer, so every batch will be much slower than it needs to be."
        warn "Move it into the Linux filesystem instead:"
        warn "    cp -r \"$REPO_DIR\" ~/MoneyPrinterTurbo && cd ~/MoneyPrinterTurbo"
        BLOCKERS=1
        ;;
    *)
        ok "repository is on the Linux filesystem"
        ;;
esac

# Below this neither apt nor a render can finish, so stop here rather than
# letting the out-of-memory killer interrupt a half-finished install.
MIN_RAM_MB=1200
TOTAL_MB="$(free -m | awk '/^Mem:/ {print $2}')"
if [ "$TOTAL_MB" -lt "$MIN_RAM_MB" ]; then
    printf '\n\033[31mSTOP: WSL sees only %s MB of RAM. At least %s MB is needed.\033[0m\n' \
        "$TOTAL_MB" "$MIN_RAM_MB" >&2
    cat >&2 <<'RAMFIX'

WSL memory is capped by Windows, not by the distro, and the default can be a
small fraction of the machine. Create or edit C:\Users\<you>\.wslconfig:

    [wsl2]
    memory=8GB

Then, in PowerShell:

    wsl --shutdown

Reopen the distro, confirm with `free -h`, and run this script again.

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
elif [ "$TOTAL_MB" -lt 2048 ]; then
    warn "WSL sees ${TOTAL_MB} MB of RAM; a render peaks near 600 MB, so this is tight."
    warn "Raise it with memory=8GB in C:\\Users\\<you>\\.wslconfig, then 'wsl --shutdown'."
    BLOCKERS=1
else
    ok "memory available to WSL: ${TOTAL_MB} MB"
fi

AVAIL_GB="$(df -BG --output=avail "$REPO_DIR" | tail -1 | tr -dc '0-9')"
if [ "${AVAIL_GB:-0}" -lt 10 ]; then
    warn "only ${AVAIL_GB} GB free; stock footage cache and outputs need room"
else
    ok "disk free: ${AVAIL_GB} GB"
fi

INIT="$(ps -p 1 -o comm= 2>/dev/null || echo unknown)"
if [ "$INIT" = "systemd" ]; then
    ok "systemd is running, deploy/install-timer.sh can schedule daily batches"
else
    warn "systemd is not running (init is '$INIT'), so the systemd timer will not install."
    warn "Either enable it by adding this to /etc/wsl.conf and running 'wsl --shutdown':"
    warn "    [boot]"
    warn "    systemd=true"
    warn "or let deploy/install-timer.sh fall back to cron, which it does automatically."
fi

log "Running the standard bootstrap"
bash "$REPO_DIR/deploy/bootstrap-ubuntu.sh"

log "WSL notes"
cat <<'NOTES'
Scheduling on WSL is not the same as on a server:

  - WSL only runs while Windows is on and the distro has been started. A timer
    or cron job inside WSL does not fire on a sleeping or shut-down machine.
  - For a batch that really runs unattended, schedule it from Windows instead:
    Task Scheduler -> Create Task -> Action:
        wsl.exe -d <distro> -- bash -lc "cd ~/MoneyPrinterTurbo && .venv/bin/python -m growth run ai-tools --count 3"
  - Rendering is CPU bound and will use most cores. Lower --count, or let the
    systemd unit's Nice=10 keep it out of the way of interactive work.
NOTES

[ "$BLOCKERS" -eq 0 ] || warn "address the warnings above before running a real batch"
exit 0
