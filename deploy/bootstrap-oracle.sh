#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/markned/flibusta-telegram-bot.git"
APP_USER="bookbot"
APP_DIR="/home/${APP_USER}/flibusta-telegram-bot"
SERVICE="flibusta-tg-bot"

if ! id "${APP_USER}" >/dev/null 2>&1; then
  sudo adduser --disabled-password --gecos "" "${APP_USER}"
fi

sudo -u "${APP_USER}" bash -lc "
  set -euo pipefail
  if [ ! -d '${APP_DIR}/.git' ]; then
    git clone '${REPO_URL}' '${APP_DIR}'
  fi
  cd '${APP_DIR}'
  git fetch origin
  git reset --hard origin/main
  python3.12 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
  [ -f .env ] || cp .env.example .env
  chmod 600 .env
"

sudo tee "/etc/systemd/system/${SERVICE}.service" >/dev/null <<UNIT
[Unit]
Description=Flibusta Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python -m app.main
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT

sudo tee "/etc/sudoers.d/${SERVICE}-deploy" >/dev/null <<SUDOERS
${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart ${SERVICE}, /usr/bin/systemctl status ${SERVICE}
SUDOERS
sudo chmod 440 "/etc/sudoers.d/${SERVICE}-deploy"

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE}"

echo
printf 'Bootstrap complete. Now edit %s as root or %s user, then start service:\n' "${APP_DIR}/.env" "${APP_USER}"
echo "  sudo nano ${APP_DIR}/.env"
echo "  sudo systemctl start ${SERVICE}"
echo "  sudo systemctl status ${SERVICE}"
