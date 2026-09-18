#!/usr/bin/env bash
# Run the Telegram bot as a service, so it survives reboots and disconnects.
# Usage: deploy/install-bot.sh
#
# The bot polls Telegram, so this opens no port and needs no domain or
# certificate. That is the whole reason it is practical on a free-tier VPS.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO_DIR/.venv/bin/python"
UNIT="mpt-growth-bot"
RUN_USER="$(id -un)"

[ -x "$VENV_PY" ] || { echo "run deploy/bootstrap-ubuntu.sh first" >&2; exit 1; }

# Fail before installing anything if the bot cannot start: a unit that
# restart-loops on a missing token is worse than a clear message now.
if ! "$VENV_PY" -c "
import sys
sys.path.insert(0, '$REPO_DIR')
from growth.bot import BotError, load_settings
try:
    token, chats = load_settings()
except BotError as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(1)
if not chats:
    print('telegram_allowed_chats is empty: the bot would answer no one.', file=sys.stderr)
    print('Message the bot once, then add the id it replies with:', file=sys.stderr)
    print('  python -m growth config --telegram-chat <id>', file=sys.stderr)
    raise SystemExit(1)
"; then
    exit 1
fi

INIT="$(ps -p 1 -o comm= 2>/dev/null || echo unknown)"
if [ "$INIT" != "systemd" ]; then
    cat >&2 <<NOSYSTEMD
systemd is not running (init is '$INIT'), so a service cannot be installed.

The bot is a long-running process, which cron cannot keep alive the way it can
start a batch. Run it in the foreground instead:

    $VENV_PY -m growth bot

On WSL, enable systemd by adding this to /etc/wsl.conf and running
'wsl --shutdown' in PowerShell:

    [boot]
    systemd=true
NOSYSTEMD
    exit 1
fi

[ "$(id -u)" -eq 0 ] && SUDO="" || SUDO="sudo"

$SUDO tee "/etc/systemd/system/$UNIT.service" >/dev/null <<UNITEOF
[Unit]
Description=Growth Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$REPO_DIR
ExecStart=$VENV_PY -m growth bot
# Telegram drops long polls and networks come and go; always come back.
Restart=always
RestartSec=10
# A render started from chat runs in this process, so stay out of the way of
# anything interactive on the same box.
Nice=10

[Install]
WantedBy=multi-user.target
UNITEOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now "$UNIT.service"

echo "installed $UNIT.service"
echo
echo "  status:  systemctl status $UNIT"
echo "  logs:    journalctl -u $UNIT -f"
echo "  restart: sudo systemctl restart $UNIT"
echo "  remove:  sudo systemctl disable --now $UNIT"
echo
echo "Send /start to the bot to check it answers."
