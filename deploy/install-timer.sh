#!/usr/bin/env bash
# Schedule one render batch per day.
# Usage: install-timer.sh <niche-id> [count] [HH:MM]
#
# Uses a systemd timer where systemd is running, and falls back to cron where
# it is not, which is the common case on WSL. It only renders into
# storage/growth/out; publishing stays manual.
set -euo pipefail

NICHE="${1:?usage: install-timer.sh <niche-id> [count] [HH:MM]}"
COUNT="${2:-3}"
AT="${3:-07:00}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO_DIR/.venv/bin/python"
UNIT="mpt-growth-$NICHE"
RUN_USER="$(id -un)"

case "$AT" in
    [0-2][0-9]:[0-5][0-9]) ;;
    *) echo "time must be HH:MM, got '$AT'" >&2; exit 1 ;;
esac
HOUR="${AT%%:*}"
MINUTE="${AT##*:}"

[ -x "$VENV_PY" ] || { echo "run deploy/bootstrap-ubuntu.sh first" >&2; exit 1; }
"$VENV_PY" -m growth niches --json | grep -q "\"$NICHE\"" || {
    echo "unknown niche: $NICHE" >&2
    "$VENV_PY" -m growth niches >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] && SUDO="" || SUDO="sudo"
INIT="$(ps -p 1 -o comm= 2>/dev/null || echo unknown)"

if [ "$INIT" = "systemd" ]; then
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
else
    echo "systemd is not running (init is '$INIT'); installing a cron job instead."
    command -v crontab >/dev/null || {
        echo "crontab is not installed. Install it with:" >&2
        echo "    sudo apt-get install -y cron" >&2
        exit 1
    }

    LOG_FILE="$REPO_DIR/storage/growth/cron-$NICHE.log"
    mkdir -p "$(dirname "$LOG_FILE")"
    MARKER="# mpt-growth:$NICHE"
    LINE="$MINUTE $HOUR * * * cd $REPO_DIR && nice -n 10 $VENV_PY -m growth run $NICHE --count $COUNT >> $LOG_FILE 2>&1 $MARKER"

    # Rewrite only this niche's line so re-running never duplicates it and
    # never touches the user's other cron entries.
    EXISTING="$(crontab -l 2>/dev/null || true)"
    printf '%s\n' "$EXISTING" | grep -vF "$MARKER" | grep -v '^$' | {
        cat
        printf '%s\n' "$LINE"
    } | crontab -

    echo "installed cron job: $COUNT videos of $NICHE daily at $AT"
    echo
    echo "  check:   crontab -l | grep mpt-growth"
    echo "  logs:    tail -f $LOG_FILE"
    echo "  run now: cd $REPO_DIR && $VENV_PY -m growth run $NICHE --count $COUNT"
    echo "  remove:  crontab -l | grep -v '$MARKER' | crontab -"
    echo

    if ! pgrep -x cron >/dev/null 2>&1 && ! pgrep -x crond >/dev/null 2>&1; then
        echo "cron is installed but not running. Start it now with:"
        echo "    sudo service cron start"
        echo
        echo "On WSL without systemd it does not start by itself. Add this to"
        echo "/etc/wsl.conf and run 'wsl --shutdown' in PowerShell to fix that:"
        echo "    [boot]"
        echo "    command=service cron start"
    fi
fi

echo
echo "Videos land in storage/growth/out/$NICHE/<date>/ with captions.txt."
echo "Review them before posting: nothing is published automatically."
