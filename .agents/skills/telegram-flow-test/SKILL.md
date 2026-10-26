---
name: telegram-flow-test
description: "Verify aiogram command, text, callback and FSM flows when Telegram handlers or access controls change."
---

Trace router registration and middleware in app/main.py, app/handlers/ and app/middlewares/access.py. Check command handlers precede broad F.text, callback namespaces/data parsing, FSM transitions and cancellation, approved-user checks and admin isolation.
Exercise pagination, book download, favorites and history only where affected; preserve old reader labels for compatibility. Reuse fake message/callback objects and monkeypatch patterns in tests/test_library_features.py and tests/test_product_improvements.py. Mock Telegram API, catalog and SMTP; never require a token or send a real message.
Run `.venv/bin/python -m pytest -q tests/test_library_features.py tests/test_product_improvements.py` or narrower named tests first. Use reader-delivery-test for delivery internals. Run make check before handoff.
