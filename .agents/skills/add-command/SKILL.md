---
name: add-command
description: "Add a Telegram command to the existing router, menu and access control with focused handler tests."
---

1. Decide whether command belongs in an existing router.
2. Keep user copy compact and safe.
3. Add command to bot command list only if user-facing.
4. Respect access control/admin checks.
5. Add handler tests or focused unit tests.
6. Run `make check`.

Use telegram-flow-test for handler routing and access flows.
