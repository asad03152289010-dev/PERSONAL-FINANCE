# Personal Finance — Vercel + PostgreSQL setup

## Before deploying

1. Keep your GitHub repository **private**. Unzip this project at the **repository root**, including `vercel.json`, `app.py`, `api/index.py`, `public/`, and `requirements.txt`. Remove obsolete Render config and outdated deployment settings.
2. Create a managed PostgreSQL database with backups (for example via a PostgreSQL provider in Vercel Marketplace, or Neon/Supabase). Obtain the **server-side PostgreSQL connection string**. Prefer a provider-supported pooled connection URL if available. Ensure TLS (`sslmode=require`, or provider-required TLS settings).
3. In **Vercel Project → Settings → Environment Variables**, set:
   - `DATABASE_URL` = PostgreSQL connection string. **Secret; never commit it**.
   - `APP_ENV` = `production`
   - `OWNER_SETUP_KEY` = new secure random string at least 24 characters; create with `python -c "import secrets; print(secrets.token_urlsafe(36))"`.
4. Deploy your GitHub repo on Vercel. Use **Other** framework if prompted. Root Directory = repository root. Remove old Build Command/Output Directory overrides. Redeploy **after** adding environment variables.
5. Open `https://YOUR-DOMAIN.vercel.app/api/bootstrap` and check that you get JSON with `setup_required` (a database error must be resolved first). Open the base URL and register the one owner with `OWNER_SETUP_KEY`, then log in.
6. Create one test transaction. Reload and verify it persists; sign out and in again and verify it is still there. Enable provider backups, strong account authentication, and monitor Vercel function logs.

The URL to use for the API is unchanged. Vercel serves the frontend from `public/` and routes `/api/*` to the WSGI app at `api/index.py`. You can still run the app with SQLite for **local development** by omitting `DATABASE_URL` and setting `APP_ENV=development`.

## Move existing records (only if your old SQLite app contains data)

- Before creating any account in PostgreSQL, **back up** the old local `finance.db` file. A JSON/CSV export is useful as another backup, but this script requires the raw SQLite DB file. If your old Vercel app had ephemeral SQLite data, it might already be gone; this cannot recreate missing entries.
- Install dependencies: `python -m pip install -r requirements.txt`.
- Set `DATABASE_URL` in your shell, or a local **untracked** `.env` loaded by `app.py`; don't post the URL anywhere. Script reads `DATABASE_URL` from process environment, so export it first.
- Preview: `python migrate_sqlite_to_postgres.py path/to/finance.db`
- Execute: `python migrate_sqlite_to_postgres.py path/to/finance.db --confirm`
- This aborts if **any destination table has rows**; it transfers account hashes, transactions, budgets, savings goals and sessions. Existing sessions will probably need a fresh login after domain changes. Do not perform the migration twice.

## Troubleshooting

- `500 FUNCTION_INVOCATION_FAILED`: Vercel → Deployments → latest → **Functions** / **Runtime Logs**. Confirm `DATABASE_URL` is present in the same deployment environment and DB hostname/SSL/network settings are valid. Inspect error messages privately; they may contain sensitive details.
- `HTTPS required`: confirm `APP_ENV=production` and Vercel forwards HTTPS headers; access the `.vercel.app` HTTPS URL.
- Empty finance history: a **new** PostgreSQL database has no old SQLite data. Use the one-time migration only if the original DB file still exists.

## Validation

`python -m unittest discover -s tests -v` tests local SQLite functionality. Production PostgreSQL integration was **not tested here** because no live database credentials were supplied. Do not store critical finances until a live write/read and backup/restore check succeeds.
