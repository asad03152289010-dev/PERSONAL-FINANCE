"""Perform a consistent SQLite backup using SQLite's online backup API."""
import os
import sqlite3
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('Usage: python backup_db.py /secure/location/backup.db')
source = Path(os.environ.get('DATABASE_PATH', Path(__file__).resolve().parent / 'data' / 'finance.db'))
destination = Path(sys.argv[1]).resolve()
if not source.exists():
    raise SystemExit('Database not found.')
if source.resolve() == destination:
    raise SystemExit('Backup destination cannot be the live database.')
if destination.exists():
    raise SystemExit('Destination exists. Pick a new filename to avoid overwriting a backup.')
destination.parent.mkdir(parents=True, exist_ok=True)
try:
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    destination.chmod(0o600)
    print(f'Backup saved: {destination}')
except BaseException:
    destination.unlink(missing_ok=True)
    raise
