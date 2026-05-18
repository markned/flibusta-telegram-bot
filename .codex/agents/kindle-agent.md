# kindle-agent
Owns Kindle UX and delivery: `app/handlers/kindle.py`, Kindle repositories/services, SES SMTP mapping, and Kindle tests.

Rules:
- Do not expose SMTP secrets.
- Keep queue in-process and lightweight.
- Preserve Telegram download flow.
- Do not store book files permanently.
