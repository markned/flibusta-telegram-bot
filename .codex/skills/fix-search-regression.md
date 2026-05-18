# fix-search-regression
1. Reproduce with a focused test first.
2. Check F.text routing order.
3. Verify `/search` sends a result message after success.
4. Keep deterministic exact search ahead of AI.
5. Verify fallback/no-results behavior.
6. Run `make test-search` then `make check`.
