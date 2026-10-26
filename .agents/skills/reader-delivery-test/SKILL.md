---
name: reader-delivery-test
description: "Verify Kindle/PocketBook format selection, conversion, SMTP queue and delivery messages offline."
---

Trace app/services/kindle.py, conversion.py, email_sender.py, smtp_errors.py, kindle_queue.py and affected reader handlers/repositories. Keep production worker concurrency 1 and queue bounded in-process.
Check EPUB/FB2 priorities per device, unsupported formats and conversion failure, SMTP size limits, retries and terminal failure, delivery status/idempotency and displayed messages. Assert temporary file cleanup after success, exception and cancellation. Never persist books or expose destination addresses/credentials in logs.
Use tmp_path, mocked catalog downloads/converter/SMTP and existing tests/test_kindle.py, tests/test_readers.py, tests/test_ebook_metadata.py. Do not call real SMTP or download a book. Run make test-kindle plus `.venv/bin/python -m pytest -q tests/test_readers.py tests/test_ebook_metadata.py`, then make check.
