"""Optional ONE-TIME migration from a local SQLite finance.db to empty PostgreSQL.

Requires DATABASE_URL in environment. Makes no changes to the SQLite source.
Run BEFORE creating an owner account in the new PostgreSQL deployment.
"""
import argparse
import os
import sqlite3
from pathlib import Path
import psycopg
from psycopg import sql

TABLES = {
    'users': ['id','email','password_hash','created_at'],
    'sessions': ['token_hash','user_id','csrf','expires_at'],
    'login_attempts': ['id','ip','attempted_at'],
    'transactions': ['id','user_id','date','received_date','kind','title','category','client','project','currency','amount_minor','paid_minor','rate','total_pkr_minor','received_pkr_minor','notes','created_at'],
    'budgets': ['id','user_id','month','category','limit_pkr_minor'],
    'savings_goals': ['id','user_id','name','target_pkr_minor','saved_pkr_minor','due_month','created_at'],
}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('sqlite_file',type=Path)
    ap.add_argument('--confirm',action='store_true',help='Actually copy data. Destination MUST be empty.')
    args=ap.parse_args()
    url=os.environ.get('DATABASE_URL')
    if not url: raise SystemExit('DATABASE_URL is missing.')
    if not args.sqlite_file.is_file(): raise SystemExit('SQLite source file not found.')
    src=sqlite3.connect(f'file:{args.sqlite_file.resolve()}?mode=ro',uri=True)
    src.row_factory=sqlite3.Row
    counts={table: src.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in TABLES}
    print('SQLite source:',counts)
    if not args.confirm:
        print('DRY RUN. Run again with --confirm to migrate to a completely empty PostgreSQL database.')
        return
    from app import PG_SCHEMA
    with psycopg.connect(url) as dst:
        for stmt in PG_SCHEMA.split(';'):
            if stmt.strip(): dst.execute(stmt)
        for table in TABLES:
            if dst.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]:
                raise RuntimeError(f'Postgres table {table} is not empty; migration aborted before copying.')
        for table, cols in TABLES.items():
            if table == 'transactions':
                existing={r['name'] for r in src.execute('PRAGMA table_info(transactions)')}
                if 'received_date' not in existing:
                    select_cols=["date AS received_date" if c=='received_date' else c for c in cols]
                else: select_cols=cols
            else: select_cols=cols
            rows=src.execute('SELECT '+','.join(select_cols)+' FROM '+table).fetchall()
            statement=sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,cols)),sql.SQL(',').join([sql.Placeholder()]*len(cols)))
            for row in rows:
                dst.execute(statement,tuple(row))
            if 'id' in cols:
                seq=dst.execute('SELECT pg_get_serial_sequence(%s,%s)',(table,'id')).fetchone()[0]
                if seq:
                    dst.execute('SELECT setval(%s, %s, %s)',(seq,max((r['id'] for r in rows),default=1),bool(rows)))
        print('Migrated',counts,'successfully. Source SQLite file untouched.')
    src.close()

if __name__=='__main__': main()
