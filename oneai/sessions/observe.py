"""Read an explicitly selected existing Codex thread without resuming it.

A sidecar's 'notLoaded' is not the desktop runtime's status. We report only
'observing', never infer whether the desktop is idle or safe to take over.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from .adapters import JsonLines


class Observer:
    def __init__(self, binary, cwd, binding, directory, emit):
        self.binding, self.emit = binding, emit
        self.announced = False
        self.rpc = JsonLines([binary, 'app-server', '--stdio'], cwd, lambda _: None)
        self.db = sqlite3.connect(Path(directory)/('observe-'+binding['session_id']+'.sqlite'))
        self.db.execute('CREATE TABLE IF NOT EXISTS observed (id TEXT PRIMARY KEY, version TEXT)')
        self.db.commit()
        try:
            self.rpc.request('initialize', {'clientInfo': {'name': 'oneai_observer', 'version': '0.1.0'}})
            self.rpc.send({'method': 'initialized'})
            account = self.rpc.request('account/read', {'refreshToken': False})
            if not account.get('account'):
                raise ValueError('Codex account is not signed in')
            thread = self.rpc.request('thread/read', {'threadId': binding['thread_id'], 'includeTurns': False})['thread']
            if Path(thread['cwd']).resolve() != Path(cwd).resolve():
                raise ValueError('Binding workspace does not match the existing thread')
            emit('runtime', {'provider': 'codex', 'remote_id': binding['thread_id'], 'mode': 'observe'})
        except Exception:
            self.close()
            raise

    def step(self):
        cursor, entries, found = None, [], False
        # Read bounded pages from the tail. Stop after overlapping the last
        # checkpoint; do not hydrate the full ultra-long conversation per poll.
        for _ in range(20):
            params = {'threadId': self.binding['thread_id'], 'limit': 25, 'sortDirection': 'desc'}
            if cursor:
                params['cursor'] = cursor
            result = self.rpc.request('thread/items/list', params)
            for row in result['data']:
                item = row['item']
                # Reasoning and raw tool arguments/results are not mirrored.
                if item.get('type') not in ('userMessage', 'agentMessage'):
                    continue
                if item['type'] == 'userMessage':
                    content = '\n'.join(c.get('text', '[附件暂不支持预览]') for c in item.get('content', []) if c.get('type') != 'reasoning')
                    role = 'user'
                else:
                    content, role = item.get('text', ''), 'assistant'
                key = row['turnId']+':'+item['id']
                version = hashlib.sha256(content.encode()).hexdigest()
                previous = self.db.execute('SELECT version FROM observed WHERE id=?', (key,)).fetchone()
                if previous and previous[0] == version:
                    found = True
                    break
                entries.append((key, version, {'item_id': key, 'role': role, 'text': content[:20000]}))
            cursor = result.get('nextCursor')
            if found or not cursor:
                break
        for key, version, payload in reversed(entries):
            self.emit('message', payload)
            self.db.execute('INSERT OR REPLACE INTO observed VALUES(?,?)', (key, version))
            self.db.commit()
        if entries or not self.announced:
            self.emit('status', {'state': 'observing', 'message': '同步桌面会话的已记录消息；只读，不接管运行。'})
            self.announced = True

    def close(self):
        self.rpc.close()
        self.db.close()
