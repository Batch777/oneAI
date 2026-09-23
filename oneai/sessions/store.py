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
                CREATE TABLE IF NOT EXISTS host_catalog(host_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS session_settings(session_id TEXT PRIMARY KEY,payload TEXT NOT NULL,removed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS session_metrics(session_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,updated REAL NOT NULL,PRIMARY KEY(session_id,kind));
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
            rows=[dict(r) for r in db.execute('SELECT s.* FROM agent_sessions s LEFT JOIN session_settings c ON c.session_id=s.id WHERE coalesce(c.removed,0)=0 ORDER BY created DESC LIMIT 200')]
            for row in rows:
                record=db.execute('SELECT payload FROM session_settings WHERE session_id=?',(row['id'],)).fetchone()
                row['settings']=json.loads(record[0]) if record else {}
                row['metrics']={m['kind']:{**json.loads(m['payload']),'reported_at':m['updated']} for m in db.execute('SELECT * FROM session_metrics WHERE session_id=?',(row['id'],))}
            return rows

    def command(self, data):
        if not isinstance(data.get('id'), str) or not 1 <= len(data['id']) <= 128:
            raise ValueError('invalid_command_id')
        action = data.get('action')
        if action not in ('create', 'prompt', 'interrupt', 'close', 'resume', 'configure', 'audit', 'remove'):
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
                settings=self._settings(db,hid,provider,workspace,data.get('settings',{}))
                sid = uuid.uuid4().hex
                db.execute('INSERT INTO session_settings VALUES(?,?,0)',(sid,encode(settings)))
                db.execute('INSERT INTO agent_sessions(id,host_id,provider,workspace,title,state,revision,created) VALUES(?,?,?,?,?,?,0,?)',
                           (sid, hid, provider, workspace, title, 'starting', now))
            else:
                sid = data.get('session_id')
                if not isinstance(sid, str) or len(sid) > 128:
                    raise ValueError('invalid_session_id')
                session = db.execute('SELECT * FROM agent_sessions WHERE id=?', (sid,)).fetchone()
                if not session:
                    raise ValueError('session_not_found')
                removed=db.execute('SELECT removed FROM session_settings WHERE session_id=?',(sid,)).fetchone()
                if removed and removed[0]:raise ValueError('session_removed')
                if session['mode'] == 'observe':
                    raise ValueError('binding_is_read_only')
                if data.get('revision') != session['revision']:
                    raise ValueError('stale_session')
                if action=='remove':
                    if session['state']!='closed':raise ValueError('close_session_before_removing')
                    db.execute('INSERT INTO session_settings VALUES(?,?,1) ON CONFLICT(session_id) DO UPDATE SET removed=1',(sid,'{}'))
                    db.execute("INSERT INTO session_commands(id,session_id,payload,state,created,expires) VALUES(?,?,?,'acknowledged',?,?)",(data['id'],sid,payload,now,now))
                    self._event(db,uuid.uuid4().hex,sid,'command',{'action':'remove'})
                    return {'session_id':sid,'state':'acknowledged'}
                if action in ('configure','audit'):
                    if session['state']!='idle':raise ValueError('session_not_idle')
                    if action=='configure':
                        old_settings=db.execute('SELECT payload FROM session_settings WHERE session_id=?',(sid,)).fetchone()
                        current=json.loads(old_settings[0]) if old_settings else {}
                        proposed={**current,**data.get('settings',{})}
                        if proposed.get('profile','general')!=current.get('profile','general'):raise ValueError('profile_requires_new_session')
                        settings=self._settings(db,session['host_id'],session['provider'],session['workspace'],proposed)
                        db.execute('INSERT INTO session_settings VALUES(?,?,0) ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload',(sid,encode(settings)))
                    elif not isinstance(data.get('purpose'),str) or not 1<=len(data['purpose'].strip())<=1000:raise ValueError('review_purpose_required')
                    db.execute("UPDATE agent_sessions SET state='starting',revision=revision+1 WHERE id=?",(sid,))
                elif action == 'prompt':
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
            settings=db.execute('SELECT payload FROM session_settings WHERE session_id=?',(row['session_id'],)).fetchone()
            return {**dict(row), 'payload': json.loads(row['payload']), 'settings':json.loads(settings[0]) if settings else {}}

    @staticmethod
    def _event(db, eid, sid, kind, payload):
        db.execute('INSERT INTO session_events(id,session_id,kind,payload,created) VALUES(?,?,?,?,?)',
                   (eid, sid, kind, encode(payload), time.time()))

    def report(self, hid, event):
        eid, sid, kind, payload = (event.get(k) for k in ('id', 'session_id', 'kind', 'payload'))
        if not isinstance(eid, str) or not 1 <= len(eid) <= 128 or not isinstance(sid, str) or len(sid) > 128 or not isinstance(payload, dict):
            raise ValueError('invalid_event')
        if kind not in ('text', 'tool', 'status', 'error', 'runtime', 'image', 'message', 'usage', 'model', 'quota', 'audit'):
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
            if kind in ('usage','model','quota','audit'):
                if len(encode(payload).encode())>60000:raise ValueError('metric_too_large')
                db.execute('INSERT INTO session_metrics VALUES(?,?,?,?) ON CONFLICT(session_id,kind) DO UPDATE SET payload=excluded.payload,updated=excluded.updated',(sid,kind,encode(payload),time.time()))
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

    def publish_catalog(self,hid,data):
        if not isinstance(data,dict) or set(data)!={'models','profiles'} or not isinstance(data['models'],list) or len(data['models'])>200 or not isinstance(data['profiles'],list) or len(data['profiles'])>20 or len(encode(data).encode())>100000:raise ValueError('invalid_catalog')
        with self.db() as db:
            host=db.execute('SELECT capabilities FROM session_hosts WHERE id=?',(hid,)).fetchone()
            if not host:raise ValueError('host_not_found')
            caps=json.loads(host[0]);keys=set()
            for m in data['models']:
                if not isinstance(m,dict) or m.get('provider') not in caps['providers'] or not isinstance(m.get('id'),str) or not 1<=len(m['id'])<=150 or not isinstance(m.get('name'),str) or len(m['name'])>200 or not isinstance(m.get('efforts'),list) or any(e not in ('off','none','minimal','low','medium','high','xhigh','max','ultra') for e in m['efforts']):raise ValueError('invalid_catalog_model')
                key=(m['provider'],m['id'])
                if key in keys:raise ValueError('duplicate_model')
                keys.add(key)
            from .profiles import PROFILES
            seen=set()
            for p in data['profiles']:
                if not isinstance(p,dict) or p.get('id') not in PROFILES or p['id'] in seen or not isinstance(p.get('workspaces'),list) or any(w not in caps['workspaces'] for w in p['workspaces']) or not isinstance(p.get('prompt'),str) or len(p['prompt'])>12000:raise ValueError('invalid_profile')
                seen.add(p['id'])
            db.execute('INSERT INTO host_catalog VALUES(?,?,?) ON CONFLICT(host_id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated',(hid,encode(data),time.time()))

    def catalogs(self):
        with self.db() as db:return [{'host_id':r['host_id'],**json.loads(r['payload']),'updated':r['updated']} for r in db.execute('SELECT * FROM host_catalog')]

    @staticmethod
    def _settings(db,hid,provider,workspace,value):
        if not isinstance(value,dict) or set(value)-{'model','effort','profile'}:raise ValueError('invalid_session_settings')
        if not value:return {}
        row=db.execute('SELECT * FROM host_catalog WHERE host_id=?',(hid,)).fetchone()
        if not row or time.time()-row['updated']>3600:raise ValueError('model_catalog_unavailable')
        catalog=json.loads(row['payload'])
        model=value.get('model');effort=value.get('effort');profile=value.get('profile','general')
        match=next((m for m in catalog['models'] if m['id']==model and m['provider']==provider),None)
        if not match:raise ValueError('model_not_available')
        if effort is not None and effort not in match['efforts']:raise ValueError('effort_not_supported')
        if not any(p['id']==profile and workspace in p['workspaces'] for p in catalog['profiles']):raise ValueError('profile_not_allowed')
        return {'model':model,'effort':effort,'profile':profile}
