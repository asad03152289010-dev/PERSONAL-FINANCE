# Private Personal Finance Web App — Vercel + PostgreSQL

A private, owner-only finance dashboard: transactions, budgets, savings goals, income, client receivables, CSV and JSON exports. The original interface and API are preserved.

**Deployment instructions:** read [VERCEL_SETUP.md](VERCEL_SETUP.md).

**Persistence:** PostgreSQL via `DATABASE_URL` is required whenever `APP_ENV=production`; SQLite is supported only as a convenience in local development. Database schema is initialized automatically on first request. Do not commit connection strings or passwords.

**Local testing:** `python -m unittest discover -s tests -v`; start locally with `python app.py` using a setup key in a local `.env` file.

If you already have records in a local SQLite database, see the optional `migrate_sqlite_to_postgres.py` script and migration steps in `VERCEL_SETUP.md`. A new PostgreSQL database does not automatically contain previous transactions.
