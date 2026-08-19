# Codex Cloud

Cloud tasks work directly with the GitHub repository and do not depend on the
developer Mac staying online. Production remains on Oracle and deploys through
the existing GitHub Actions workflow after changes reach `main`.

## Environment

Open Codex settings → Environments and create an environment for
`markned/flibusta-telegram-bot`.

- Setup script: `bash .codex/cloud-setup.sh`
- Maintenance script: `bash .codex/cloud-maintenance.sh`
- Agent internet access: off by default; enable only for tasks that require it
- Environment variable: `TELEGRAM_BOT_TOKEN=123456:cloud-test`

The dummy token only lets settings import during tests. Never copy production
tokens or SMTP credentials into the cloud development environment.

Start a cloud task against `main`. Codex can edit and test in its isolated
container, then open a pull request. Merging the pull request starts the
existing Oracle deployment automatically.
