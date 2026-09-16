#!/usr/bin/env bash
# Install a systemd timer that renders one batch per day.
# Usage: deploy/install-timer.sh <niche-id> [count] [HH:MM]
# It only renders into storage/growth/out; publishing stays manual.
set -euo pipefail

NICHE="${1:?usage: install-timer.sh <niche-id> [count] [HH:MM]}"
COUNT="${2:-3}"
AT="${3:-07:00}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO_DIR/.venv/bin/python"
UNIT="mpt-growth-$NICHE"
RUN_USER="$(id -un)"

[ -x "$VENV_PY" ] || { echo "run deploy/bootstrap-ubuntu.sh first" >&2; exit 1; }
"$VENV_PY" -m growth niches --json | grep -q "\"$NICHE\"" || {
    echo "unknown niche: $NICHE" >&2
    "$VENV_PY" -m growth niches >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] && SUDO="" || SUDO="sudo"

$SUDO tee "/etc/systemd/system/$UNIT.service" >/dev/null <<UNITEOF
[Unit]
Description=Render a $NICHE video batch
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$REPO_DIR
ExecStart=$VENV_PY -m growth run $NICHE --count $COUNT
# Renders are long and CPU bound; stay out of the way of interactive work.
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=6h
UNITEOF

$SUDO tee "/etc/systemd/system/$UNIT.timer" >/dev/null <<UNITEOF
[Unit]
Description=Daily $NICHE batch at $AT

[Timer]
OnCalendar=*-*-* $AT:00
# A host that was off at the scheduled time still renders once it is back.
Persistent=true
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
UNITEOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now "$UNIT.timer"

echo "installed $UNIT.timer: $COUNT videos of $NICHE daily at $AT"
echo
echo "  status:  systemctl status $UNIT.timer"
echo "  logs:    journalctl -u $UNIT.service -f"
echo "  run now: sudo systemctl start $UNIT.service"
echo "  remove:  sudo systemctl disable --now $UNIT.timer"
echo
echo "Videos land in storage/growth/out/$NICHE/<date>/ with captions.txt."
echo "Review them before posting: nothing is published automatically."
