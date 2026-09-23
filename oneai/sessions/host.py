"""Outbound-only execution host. Local durable outbox survives network outages."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit
import uuid
from .adapters import ADAPTERS
from .store import encode


class Host:
    def __init__(self, config, directory):
        self.config, self.directory = config, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lockfile = (self.directory/'host.lock').open('a')
        fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory/'host.sqlite', check_same_thread=False)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS runtimes (id TEXT PRIMARY KEY, provider TEXT, workspace TEXT, remote_id TEXT, prompted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS received (id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT);
        ''')
        if 'prompted' not in {r[1] for r in self.db.execute('PRAGMA table_info(runtimes)')}:
            self.db.execute('ALTER TABLE runtimes ADD COLUMN prompted INTEGER NOT NULL DEFAULT 0')
        self.db.execute('CREATE TABLE IF NOT EXISTS session_baselines(id TEXT PRIMARY KEY,base_sha TEXT NOT NULL)')
        self.db.commit()
        self.runtimes = {}
        self.runtime_lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.futures = {}
        self.pending_starts = {}
        self.stopping = False
        self.metadata_at=0;self.metadata_future=None;self.metrics_at={}
        self.observers = {}
        self.observed_at = 0
        for sid, *_ in self.db.execute('SELECT * FROM runtimes').fetchall():
            # Session processes belong to the previous host instance. A stop from
            # a client reconnects persisted history; it never repeats a prompt.
            self.emit(sid, 'status', {'state': 'unknown', 'message': '执行服务已重启；请先停止并核对，再发送下一条消息。'})

    def request(self, route, data):
        req = urllib.request.Request(self.config['server'].rstrip('/')+'/api/agent-host/'+route,
                                     data=encode(data).encode(), method='POST',
                                     headers={'Authorization': 'Bearer '+self.config['token'],
                                              'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)

    def emit(self, sid, kind, payload):
        event = {'id': uuid.uuid4().hex, 'session_id': sid, 'kind': kind, 'payload': payload}
        with self.lock:
            self.db.execute('INSERT INTO outbox(payload) VALUES(?)', (encode(event),))
            self.db.commit()

    def flush(self):
        with self.lock:
            rows = self.db.execute('SELECT seq,payload FROM outbox ORDER BY seq LIMIT 100').fetchall()
        batch, size = [], 0
        for row in rows:
            size += len(row[1].encode())
            if size > 160000:
                break
            batch.append(row)
        if not batch:
            return
        self.request('events', {'events': [json.loads(row[1]) for row in batch]})
        with self.lock:
            self.db.executemany('DELETE FROM outbox WHERE seq=?', [(row[0],) for row in batch])
            self.db.commit()

    def runtime(self, command):
        with self.runtime_lock:
            return self._runtime(command)

    def _runtime(self, command):
        sid, provider, workspace = (command[k] for k in ('session_id', 'provider', 'workspace'))
        if sid in self.runtimes:
            return self.runtimes[sid]
        if provider not in self.config['providers'] or workspace not in self.config['workspaces']:
            raise ValueError('host_capability_rejected')
        cwd = Path(self.config['workspaces'][workspace]).resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError('workspace_not_directory')
        with self.lock:
            existing = self.db.execute('SELECT * FROM runtimes WHERE id=?', (sid,)).fetchone()
        if existing and (existing[1] != provider or existing[2] != workspace):
            raise ValueError('runtime_identity_conflict')
        if len(self.runtimes) >= self.config.get('max_sessions', 4):
            raise ValueError('执行主机已达到并发会话上限，请先关闭一个会话。')
        settings=command.get('settings') or {}
        profile=settings.get('profile','general')
        if profile!='general' and workspace not in self.config.get('profiles',{}).get(profile,[]):raise ValueError('host_profile_rejected')
        from .audit import baseline
        with self.lock:
            if not self.db.execute('SELECT 1 FROM session_baselines WHERE id=?',(sid,)).fetchone():
                try:sha=baseline(cwd)
                except Exception:sha=''
                self.db.execute('INSERT INTO session_baselines VALUES(?,?)',(sid,sha));self.db.commit()
        binary = self.config['binaries'][provider]
        runtime = ADAPTERS[provider](binary, cwd, self.directory/sid,
                                    lambda k, p: self.emit(sid, k, p), existing[3] if existing and existing[4] else None,
                                    **({'policy': self.config['runtime_policy']} if 'runtime_policy' in self.config else {}),
                                    **({'settings':settings} if settings else {}))
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO runtimes VALUES(?,?,?,?,?)',
                            (sid, provider, workspace, runtime.remote_id, existing[4] if existing else 0))
            self.db.commit()
        self.runtimes[sid] = runtime
        return runtime

    def execute(self, command):
        sid, cid = command['session_id'], command['id']
        try:
            action = command['payload']['action']
            if action == 'close':
                with self.runtime_lock:
                    runtime = self.runtimes.pop(sid, None)
                    if runtime:
                        runtime.close()
                self.emit(sid, 'status', {'state': 'closed', 'command_id': cid})
                return
            if action == 'interrupt':
                pending = self.pending_starts.get(sid)
                if pending and not pending.wait(50):
                    raise RuntimeError('prompt_dispatch_unconfirmed')
            runtime = self.runtime(command)
            if action == 'configure':
                runtime.configure(command['settings'])
                self.emit(sid,'status',{'state':'idle','command_id':cid})
            elif action == 'audit':
                from .audit import build
                with self.lock:base=self.db.execute('SELECT base_sha FROM session_baselines WHERE id=?',(sid,)).fetchone()[0]
                value=build(Path(self.config['workspaces'][command['workspace']]),base,command['payload']['purpose'])
                self.emit(sid,'audit',value);self.emit(sid,'status',{'state':'idle','command_id':cid})
            elif action in ('create', 'resume'):

                self.emit(sid, 'runtime', {'provider': command['provider'], 'remote_id': runtime.remote_id})
                self.emit(sid, 'status', {'state': 'idle', 'command_id': cid})
            elif action == 'prompt':
                with self.lock:
                    self.db.execute('UPDATE runtimes SET prompted=1 WHERE id=?', (sid,))
                    self.db.commit()
                # Acknowledgement precedes runtime deltas in the durable outbox.
                self.emit(sid, 'status', {'state': 'running', 'command_id': cid})
                runtime.prompt(command['payload']['text'], on_sent=self.pending_starts[sid].set)
            elif action == 'interrupt':
                runtime.interrupt()
                self.emit(sid, 'status', {'state': 'idle', 'command_id': cid})
        except Exception as error:
            self.emit(sid, 'error', {'message': str(error)[:1000]})
            self.emit(sid, 'status', {'state': 'unknown', 'command_id': cid})
            if command['payload']['action'] == 'prompt' and sid in self.pending_starts:
                self.pending_starts[sid].set()

    def observe(self):
        if time.monotonic()-self.observed_at < 5:
            return
        self.observed_at = time.monotonic()
        from .observe import Observer
        for binding in self.config.get('bindings', []):
            sid = binding['session_id']
            try:
                if sid not in self.observers:
                    self.observers[sid] = Observer(self.config['binaries']['codex'],
                        Path(self.config['workspaces'][binding['workspace']]), binding,
                        self.directory, lambda k, p, sid=sid: self.emit(sid, k, p))
                self.observers[sid].step()
            except Exception as error:
                self.emit(sid, 'error', {'message': '桌面会话同步失败：'+str(error)[:500]})

    def metadata(self):
        now=time.monotonic()
        if now-self.metadata_at>600:
            from .catalog import discover
            try:self.request('catalog',discover(self.config));self.metadata_at=now
            except Exception:print('Model catalog unavailable',flush=True);self.metadata_at=now-540
        for sid,runtime in list(self.runtimes.items()):
            if now-self.metrics_at.get(sid,0)<60 and not getattr(runtime,'usage_dirty',False):continue
            try:
                runtime.collect();runtime.usage_dirty=False;self.metrics_at[sid]=now
            except Exception:self.metrics_at[sid]=now;runtime.usage_dirty=False

    def step(self):
        if self.config.get('model_catalog') and (self.metadata_future is None or self.metadata_future.done()):
            self.metadata_future=self.pool.submit(self.metadata)
        self.observe()
        self.flush()
        command = self.request('poll', {})['command']
        if not command:
            return
        with self.lock:
            old = self.db.execute('SELECT 1 FROM received WHERE id=?', (command['id'],)).fetchone()
            if old:
                return  # Never retry a runtime write after ambiguous acknowledgement.
            self.db.execute('INSERT INTO received VALUES(?)', (command['id'],))
            self.db.commit()
        sid = command['session_id']
        action = command['payload']['action']
        if action != 'interrupt' and sid in self.futures and not self.futures[sid].done():
            self.emit(sid, 'status', {'state': 'unknown', 'command_id': command['id'], 'message': '执行主机忙，指令未重放。'})
            return
        if action == 'prompt':
            self.pending_starts[sid] = threading.Event()
        future = self.pool.submit(self.execute, command)
        if action != 'interrupt':
            self.futures[sid] = future

    def close(self):
        self.stopping = True
        for runtime in list(self.runtimes.values()):
            runtime.close()
        self.pool.shutdown(wait=True)
        for observer in self.observers.values():
            observer.close()
        with self.lock:
            self.db.close()
        self.lockfile.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='oneAI outbound session host')
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--state', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.config.stat().st_mode & 0o077:
        parser.error('host configuration contains a token; chmod 600 first')
    config = json.loads(args.config.read_text())
    url = urlsplit(config['server'])
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost')):
        parser.error('HTTPS required except on loopback')
    host = Host(config, args.state)
    try:
        while True:
            try:
                host.step()
            except (OSError, ValueError, urllib.error.URLError) as error:
                print('Relay unavailable; preserving local progress:', type(error).__name__, flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        host.close()


if __name__ == '__main__':
    main()
