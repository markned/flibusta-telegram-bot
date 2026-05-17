# Деплой на Oracle Cloud

Бот рассчитан на лёгкий запуск рядом с уже работающими сервисами: Python + systemd, без Docker и без БД.

## Первый запуск на сервере
```bash
sudo adduser --disabled-password bookbot
sudo su - bookbot
git clone https://github.com/markned/flibusta-telegram-bot.git
cd flibusta-telegram-bot
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env
chmod 600 .env
```

## systemd
Создать `/etc/systemd/system/flibusta-tg-bot.service`:
```ini
[Unit]
Description=Flibusta Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=bookbot
Group=bookbot
WorkingDirectory=/home/bookbot/flibusta-telegram-bot
EnvironmentFile=/home/bookbot/flibusta-telegram-bot/.env
ExecStart=/home/bookbot/flibusta-telegram-bot/.venv/bin/python -m app.main
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now flibusta-tg-bot
sudo systemctl status flibusta-tg-bot
```

## GitHub Actions secrets
В окружении `Oracle` завести те же секреты, что у VPN-бота:
- `VPS_HOST`
- `VPS_USERNAME`
- `VPS_SSH_KEY`
- `VPS_DEPLOY_PATH` = `/home/bookbot/flibusta-telegram-bot`

## sudo без пароля для автодеплоя
`sudo visudo -f /etc/sudoers.d/flibusta-tg-bot-deploy`
```text
bookbot ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart flibusta-tg-bot, /usr/bin/systemctl status flibusta-tg-bot
```
