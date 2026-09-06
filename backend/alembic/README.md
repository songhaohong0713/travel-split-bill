# Alembic migrations

Task 1 establishes the Alembic migration mechanism only. It deliberately creates no
product tables or revisions. Task 3 owns the first product-schema migration and must
place it in `versions/`.

Alembic reads `DATABASE_URL` at runtime. When the variable is absent, the local
SQLite URL in `alembic.ini` is used for development and test commands.
