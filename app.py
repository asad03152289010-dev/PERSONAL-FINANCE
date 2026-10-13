"""Private finance tracker; PostgreSQL for Vercel, optional SQLite locally."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import traceback
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent

# Convenience for local development. Only read a file in this source folder.
dotenv = ROOT / '.env'
if dotenv.exists():
    for line in dotenv.read_text().splitlines():
        if line and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            if key.strip() not in os.environ:
                os.environ[key.strip()] = value.strip().strip('"').strip("'")

APP_ENV = os.environ.get('APP_ENV', 'development')
PRODUCTION = APP_ENV == 'production'
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
DB_FILE = Path(os.environ.get('DATABASE_PATH', str(ROOT / 'data' / 'finance.db'))).expanduser()
if PRODUCTION and not DATABASE_URL:
    raise RuntimeError('DATABASE_URL must be configured for production; SQLite storage is ephemeral on Vercel.')
SETUP_KEY = os.environ.get('OWNER_SETUP_KEY', '')
SETUP_ENABLED = len(SETUP_KEY) >= 24 and not SETUP_KEY.startswith('REPLACE_')
MAX_BODY = 16 * 1024
SESSION_SECONDS = 7 * 24 * 3600
DEFAULT_CATEGORIES = ['Food & Drinks', 'Shopping', 'Transport', 'Bills & Utilities', 'Subscriptions', 'Family', 'Health', 'Education', 'Rent', 'Entertainment', 'Other']
CURRENCIES = {'PKR', 'USD'}
NAME_RE = re.compile(r'^[\w &()\-.,/]{1,64}$', re.UNICODE)
MONTH_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')

if not DATABASE_URL:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)


class PostgresDB:
    """Expose sqlite-like execute interface while using parameterized psycopg SQL."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=None):
        # All app SQL uses positional '?' placeholders; psycopg expects '%s'.
        # Queries are internal and never contain user-controlled SQL expressions.
        return self.conn.execute(query.replace('?', '%s'), params)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()


@contextmanager
def connect():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=8) as conn:
            yield PostgresDB(conn)
        return
    db = sqlite3.connect(DB_FILE, timeout=10)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf TEXT NOT NULL, expires_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
 id BIGSERIAL PRIMARY KEY, ip TEXT NOT NULL, attempted_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_idx ON login_attempts(ip, attempted_at);
