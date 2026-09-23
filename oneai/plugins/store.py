"""Relay-only plugin index and at-most-once invocation journal. No package bytes."""
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
from .package import manifest,validate_input,encode


class Store:
    def __init__(self,path):
        self.path=path;path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS plugins(key TEXT PRIMARY KEY,host_id TEXT NOT NULL,plugin_id TEXT NOT NULL,manifest TEXT NOT NULL,digest TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 0,seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS plugin_runs(id TEXT PRIMARY KEY,plugin_key TEXT NOT NULL,host_id TEXT NOT NULL,digest TEXT NOT NULL,workspace TEXT NOT NULL,input TEXT NOT NULL,status TEXT NOT NULL,result TEXT,created REAL NOT NULL,deadline REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS plugin_dispatch ON plugin_runs(host_id,status,created);
            ''')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def publish(self,hid,items):
        if not isinstance(items,list) or len(items)>50:raise ValueError('invalid_plugin_inventory')
        checked=[]
        for item in items:
            m=manifest(item['manifest']);d=item['digest']
            if not isinstance(d,str) or not re.fullmatch(r'[a-f0-9]{64}',d):raise ValueError('invalid_digest')
            key=hashlib.sha256((hid+'/'+m['id']).encode()).hexdigest()[:32]
            if any(x[0]==key for x in checked):raise ValueError('duplicate_plugin')
            checked.append((key,m,d))
        now=time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for key,m,d in checked:
                old=db.execute('SELECT * FROM plugins WHERE key=?',(key,)).fetchone()
                if old and (old['digest']!=d or old['manifest']!=encode(m)):
                    db.execute("UPDATE plugin_runs SET status='cancelled' WHERE plugin_key=? AND status='queued'",(key,))
                    db.execute('UPDATE plugins SET digest=?,manifest=?,enabled=0,revision=revision+1,seen=? WHERE key=?',(d,encode(m),now,key))
                elif old:db.execute('UPDATE plugins SET seen=? WHERE key=?',(now,key))
                else:db.execute('INSERT INTO plugins VALUES(?,?,?,?,?,0,0,?)',(key,hid,m['id'],encode(m),d,now))
            keys={x[0] for x in checked}
            for row in db.execute('SELECT key FROM plugins WHERE host_id=?',(hid,)).fetchall():
                if row['key'] not in keys:
                    db.execute('UPDATE plugins SET enabled=0,seen=0,revision=revision+1 WHERE key=? AND seen!=0',(row['key'],))
                    db.execute("UPDATE plugin_runs SET status='cancelled' WHERE plugin_key=? AND status='queued'",(row['key'],))
    def list(self):
        with self.db() as db:
            self._expire(db)
            return [{**dict(r),'manifest':json.loads(r['manifest']),'online':time.time()-r['seen']<120} for r in db.execute('SELECT * FROM plugins ORDER BY plugin_id')]
    def toggle(self,key,revision,enabled):
        if type(enabled) is not bool or type(revision) is not int:raise ValueError('invalid_plugin_toggle')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            r=db.execute('SELECT * FROM plugins WHERE key=?',(key,)).fetchone()
            if not r or r['revision']!=revision:raise ValueError('stale_plugin')
            if enabled and time.time()-r['seen']>=120:raise ValueError('plugin_offline')
            db.execute('UPDATE plugins SET enabled=?,revision=revision+1 WHERE key=?',(enabled,key))
            if not enabled:db.execute("UPDATE plugin_runs SET status='cancelled' WHERE plugin_key=? AND status='queued'",(key,))
    def invoke(self,data,workspaces):
        rid=data.get('id');key=data.get('plugin_key');workspace=data.get('workspace');value=data.get('input',{})
        if not isinstance(rid,str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,100}',rid):raise ValueError('invalid_invocation_id')
        if not isinstance(key,str) or not isinstance(workspace,str):raise ValueError('invalid_plugin_target')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');self._expire(db)
            p=db.execute('SELECT * FROM plugins WHERE key=?',(key,)).fetchone()
            if not p:raise ValueError('plugin_not_found')
            old=db.execute('SELECT * FROM plugin_runs WHERE id=?',(rid,)).fetchone()
            if old:
                if (old['plugin_key'],old['workspace'],old['input'])!=(key,workspace,encode(value)):raise ValueError('invocation_id_conflict')
                return self._run(old)
            m=json.loads(p['manifest']);validate_input(m['input_schema'],value)
            if type(data.get('revision')) is not int:raise ValueError('stale_plugin')
            if workspace not in workspaces.get(p['host_id'],[]):raise ValueError('workspace_not_allowed')
            if not p['enabled'] or time.time()-p['seen']>=120:raise ValueError('plugin_unavailable')
            if data.get('revision')!=p['revision']:raise ValueError('stale_plugin')
            if db.execute("SELECT count(*) FROM plugin_runs WHERE host_id=? AND status IN ('queued','running')",(p['host_id'],)).fetchone()[0]>=20:raise ValueError('plugin_queue_full')
            now=time.time();db.execute('INSERT INTO plugin_runs VALUES(?,?,?,?,?,?,?,NULL,?,?)',(rid,key,p['host_id'],p['digest'],workspace,encode(value),'queued',now,now+300))
            return self._run(db.execute('SELECT * FROM plugin_runs WHERE id=?',(rid,)).fetchone())
    @staticmethod
    def _expire(db):
        now=time.time()
        db.execute("UPDATE plugin_runs SET status='expired' WHERE status='queued' AND deadline<?",(now,))
        db.execute("UPDATE plugin_runs SET status='unknown' WHERE status='running' AND deadline<?",(now,))
    def claim(self,hid):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');self._expire(db)
            r=db.execute("SELECT r.*,p.manifest FROM plugin_runs r JOIN plugins p ON p.key=r.plugin_key WHERE r.host_id=? AND r.status='queued' AND p.enabled=1 AND p.digest=r.digest ORDER BY r.created LIMIT 1",(hid,)).fetchone()
            if not r:return None
            deadline=time.time()+json.loads(r['manifest'])['timeout_seconds']+60
            db.execute("UPDATE plugin_runs SET status='running',deadline=? WHERE id=?",(deadline,r['id']))
            return {**dict(r),'input':json.loads(r['input']),'manifest':json.loads(r['manifest'])}
    def result(self,hid,data):
        status=data.get('status');output=data.get('result')
        if status not in ('completed','failed','unknown') or not isinstance(output,dict) or len(encode(output).encode())>64000:raise ValueError('invalid_plugin_result')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            r=db.execute('SELECT * FROM plugin_runs WHERE id=?',(data.get('id'),)).fetchone()
            if not r or r['host_id']!=hid:raise ValueError('plugin_run_owner')
            if r['result'] is not None:
                if r['result']!=encode(output) or r['status']!=status:raise ValueError('plugin_result_conflict')
                return
            if r['status'] not in ('running','unknown'):raise ValueError('plugin_not_dispatched')
            db.execute('UPDATE plugin_runs SET status=?,result=? WHERE id=?',(status,encode(output),r['id']))
    @staticmethod
    def _run(r):
        return {**dict(r),'input':json.loads(r['input']),'result':json.loads(r['result']) if r['result'] else None}
    def runs(self):
        with self.db() as db:
            self._expire(db)
            return [self._run(r) for r in db.execute('SELECT * FROM plugin_runs ORDER BY created DESC LIMIT 30')]
