"""Cloud-side journal. Dispatch is at-most-once; ambiguous delivery is never retried."""
from __future__ import annotations
import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] > 3:
                raise RuntimeError('session_database_from_newer_version')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS session_hosts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
                    capabilities TEXT NOT NULL, heartbeat REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    id TEXT PRIMARY KEY, host_id TEXT NOT NULL, provider TEXT NOT NULL,
                    workspace TEXT NOT NULL, title TEXT NOT NULL, state TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, mode TEXT NOT NULL DEFAULT 'managed');
                CREATE TABLE IF NOT EXISTS session_commands (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, dispatched REAL);
                CREATE INDEX IF NOT EXISTS session_dispatch ON session_commands(state,created);
                CREATE TABLE IF NOT EXISTS session_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS session_replay ON session_events(session_id,seq);
                PRAGMA user_version=3;
            ''')
            if 'mode' not in {r[1] for r in db.execute('PRAGMA table_info(agent_sessions)')}:
                db.execute("ALTER TABLE agent_sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'managed'")
            if 'dispatched' not in {r[1] for r in db.execute('PRAGMA table_info(session_commands)')}:
                db.execute('ALTER TABLE session_commands ADD COLUMN dispatched REAL')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enroll(self, name, workspaces, providers, read_only=False):
        if not workspaces or not set(providers) <= {'codex', 'pi'} or (not providers and not read_only):
            raise ValueError('invalid_capabilities')
        hid, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        caps = {'workspaces': workspaces, 'providers': providers, 'protocol': 1, 'read_only': read_only}
        with self.db() as db:
            db.execute('INSERT INTO session_hosts VALUES(?,?,?,?,0)',
                       (hid, name, hashlib.sha256(token.encode()).hexdigest(), encode(caps)))
        return {'id': hid, 'token': token, **caps}

    def bind(self, hid, workspace, title, thread_id):
        """Administrator-only binding; browser clients cannot choose arbitrary local threads."""
        if not isinstance(thread_id, str) or not 1 <= len(thread_id) <= 128:
            raise ValueError('invalid_thread_id')
        with self.db() as db:
            host = db.execute('SELECT capabilities FROM session_hosts WHERE id=?', (hid,)).fetchone()
            if not host or workspace not in json.loads(host[0])['workspaces']:
                raise ValueError('capability_rejected')
            sid = uuid.uuid4().hex
            db.execute("INSERT INTO agent_sessions(id,host_id,provider,workspace,title,state,revision,created,mode) VALUES(?,?,?,?,?,?,0,?,'observe')",
                       (sid, hid, 'codex', workspace, title, 'observing', time.time()))
            self._event(db, uuid.uuid4().hex, sid, 'runtime', {'remote_id': thread_id, 'mode': 'observe'})
        return {'session_id': sid, 'thread_id': thread_id, 'workspace': workspace}

    def authenticate(self, token):
        with self.db() as db:
            row = db.execute('SELECT id FROM session_hosts WHERE token_hash=?',
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row:
            raise ValueError('host_unauthorized')
        return row['id']

    def hosts(self):
        with self.db() as db:
            return [{**dict(r), 'capabilities': json.loads(r['capabilities']),
                     'online': time.time()-r['heartbeat'] < 45}
                    for r in db.execute('SELECT id,name,capabilities,heartbeat FROM session_hosts')]

    def sessions(self):
        with self.db() as db:
            db.execute("UPDATE agent_sessions SET state='unknown',revision=revision+1 WHERE state IN ('starting','queued','stopping','closing') AND id IN (SELECT session_id FROM session_commands WHERE state='dispatched' AND dispatched<?)", (time.time()-60,))
            return [dict(r) for r in db.execute('SELECT * FROM agent_sessions ORDER BY created DESC LIMIT 200')]

    def command(self, data):
        if not isinstance(data.get('id'), str) or not 1 <= len(data['id']) <= 128:
            raise ValueError('invalid_command_id')
        action = data.get('action')
        if action not in ('create', 'prompt', 'interrupt', 'close', 'resume'):
            raise ValueError('unsupported_action')
        payload = encode(data)
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM session_commands WHERE id=?', (data['id'],)).fetchone()
            if old:
                if old['payload'] != payload:
                    raise ValueError('command_id_conflict')
                return {'session_id': old['session_id'], 'state': old['state']}
            if action == 'create':
                hid, provider, workspace = (data.get(k) for k in ('host_id', 'provider', 'workspace'))
                if not all(isinstance(v, str) for v in (hid, provider, workspace)):
                    raise ValueError('invalid_host_or_workspace')
                host = db.execute('SELECT * FROM session_hosts WHERE id=?', (hid,)).fetchone()
                if not host:
                    raise ValueError('host_not_found')
                caps = json.loads(host['capabilities'])
                if provider not in caps['providers'] or workspace not in caps['workspaces']:
                    raise ValueError('capability_rejected')
                title = data.get('title', '新会话')
                if not isinstance(title, str) or not 1 <= len(title) <= 200:
                    raise ValueError('invalid_title')
                sid = uuid.uuid4().hex
                db.execute('INSERT INTO agent_sessions(id,host_id,provider,workspace,title,state,revision,created) VALUES(?,?,?,?,?,?,0,?)',
                           (sid, hid, provider, workspace, title, 'starting', now))
            else:
                sid = data.get('session_id')
                if not isinstance(sid, str) or len(sid) > 128:
                    raise ValueError('invalid_session_id')
                session = db.execute('SELECT * FROM agent_sessions WHERE id=?', (sid,)).fetchone()
                if not session:
                    raise ValueError('session_not_found')
                if session['mode'] == 'observe':
                    raise ValueError('binding_is_read_only')
                if data.get('revision') != session['revision']:
                    raise ValueError('stale_session')
                if action == 'prompt':
                    if session['state'] != 'idle':
                        raise ValueError('session_not_idle')
                    message = data.get('text')
                    if not isinstance(message, str) or not message.strip() or len(message) > 50000:
                        raise ValueError('invalid_message')
                    # Slash commands can change the pi runtime independently of the broker.
                    if message.lstrip().startswith('/'):
                        raise ValueError('slash_commands_not_supported')
                    db.execute("UPDATE agent_sessions SET state='queued',revision=revision+1 WHERE id=?", (sid,))
                elif action == 'resume':
                    if session['state'] != 'closed':
                        raise ValueError('session_not_closed')
                    db.execute("UPDATE agent_sessions SET state='starting',revision=revision+1 WHERE id=?", (sid,))
                elif action == 'close':
                    if session['state'] != 'idle':
                        raise ValueError('session_not_idle')
                    db.execute("UPDATE agent_sessions SET state='closing',revision=revision+1 WHERE id=?", (sid,))
                else:
                    if session['state'] not in ('queued', 'running', 'unknown', 'stopping'):
                        raise ValueError('session_not_running')
                    # Cancel not-yet-delivered prompts before sending the stop command.
                    db.execute("UPDATE session_commands SET state='cancelled' WHERE session_id=? AND state='queued'", (sid,))
                    db.execute("UPDATE agent_sessions SET state='stopping',revision=revision+1 WHERE id=?", (sid,))
            db.execute('INSERT INTO session_commands(id,session_id,payload,state,created,expires) VALUES(?,?,?,?,?,?)',
                       (data['id'], sid, payload, 'queued', now, now+300))
            self._event(db, uuid.uuid4().hex, sid, 'command', {'action': action, 'text': data.get('text', '')})
            return {'session_id': sid, 'state': 'queued'}

    def claim(self, hid):
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE session_hosts SET heartbeat=? WHERE id=?', (now, hid))
            expired = db.execute("SELECT c.* FROM session_commands c JOIN agent_sessions s ON c.session_id=s.id WHERE s.host_id=? AND c.state='queued' AND c.expires<?", (hid, now)).fetchall()
            for c in expired:
                db.execute("UPDATE session_commands SET state='expired' WHERE id=?", (c['id'],))
                db.execute("UPDATE agent_sessions SET state='unknown',revision=revision+1 WHERE id=?", (c['session_id'],))
                self._event(db, uuid.uuid4().hex, c['session_id'], 'error', {'message': '指令已过期，没有自动执行。'})
            row = db.execute("SELECT c.*,s.provider,s.workspace,s.host_id FROM session_commands c JOIN agent_sessions s ON s.id=c.session_id WHERE s.host_id=? AND c.state='queued' ORDER BY c.created LIMIT 1", (hid,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE session_commands SET state='dispatched',dispatched=? WHERE id=?", (now,row['id']))
            return {**dict(row), 'payload': json.loads(row['payload'])}

    @staticmethod
    def _event(db, eid, sid, kind, payload):
        db.execute('INSERT INTO session_events(id,session_id,kind,payload,created) VALUES(?,?,?,?,?)',
                   (eid, sid, kind, encode(payload), time.time()))

    def report(self, hid, event):
        eid, sid, kind, payload = (event.get(k) for k in ('id', 'session_id', 'kind', 'payload'))
        if not isinstance(eid, str) or not 1 <= len(eid) <= 128 or not isinstance(sid, str) or len(sid) > 128 or not isinstance(payload, dict):
            raise ValueError('invalid_event')
        if kind not in ('text', 'tool', 'status', 'error', 'runtime', 'image', 'message'):
            raise ValueError('invalid_event_kind')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT host_id,mode FROM agent_sessions WHERE id=?', (sid,)).fetchone()
            if not row or row['host_id'] != hid:
                raise ValueError('session_owner_rejected')
            previous = db.execute('SELECT * FROM session_events WHERE id=?', (eid,)).fetchone()
            if previous:
                if previous['session_id'] != sid or previous['kind'] != kind or previous['payload'] != encode(payload):
                    raise ValueError('event_id_conflict')
                return
            if kind == 'status':
                state = payload.get('state')
                if row['mode'] == 'observe' and state not in ('observing', 'unknown'):
                    raise ValueError('binding_is_read_only')
                if state not in ('idle', 'running', 'unknown', 'failed', 'closed', 'observing'):
                    raise ValueError('invalid_session_state')
                # A late idle event must not clear a more recent queued/stop command.
                queued = db.execute("SELECT 1 FROM session_commands WHERE session_id=? AND state='queued'", (sid,)).fetchone()
                if not queued:
                    db.execute('UPDATE agent_sessions SET state=?,revision=revision+1 WHERE id=?', (state, sid))
                cid = payload.get('command_id')
                if cid:
                    db.execute("UPDATE session_commands SET state=? WHERE id=? AND session_id=? AND state='dispatched'", ('failed' if state in ('failed','unknown') else 'acknowledged', cid, sid))
            self._event(db, eid, sid, kind, payload)

    def replay(self, sid, after=0):
        if after < 0:
            raise ValueError('invalid_cursor')
        with self.db() as db:
            if not db.execute('SELECT 1 FROM agent_sessions WHERE id=?', (sid,)).fetchone():
                raise ValueError('session_not_found')
            return [{**dict(r), 'payload': json.loads(r['payload'])} for r in db.execute(
                'SELECT * FROM session_events WHERE session_id=? AND seq>? ORDER BY seq LIMIT 200', (sid, after))]
