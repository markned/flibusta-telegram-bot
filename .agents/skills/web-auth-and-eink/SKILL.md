---
name: web-auth-and-eink
description: "Verify aiohttp pairing, sessions, download authorization and old e-ink WebKit compatibility after web changes."
---

Read the affected symbols in app/web/app.py, app/web/ui.py, app/web/device.py, app/repositories/web_access.py and app/handlers/web.py.
Check one-time code expiry and atomic consumption/replay rejection, HMAC storage, session expiry/revocation and approved-user access. Check cookies, HTTP security, CSRF/origin behavior for mutations, input validation and every protected download/cover/reader route, including unauthorized and traversal attempts. Do not weaken route checks for device user agents.
Preserve readable Kindle/PocketBook HTML/CSS: no flex/grid/min(), inspect escaping and valid forms, device-specific delivery and mobile direct download. Reuse aiohttp TestClient/TestServer patterns in tests/test_web.py and tests/test_web_devices.py; these local in-process HTTP tests need no external service. No Playwright unless a demonstrated browser-only failure requires it.
Run `.venv/bin/python -m pytest -q tests/test_web.py tests/test_web_devices.py`. For auth/security changes additionally use differential-review scoped to changed trust boundaries; cosmetic copy does not trigger a full audit. Finish make check.
