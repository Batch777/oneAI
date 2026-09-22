from contextlib import contextmanager
import hashlib
import secrets
import sqlite3
import time


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@contextmanager
def database(cfg):
    cfg.ensure_dirs()
    path=cfg.state_path/'web-auth.sqlite'
    db=sqlite3.connect(path,timeout=15); db.row_factory=sqlite3.Row; path.chmod(0o600)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS codes(hash TEXT PRIMARY KEY,expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY,id TEXT UNIQUE NOT NULL,csrf TEXT NOT NULL,name TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS attempts(ip TEXT NOT NULL,at REAL NOT NULL);
    ''')
    try: yield db
    finally: db.close()


def pair(cfg, code=None):
    # Administrator-only override; retain expiry, single use and attempt budgets.
    if code is not None and (not isinstance(code, str) or len(code) != 4 or not code.isascii() or not code.isdigit()):
        raise ValueError("pair_code_must_be_four_digits")
    with database(cfg) as db:
        db.execute('BEGIN IMMEDIATE')
        previous={r[0] for r in db.execute('SELECT hash FROM codes')}
        if code is None:
            while True:
                code=''.join(secrets.choice('0123456789') for _ in range(4))
                if digest(code) not in previous: break
        # One short code at a time; generating another replaces the previous one.
        db.execute('DELETE FROM codes')
        db.execute('INSERT INTO codes VALUES(?,?)',(digest(code),time.time()+600))
        db.commit()
    return code


def login(cfg,code,name,ip):
    now=time.time()
    with database(cfg) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM attempts WHERE at<?',(now-600,))
        local=db.execute('SELECT count(*) FROM attempts WHERE ip=?',(ip,)).fetchone()[0]
        total=db.execute('SELECT count(*) FROM attempts').fetchone()[0]
        if local>=5 or total>=10:
            db.rollback(); raise ValueError('too_many_attempts')
        code=code.replace(' ','').replace('-','') if isinstance(code,str) else ''
        row=db.execute('SELECT * FROM codes WHERE hash=? AND expires>?',(digest(code),now)).fetchone()
        if row is None:
            db.execute('INSERT INTO attempts VALUES(?,?)',(ip,now))
            db.commit(); raise ValueError('invalid_code')
        token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32); sid=secrets.token_hex(12)
        db.execute('DELETE FROM codes WHERE hash=?',(row['hash'],))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(digest(token),sid,csrf,name[:80],now,now+30*86400))
        db.commit()
        return token,csrf,sid


def session(cfg,token):
    if not token or len(token)>256: return None
    with database(cfg) as db:
        row=db.execute('SELECT * FROM sessions WHERE hash=? AND expires>?',(digest(token),time.time())).fetchone()
        return dict(row) if row else None
