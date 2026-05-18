# add-tests
1. Cover the regression or acceptance criterion directly.
2. Mock network-facing dependencies.
3. Prefer small deterministic fixtures.
4. Test negative paths where user trust matters.
5. Avoid sleeping, real time, and real network.
6. Run the narrow test target, then `make check`.
