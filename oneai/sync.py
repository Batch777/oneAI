"""Bounded JSON RPC over a forced SSH command. Cloud owns tasks; notes use CAS.

No token caches, SQLite files, shell commands or PDF files cross this protocol.
Deletes are deliberately not propagated: removing a note locally restores it.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from .config import Config
from .tasks import Tasks
from .vault import atomic_write, resolve_note

MAX_NOTE = 2_000_000
MAX_RPC = 16_000_000
EXCLUDED = ('inbox/tasks/', 'inbox/cloud-tasks/', 'inbox/commands/', 'sync-conflicts/')


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def note_path(vault, name):
    if not isinstance(name, str) or len(name) > 1024:
        raise ValueError('Invalid note path')
    parts = PurePosixPath(name).parts
    if not parts or any(p.startswith('.') for p in parts) or '\\' in name or PurePosixPath(name).as_posix().startswith(EXCLUDED):
        raise ValueError('Reserved note path')
    path = resolve_note(vault, name)
    if any((vault / Path(*parts[:i])).is_symlink() for i in range(1, len(parts)+1)):
        raise ValueError('Symlinks cannot be synchronized')
    return path


def notes(vault):
    result = {}
    for p in sorted(vault.rglob('*.md')):
        name = p.relative_to(vault).as_posix()
        try:
            p = note_path(vault, name)
            if p.stat().st_size > MAX_NOTE: continue
            text = p.read_text(encoding='utf-8')
        except (ValueError, UnicodeError, FileNotFoundError):
            continue
        result[name] = sha(text)
    return result


def handle(cfg, request):
    if not isinstance(request, dict): raise ValueError('Expected object')
    action = request.get('action')
    if action == 'manifest': return {'notes': notes(cfg.vault_path)}
    if action in ('get', 'put'):
        path = note_path(cfg.vault_path, request.get('path'))
        old = path.read_text(encoding='utf-8') if path.exists() else None
        if old is not None and len(old.encode()) > MAX_NOTE: raise ValueError('Note too large')
        if action == 'get': return {'text': old, 'hash': sha(old) if old is not None else None}
        text = request.get('text')
        if not isinstance(text, str) or len(text.encode()) > MAX_NOTE: raise ValueError('Note too large or invalid')
        current = sha(old) if old is not None else None
        if current == sha(text): return {'hash': current}  # retry after lost acknowledgement
        if request.get('base') != current: return {'conflict': True, 'hash': current}
        if old is not None:
            atomic_write(cfg.state_path/'sync-history'/sha(old), old)
        atomic_write(path, text)
        return {'hash': sha(text)}
    if action == 'command':
        tasks = Tasks(cfg.state_path/'tasks.sqlite')
        try:
            command = request.get('command')
            if not isinstance(command, dict): raise ValueError('Expected command object')
            # Commands act on cloud's revision, never on a replicated database.
            tasks.command(command)
            return {'status': 'accepted', 'id': command.get('id')}
        finally: tasks.db.close()
    if action in ('tasks', 'task'):
        tasks = Tasks(cfg.state_path/'tasks.sqlite')
        try:
            if action == 'task': return tasks.get(request.get('task_id'))
            return {'tasks': [dict(r) for r in tasks.db.execute('SELECT id,title,status,revision,updated FROM tasks ORDER BY updated DESC LIMIT 1000')]}
        finally: tasks.db.close()
    if action == 'views':
        after=request.get('after','')
        import re
        if not isinstance(after,str) or (after and not re.fullmatch('[a-f0-9]{24}\\.md',after)):
            raise ValueError('Invalid cursor')
        tasks=Tasks(cfg.state_path/'tasks.sqlite')
        try:
            rows=tasks.db.execute('SELECT * FROM tasks WHERE id>? ORDER BY id LIMIT 51',(after[:-3] if after else '',)).fetchall()
            result={r['id']+'.md':tasks.view(r) for r in rows[:50]}
            return {'views':result,'next':rows[49]['id']+'.md' if len(rows)>50 else None}
        finally: tasks.db.close()
    if action == 'health': return {'protocol': 1, 'authority': 'cloud'}
    raise ValueError('Unsupported RPC action')


def serve(cfg):
    cfg.ensure_dirs()
    # One request per SSH process; shared lock serializes remote note updates.
    raw = sys.stdin.buffer.readline(MAX_RPC+1)
    if len(raw) > MAX_RPC: raise ValueError('RPC too large')
    with (cfg.state_path/'sync-server.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try: reply = handle(cfg, json.loads(raw))
        except (ValueError, TypeError, KeyError) as error: reply = {'error': str(error)}
    output = json.dumps(reply, ensure_ascii=False)
    if len(output.encode()) > MAX_RPC: output = json.dumps({'error':'RPC response too large'})
    print(output)


class SSHRemote:
    def __init__(self, settings):
        self.settings = settings
    def __call__(self, request):
        s = self.settings
        # Host key pinning and a separate restricted key; never fall back to passwords.
        args = ['ssh','-T','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15',
                '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2',
                '-o','UserKnownHostsFile='+s['known_hosts'],'-i',s['identity'],
                s['target'],'oneai-sync']
        body = json.dumps(request, ensure_ascii=False)+'\n'
        if len(body.encode()) > MAX_RPC: raise ValueError('RPC too large')
        reply = subprocess.run(args, input=body, text=True, capture_output=True, timeout=90)
        if reply.returncode: raise ConnectionError('Cloud SSH connection failed; no local state acknowledged')
        result = json.loads(reply.stdout)
        if 'error' in result: raise ValueError(result['error'])
        return result


def sync_once(cfg, remote):
    cfg.ensure_dirs()
    ledger_path = cfg.state_path/'sync-bases.json'
    bases = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    local = notes(cfg.vault_path)
    cloud = remote({'action':'manifest'})['notes']
    counts = {'uploaded':0,'downloaded':0,'conflicts':0,'commands':0}
    for name in sorted(local.keys() | cloud.keys()):
        path = note_path(cfg.vault_path, name)
        l, r, base = local.get(name), cloud.get(name), bases.get(name)
        if l == r:
            bases[name] = l
        elif l is not None and (r is None or r == base):
            text = path.read_text()
            if sha(text) != l: continue  # editor changed it during scan
            result = remote({'action':'put','path':name,'base':r,'text':text})
            if result.get('conflict'): counts['conflicts'] += 1; continue
            bases[name] = result['hash']; counts['uploaded'] += 1
        elif l is None or l == base:
            result = remote({'action':'get','path':name})
            if result['text'] is None: continue
            current = sha(path.read_text()) if path.exists() else None
            if current != l: continue
            if sha(result['text']) != result['hash']: raise ValueError('Invalid content hash')
            atomic_write(path, result['text']); bases[name] = result['hash']; counts['downloaded'] += 1
        else:
            result = remote({'action':'get','path':name})
            if result['text'] is not None:
                conflict = cfg.state_path/'sync-conflicts'/(sha(name)+'-'+result['hash']+'.md')
                atomic_write(conflict,result['text'])
                atomic_write(conflict.with_suffix('.json'), json.dumps({'path':name,'local':l,'remote':result['hash']}))
            counts['conflicts'] += 1
        # Crash before checkpoint is safe: equal hashes and CAS reconcile the replay.
        atomic_write(ledger_path,json.dumps(bases,ensure_ascii=False))
    command_dir = cfg.vault_path/'inbox/commands'
    if command_dir.exists() and not command_dir.is_symlink() and command_dir.resolve().is_relative_to(cfg.vault_path.resolve()):
        for path in sorted(command_dir.glob('*.json')):
            if path.is_symlink() or path.stat().st_size > 100_000: continue
            try:
                command = json.loads(path.read_text())
                result = remote({'action':'command','command':command})
                counts['commands'] += 1
            except (ValueError,TypeError) as error:
                result = {'status':'rejected','reason':str(error)}
            # Network failures propagate; never produce a false rejection/acceptance.
            receipt = path.with_suffix('.receipt')
            if not receipt.is_symlink(): atomic_write(receipt,json.dumps(result,ensure_ascii=False))
    after = ''
    while True:
        page = remote({'action':'views','after':after})
        for name, text in page['views'].items():
            if not isinstance(name,str) or len(name) != 27 or not name.endswith('.md') or any(c not in '0123456789abcdef' for c in name[:-3]):
                raise ValueError('Invalid task view name')
            dest = resolve_note(cfg.vault_path,'inbox/cloud-tasks/'+name)
            if not dest.exists() or dest.read_text() != text: atomic_write(dest,text)
        if not page.get('next'): break
        if page['next'] <= after: raise ValueError('Invalid page cursor')
        after = page['next']
    atomic_write(cfg.state_path/'sync-status.json',json.dumps(counts))
    return counts


def main():
    p=argparse.ArgumentParser(__doc__); p.add_argument('mode',choices=['serve','once']); args=p.parse_args()
    cfg=Config.load()
    if args.mode=='serve': serve(cfg)
    else:
        settings=json.loads((cfg.state_path/'cloud-sync.json').read_text())
        print(json.dumps(sync_once(cfg,SSHRemote(settings))))

if __name__=='__main__': main()
