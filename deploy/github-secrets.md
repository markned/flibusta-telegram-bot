# GitHub Actions secrets for `Oracle`

Create these environment secrets in `Settings → Environments → Oracle`:

| Secret | Value |
|---|---|
| `VPS_HOST` | `92.5.102.163` |
| `VPS_USERNAME` | `bookbot` |
| `VPS_SSH_KEY` | private deploy key that matches a public key in `/home/bookbot/.ssh/authorized_keys` |
| `VPS_DEPLOY_PATH` | `/home/bookbot/flibusta-telegram-bot` |

The deploy workflow restarts `flibusta-tg-bot` after each push to `main`.