CREATE TABLE IF NOT EXISTS transactions (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 date TEXT NOT NULL, received_date TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('income','expense')),
 title TEXT NOT NULL, category TEXT NOT NULL, client TEXT NOT NULL DEFAULT '',
 project TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL CHECK(currency IN ('PKR','USD')),
 amount_minor BIGINT NOT NULL CHECK(amount_minor > 0),
 paid_minor BIGINT NOT NULL CHECK(paid_minor >= 0 AND paid_minor <= amount_minor),
 rate TEXT NOT NULL, total_pkr_minor BIGINT NOT NULL,
 received_pkr_minor BIGINT NOT NULL, notes TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
);
CREATE INDEX IF NOT EXISTS tx_user_date ON transactions(user_id, date);
CREATE TABLE IF NOT EXISTS budgets (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 month TEXT NOT NULL, category TEXT NOT NULL,
 limit_pkr_minor BIGINT NOT NULL CHECK(limit_pkr_minor >= 0),
 UNIQUE(user_id,month,category)
);
CREATE TABLE IF NOT EXISTS savings_goals (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL, target_pkr_minor BIGINT NOT NULL CHECK(target_pkr_minor > 0),
 saved_pkr_minor BIGINT NOT NULL CHECK(saved_pkr_minor >= 0),
 due_month TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS received_date TEXT NOT NULL DEFAULT '';
UPDATE transactions SET received_date=date WHERE received_date='';
"""


def initialize():
    if DATABASE_URL:
        with connect() as db:
            # Use separate statements for compatibility with pooled endpoints.
            for sql in PG_SCHEMA.strip().split(';'):
                if sql.strip():
                    db.execute(sql)
        return
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            attempted_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS attempts_idx ON login_attempts(ip, attempted_at);
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            received_date TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('income', 'expense')),
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            client TEXT NOT NULL DEFAULT '',
            project TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL CHECK(currency IN ('PKR', 'USD')),
            amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
            paid_minor INTEGER NOT NULL CHECK(paid_minor >= 0 AND paid_minor <= amount_minor),
            rate TEXT NOT NULL,
            total_pkr_minor INTEGER NOT NULL,
            received_pkr_minor INTEGER NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS tx_user_date ON transactions(user_id, date);
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            month TEXT NOT NULL,
            category TEXT NOT NULL,
            limit_pkr_minor INTEGER NOT NULL CHECK(limit_pkr_minor >= 0),
            UNIQUE(user_id, month, category)
        );
        CREATE TABLE IF NOT EXISTS savings_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            target_pkr_minor INTEGER NOT NULL CHECK(target_pkr_minor > 0),
            saved_pkr_minor INTEGER NOT NULL CHECK(saved_pkr_minor >= 0),
            due_month TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        ''')
        cols = [r['name'] for r in db.execute('PRAGMA table_info(transactions)').fetchall()]
        if 'received_date' not in cols:
            db.execute("ALTER TABLE transactions ADD COLUMN received_date TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE transactions SET received_date=date WHERE received_date=''")


_init_complete = False
_init_lock = __import__("threading").Lock()

def ensure_initialized():
    global _init_complete
    if not _init_complete:
        with _init_lock:
            if not _init_complete:
                initialize()
                _init_complete = True


class HttpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def cents(value, *, zero=False):
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d < 0 or (not zero and d == 0) or d > Decimal('999999999999'):
            raise ValueError()
        if d.as_tuple().exponent < -2:
            raise ValueError()
        return int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise HttpError(400, 'Enter a valid amount (up to 2 decimal places).')


def money_display(minor):
    return str((Decimal(minor) / 100).quantize(Decimal('.01')))


def val_text(value, label, max_len, required=False):
    if not isinstance(value, str):
        raise HttpError(400, f'{label} must be text.')
    value = value.strip()
    if required and not value:
        raise HttpError(400, f'{label} is required.')
    if len(value) > max_len or any(ord(ch) < 32 for ch in value):
        raise HttpError(400, f'{label} is too long or contains invalid characters.')
    return value


def valid_month(m):
    if not isinstance(m, str) or not MONTH_RE.fullmatch(m) or not (2020 <= int(m[:4]) <= 2100):
        raise HttpError(400, 'Invalid month. Use YYYY-MM.')
    return m


def valid_date(value):
    try:
        parsed = dt.date.fromisoformat(value)
        if parsed.isoformat() != value or not (2020 <= parsed.year <= 2100):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HttpError(400, 'Invalid date.')


def build_transaction(data):
    if not isinstance(data, dict):
        raise HttpError(400, 'Invalid form data.')
    kind = data.get('kind', '')
    if kind not in ('income', 'expense'):
        raise HttpError(400, 'Choose income or expense.')
    date = valid_date(data.get('date'))
    received_date = valid_date(data.get('received_date') or date) if kind == 'income' else date
    title = val_text(data.get('title', ''), 'Title', 120, required=True)
    category = val_text(data.get('category', ''), 'Category', 64, required=True)
    currency = data.get('currency', 'PKR')
    if currency not in CURRENCIES:
        raise HttpError(400, 'Unsupported currency.')
    amount = cents(data.get('amount'))
    paid = cents(data.get('received', data.get('amount')) if kind == 'income' else data.get('amount'), zero=True)
    if paid > amount:
        raise HttpError(400, 'Received amount cannot exceed invoice amount.')
    try:
        rate = Decimal(str(data.get('rate', '1' if currency == 'PKR' else '')))
        if not rate.is_finite() or rate <= 0 or rate > 100000 or (currency == 'PKR' and rate != 1):
            raise ValueError()
    except (ValueError, InvalidOperation, TypeError):
        raise HttpError(400, 'Enter a valid PKR exchange rate (use 1 for PKR).')
    total_pkr_minor = int((Decimal(amount) * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    received_pkr_minor = int((Decimal(paid) * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return {
        'date': date, 'received_date': received_date, 'kind': kind, 'title': title, 'category': category,
        'client': val_text(data.get('client', ''), 'Client', 100),
        'project': val_text(data.get('project', ''), 'Project', 120),
        'currency': currency, 'amount_minor': amount, 'paid_minor': paid,
        'rate': str(rate), 'total_pkr_minor': total_pkr_minor,
        'received_pkr_minor': received_pkr_minor,
        'notes': val_text(data.get('notes', ''), 'Notes', 500),
    }


def fmt_tx(row):
    t = dict(row)
    invoice_minor = t['amount_minor']
    paid_minor = t['paid_minor']
    t['amount'] = money_display(t.pop('amount_minor'))
    t['received'] = money_display(t.pop('paid_minor'))
    t['total_pkr'] = money_display(t.pop('total_pkr_minor'))
    t['received_pkr'] = money_display(t.pop('received_pkr_minor'))
    t['pending_pkr'] = money_display(int(Decimal(t['total_pkr']) * 100) - int(Decimal(t['received_pkr']) * 100))
    t['status'] = 'paid' if paid_minor == invoice_minor else ('pending' if paid_minor == 0 else 'partial')
    return t


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**15, r=8, p=1, maxmem=128*1024*1024)
    return 'scrypt$32768$' + salt.hex() + '$' + digest.hex()


def verify_password(password, saved):
    try:
        label, n, salt, digest = saved.split('$')
        if label != 'scrypt' or n != '32768':
            return False
        computed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**15, r=8, p=1, maxmem=128*1024*1024)
        return hmac.compare_digest(computed, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def read_cookies(environ):
    result = {}
    for pair in environ.get('HTTP_COOKIE', '').split(';'):
        if '=' in pair:
            k, v = pair.strip().split('=', 1)
            result[k] = v
    return result


def current_session(environ):
    tok = read_cookies(environ).get('fm_session', '')
    if not tok or len(tok) > 150:
        return None
    with connect() as db:
        return db.execute('''SELECT s.csrf, s.token_hash, s.user_id, u.email
            FROM sessions s JOIN users u ON s.user_id=u.id
            WHERE s.token_hash=? AND s.expires_at>?''',
            (hashlib.sha256(tok.encode()).hexdigest(), int(dt.datetime.now().timestamp()))).fetchone()


def cookie(name, value, max_age=None, http_only=True):
    flags = [f'{name}={value}', 'Path=/', 'SameSite=Strict']
    if http_only:
        flags.append('HttpOnly')
    if PRODUCTION:
        flags.append('Secure')
    if max_age is not None:
        flags.append(f'Max-Age={max_age}')
    return '; '.join(flags)


def request_json(environ):
    if not environ.get('CONTENT_TYPE', '').split(';')[0].strip().lower() == 'application/json':
        raise HttpError(415, 'Send application/json.')
    try:
        size = int(environ.get('CONTENT_LENGTH', '0') or '0')
    except ValueError:
        raise HttpError(400, 'Invalid request size.')
    if size <= 0 or size > MAX_BODY:
        raise HttpError(413, 'Invalid or oversized request.')
    try:
        return json.loads(environ['wsgi.input'].read(size))
    except (UnicodeError, ValueError):
        raise HttpError(400, 'Malformed JSON.')


def make_response(start_response, status=200, data=None, headers=None, raw=None, content_type='application/json; charset=utf-8'):
    if raw is None:
        raw = json.dumps(data or {}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    head = [
        ('Content-Type', content_type), ('Content-Length', str(len(raw))),
        ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'),
        ('Referrer-Policy', 'no-referrer'), ('X-Permitted-Cross-Domain-Policies', 'none'),
        ('Permissions-Policy', 'camera=(), microphone=(), geolocation=()'),
        ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
        ('Cache-Control', 'private, no-store, max-age=0'),
    ]
    if PRODUCTION:
        head.append(('Strict-Transport-Security', 'max-age=31536000; includeSubDomains'))
    head.extend(headers or [])
    start_response(f'{status} {HTTPStatus(status).phrase}', head)
    return [raw]


def csrf_ok(environ, session):
    # A cross-origin request with cookies cannot get past either this check or CSRF.
    origin = environ.get('HTTP_ORIGIN', '')
    host = environ.get('HTTP_HOST', '')
    if origin:
        parts = urlsplit(origin)
        if parts.netloc.lower() != host.lower() or parts.scheme not in ('http', 'https') or (PRODUCTION and parts.scheme != 'https'):
            raise HttpError(403, 'Cross-site request blocked.')
    elif PRODUCTION and environ.get('HTTP_SEC_FETCH_SITE', '') not in ('same-origin', 'none'):
        raise HttpError(403, 'Origin verification required.')
    supplied = environ.get('HTTP_X_CSRF_TOKEN', '')
    if session:
        expected = session['csrf']
    else:
        expected = read_cookies(environ).get('fm_guestcsrf', '')
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HttpError(403, 'Security token expired. Refresh and retry.')


def require_user(environ):
    session = current_session(environ)
    if not session:
        raise HttpError(401, 'Please sign in again.')
    return session


def month_from_query(query):
    return valid_month(query.get('month', [dt.datetime.now().strftime('%Y-%m')])[0])


def ensure_owner(db, uid):
    if not db.execute('SELECT id FROM users WHERE id=?', (uid,)).fetchone():
        raise HttpError(401, 'Session no longer valid.')


def handle_api(environ, start_response, method, path):
    query = parse_qs(environ.get('QUERY_STRING', ''))
    session = current_session(environ)
    if method not in ('GET', 'HEAD'):
        csrf_ok(environ, session)

    if path == '/api/bootstrap' and method == 'GET':
        if session:
            return make_response(start_response, data={'authenticated': True, 'email': session['email'], 'csrf': session['csrf']})
        with connect() as db:
            first_run = db.execute('SELECT COUNT(*) AS count FROM users').fetchone()['count'] == 0
        token = secrets.token_urlsafe(28)
        return make_response(start_response, data={
            'authenticated': False, 'setup_required': first_run, 'setup_enabled': SETUP_ENABLED,
            'csrf': token,
        }, headers=[('Set-Cookie', cookie('fm_guestcsrf', token, 3600, http_only=True))])

    if path == '/api/setup' and method == 'POST':
        payload = request_json(environ)
        key = str(payload.get('setup_key', ''))
        if not SETUP_ENABLED or not hmac.compare_digest(key, SETUP_KEY):
            raise HttpError(403, 'Invalid owner setup key.')
        email = val_text(payload.get('email', '').lower(), 'Email', 200, required=True)
        password = payload.get('password', '')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise HttpError(400, 'Enter a valid email.')
        if not isinstance(password, str) or not (12 <= len(password) <= 128):
            raise HttpError(400, 'Password must be 12–128 characters.')
        with connect() as db:
            # For first-run setup only. Unique index prevents simultaneous duplicate owner records.
            db.execute('SELECT pg_advisory_xact_lock(821697463)') if DATABASE_URL else db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT id FROM users LIMIT 1').fetchone():
                raise HttpError(403, 'Owner account already exists.')
            db.execute('INSERT INTO users(email,password_hash) VALUES(?,?)', (email, hash_password(password)))
        return make_response(start_response, data={'ok': True, 'message': 'Account created. Please sign in.'})

    if path == '/api/login' and method == 'POST':
        payload = request_json(environ)
        email = str(payload.get('email', '')).strip().lower()[:200]
        pw = str(payload.get('password', ''))
        ip = environ.get('REMOTE_ADDR', '')[:100]
        now = int(dt.datetime.now().timestamp())
        with connect() as db:
            db.execute('DELETE FROM login_attempts WHERE attempted_at<?', (now - 3600,))
            hits = db.execute('SELECT COUNT(*) AS count FROM login_attempts WHERE ip=? AND attempted_at>?', (ip, now - 900)).fetchone()['count']
            if hits >= 6:
                raise HttpError(429, 'Too many attempts. Try again in 15 minutes.')
            user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            # Deliberately same response for missing account and wrong password.
            if not user or not verify_password(pw, user['password_hash']):
                db.execute('INSERT INTO login_attempts(ip, attempted_at) VALUES(?,?)', (ip, now))
                db.commit()  # Persist failed-attempt count before sending 401.
                raise HttpError(401, 'Invalid email or password.')
            db.execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
            db.execute('DELETE FROM sessions WHERE expires_at<?', (now,))
            token = secrets.token_urlsafe(40)
            csrf = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES(?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), user['id'], csrf, now + SESSION_SECONDS))
        return make_response(start_response, data={'ok': True, 'email': email, 'csrf': csrf},
                             headers=[('Set-Cookie', cookie('fm_session', token, SESSION_SECONDS)),
                                      ('Set-Cookie', cookie('fm_guestcsrf', '', 0))])

    if path == '/api/logout' and method == 'POST':
        if session:
            with connect() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (session['token_hash'],))
        return make_response(start_response, data={'ok': True},
                             headers=[('Set-Cookie', cookie('fm_session', '', 0))])

    user = require_user(environ)
    uid = user['user_id']

    if path == '/api/summary' and method == 'GET':
        month = month_from_query(query)
        year = int(month[:4])
        with connect() as db:
            tx = db.execute('''SELECT kind,category,date,received_date,total_pkr_minor,received_pkr_minor
                FROM transactions WHERE user_id=? AND
                (substr(date,1,7)=? OR (kind='income' AND substr(received_date,1,7)=?))''',
                (uid, month, month)).fetchall()
            budget = db.execute('SELECT category,limit_pkr_minor FROM budgets WHERE user_id=? AND month=?', (uid, month)).fetchall()
            by_month = db.execute('''SELECT substr(CASE WHEN kind='income' THEN received_date ELSE date END,1,7) AS month,kind,
                SUM(received_pkr_minor) AS received FROM transactions
                WHERE user_id=? AND (CASE WHEN kind='income' THEN received_date ELSE date END)>=?
                AND (CASE WHEN kind='income' THEN received_date ELSE date END)<?
                GROUP BY substr(CASE WHEN kind='income' THEN received_date ELSE date END,1,7),kind''',
                (uid, f'{year}-01-01', f'{year+1}-01-01')).fetchall()
            clients = db.execute('''SELECT client, SUM(total_pkr_minor) total, SUM(received_pkr_minor) paid
                FROM transactions WHERE user_id=? AND kind='income' AND TRIM(client)<>''
                GROUP BY client ORDER BY total DESC LIMIT 15''', (uid,)).fetchall()
            all_count = db.execute('SELECT COUNT(*) AS count FROM transactions WHERE user_id=?', (uid,)).fetchone()['count']
        income = sum(r['received_pkr_minor'] for r in tx if r['kind'] == 'income' and r['received_date'][:7] == month)
        expenses = sum(r['received_pkr_minor'] for r in tx if r['kind'] == 'expense' and r['date'][:7] == month)
        pending = sum(r['total_pkr_minor'] - r['received_pkr_minor'] for r in tx if r['kind'] == 'income' and r['date'][:7] == month)
        by_category = {}
        for r in tx:
            if r['kind'] == 'expense' and r['date'][:7] == month:
                by_category[r['category']] = by_category.get(r['category'], 0) + r['received_pkr_minor']
        budget_total = sum(row['limit_pkr_minor'] for row in budget)
        yearly = {f'{year}-{i:02d}': {'income': 0, 'expenses': 0} for i in range(1, 13)}
        for row in by_month:
            field = 'income' if row['kind'] == 'income' else 'expenses'
            yearly[row['month']][field] = int(row['received'] or 0)
        return make_response(start_response, data={
            'month': month, 'income': income / 100, 'expenses': expenses / 100, 'savings': (income - expenses) / 100,
            'pending': pending / 100, 'budget': budget_total / 100, 'all_count': all_count,
            'by_category': [{'category': k, 'amount': v / 100} for k, v in sorted(by_category.items(), key=lambda x: -x[1])],
            'history': [{'month': k, 'income': v['income'] / 100, 'expenses': v['expenses'] / 100} for k, v in yearly.items()],
            'clients': [{'client': r['client'], 'invoiced': r['total'] / 100, 'received': r['paid'] / 100,
                         'outstanding': (r['total'] - r['paid']) / 100} for r in clients],
        })

    if path == '/api/transactions' and method == 'GET':
        month = month_from_query(query)
        with connect() as db:
            rows = db.execute('''SELECT * FROM transactions WHERE user_id=? AND substr(date,1,7)=?
                ORDER BY date DESC, id DESC LIMIT 500''', (uid, month)).fetchall()
        return make_response(start_response, data={'transactions': [fmt_tx(t) for t in rows], 'limited': len(rows) == 500})

    if path == '/api/transactions' and method == 'POST':
        values = build_transaction(request_json(environ))
        keys = list(values.keys())
        with connect() as db:
            cur = db.execute(f"INSERT INTO transactions(user_id,{','.join(keys)}) VALUES(?,{','.join(['?']*len(keys))})" + (" RETURNING id" if DATABASE_URL else ""), [uid] + list(values.values()))
            new_id = cur.fetchone()["id"] if DATABASE_URL else cur.lastrowid
        return make_response(start_response, 201, {'id': new_id, 'ok': True})

    match = re.fullmatch(r'/api/transactions/(\d+)', path)
    if match and method in ('PUT', 'DELETE'):
        txid = int(match.group(1))
        with connect() as db:
            if method == 'DELETE':
                res = db.execute('DELETE FROM transactions WHERE id=? AND user_id=?', (txid, uid))
            else:
                values = build_transaction(request_json(environ))
                res = db.execute('UPDATE transactions SET '+','.join(f'{k}=?' for k in values)+' WHERE id=? AND user_id=?',
                                 list(values.values()) + [txid, uid])
            if res.rowcount == 0:
                raise HttpError(404, 'Transaction not found.')
        return make_response(start_response, data={'ok': True})

    if path == '/api/budgets' and method == 'GET':
        month = month_from_query(query)
        with connect() as db:
            rows = db.execute('SELECT category,limit_pkr_minor FROM budgets WHERE month=? AND user_id=? ORDER BY category', (month, uid)).fetchall()
        return make_response(start_response, data={'budgets': [{'category': r['category'], 'limit': r['limit_pkr_minor'] / 100} for r in rows]})

    if path == '/api/budgets' and method == 'PUT':
        data = request_json(environ)
        month = valid_month(data.get('month'))
        category = val_text(data.get('category', ''), 'Category', 64, required=True)
        limit = cents(data.get('limit'), zero=True)
        with connect() as db:
            db.execute('''INSERT INTO budgets(user_id,month,category,limit_pkr_minor) VALUES(?,?,?,?)
                ON CONFLICT(user_id,month,category) DO UPDATE SET limit_pkr_minor=excluded.limit_pkr_minor''',
                (uid, month, category, limit))
        return make_response(start_response, data={'ok': True})

    if path == '/api/goals' and method == 'GET':
        with connect() as db:
            rows = db.execute('SELECT * FROM savings_goals WHERE user_id=? ORDER BY id DESC', (uid,)).fetchall()
        return make_response(start_response, data={'goals': [{'id': r['id'], 'name': r['name'],
            'target': money_display(r['target_pkr_minor']), 'saved': money_display(r['saved_pkr_minor']),
            'due_month': r['due_month']} for r in rows]})

    if (path == '/api/goals' and method == 'POST') or (re.fullmatch(r'/api/goals/\d+', path) and method in ('PUT', 'DELETE')):
        match = re.fullmatch(r'/api/goals/(\d+)', path)
        with connect() as db:
            if method == 'DELETE':
                res = db.execute('DELETE FROM savings_goals WHERE id=? AND user_id=?', (int(match.group(1)), uid))
                if res.rowcount == 0:
                    raise HttpError(404, 'Savings goal not found.')
            else:
                d = request_json(environ)
                name = val_text(d.get('name', ''), 'Name', 100, required=True)
                target = cents(d.get('target'))
                saved = cents(d.get('saved', '0'), zero=True)
                due = d.get('due_month', '')
                if due:
                    valid_month(due)
                if method == 'POST':
                    db.execute('INSERT INTO savings_goals(user_id,name,target_pkr_minor,saved_pkr_minor,due_month) VALUES(?,?,?,?,?)',
                               (uid, name, target, saved, due))
                else:
                    res = db.execute('''UPDATE savings_goals SET name=?,target_pkr_minor=?,saved_pkr_minor=?,due_month=?
                        WHERE id=? AND user_id=?''', (name, target, saved, due, int(match.group(1)), uid))
                    if res.rowcount == 0:
                        raise HttpError(404, 'Savings goal not found.')
        return make_response(start_response, 201 if method == 'POST' else 200, {'ok': True})

    if path == '/api/export.csv' and method == 'GET':
        with connect() as db:
            rows = db.execute('SELECT * FROM transactions WHERE user_id=? ORDER BY date,id', (uid,)).fetchall()
        writer_out = io.StringIO()
        writer = csv.writer(writer_out)
        fields = ['date', 'received_date', 'kind', 'title', 'category', 'client', 'project', 'currency', 'amount', 'received', 'rate', 'total_pkr', 'received_pkr', 'notes']
        writer.writerow(fields)
        for row in rows:
            obj = fmt_tx(row)
            # Spreadsheet formula injection prevention for user-input text fields.
            def safe(cell):
                s = str(cell)
                return "'" + s if s.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else s
            writer.writerow([safe(obj.get(field, '')) for field in fields])
        filename = f'finance-transactions-{dt.date.today().isoformat()}.csv'
        return make_response(start_response, raw=writer_out.getvalue().encode('utf-8-sig'),
                             content_type='text/csv; charset=utf-8',
                             headers=[('Content-Disposition', f'attachment; filename="{filename}"')])

    if path == '/api/backup.json' and method == 'GET':
        with connect() as db:
            transactions = [fmt_tx(r) for r in db.execute('SELECT * FROM transactions WHERE user_id=? ORDER BY id', (uid,)).fetchall()]
            budgets = [{'month': r['month'], 'category': r['category'], 'limit': money_display(r['limit_pkr_minor'])}
                for r in db.execute('SELECT * FROM budgets WHERE user_id=?', (uid,)).fetchall()]
            goals = [{'name': r['name'], 'target': money_display(r['target_pkr_minor']),
                     'saved': money_display(r['saved_pkr_minor']), 'due_month': r['due_month']}
                for r in db.execute('SELECT * FROM savings_goals WHERE user_id=?', (uid,)).fetchall()]
        payload = {'format': 'ksm-finance-backup-v1', 'exported_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                   'transactions': transactions, 'budgets': budgets, 'goals': goals}
        return make_response(start_response, data=payload, headers=[('Content-Disposition', 'attachment; filename="finance-backup.json"')])

    raise HttpError(404, 'Route not found.')


PUBLIC = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/static/style.css': ('static/style.css', 'text/css; charset=utf-8'),
          '/static/app.js': ('static/app.js', 'text/javascript; charset=utf-8'),
          '/favicon.svg': ('static/favicon.svg', 'image/svg+xml')}


def app(environ, start_response):
    method = environ.get('REQUEST_METHOD', 'GET').upper()
    path = environ.get('PATH_INFO', '/')
    try:
        ensure_initialized()
        if PRODUCTION and environ.get('HTTP_X_FORWARDED_PROTO', environ.get('wsgi.url_scheme', 'http')) != 'https':
            # This requires HTTPS at the reverse proxy; no finance data sent over HTTP.
            return make_response(start_response, 403, {'error': 'HTTPS required.'})
        if path.startswith('/api/'):
            return handle_api(environ, start_response, method, path)
        if method != 'GET' or path not in PUBLIC:
            raise HttpError(404, 'Not found.')
        fpath, typ = PUBLIC[path]
        raw = (ROOT / fpath).read_bytes()
        return make_response(start_response, raw=raw, content_type=typ)
    except HttpError as e:
        return make_response(start_response, e.code, {'error': e.message})
    except Exception:
        # Never disclose stack traces, SQL queries or secrets in API responses.
        traceback.print_exc()
        return make_response(start_response, 500, {'error': 'Unexpected server error. Please retry.'})


application = app  # WSGI / Gunicorn entry point

if __name__ == '__main__':
    from wsgiref.simple_server import make_server
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '8000'))
    print(f'Finance app running locally at http://{host}:{port}')
    with make_server(host, port, app) as httpd:
        httpd.serve_forever()
