# add-db-migration
1. Add one numbered migration in `app/repositories/migrations.py`.
2. Make it idempotent and data-preserving.
3. Add only indexes needed by actual queries.
4. Update/add repository tests.
5. Verify empty DB + repeated initialization.
6. Run `make check`.
