"""Durable local tasks. Approval records acknowledge a draft, never authorize sending."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from .vault import now_iso, write_note, resolve_note


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Tasks:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        os.chmod(path, 0o600)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY, origin TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
          input TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          result TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL DEFAULT '[]',
          rules TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 0,
          approved_revision INTEGER, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history(
          seq INTEGER PRIMARY KEY,task_id TEXT NOT NULL,event TEXT NOT NULL,
          detail TEXT NOT NULL,created TEXT NOT NULL);
        ''')

    def create(self, origin: str, title: str, body: str) -> str:
        task_id = digest(origin)[:24]
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO tasks(id,origin,title,input,updated) VALUES(?,?,?,?,?)', (task_id,origin,title,body,now_iso()))
        return task_id

    def get(self, task_id: str):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        if row is None: raise ValueError('Unknown task')
        return dict(row)

    def finish(self, task_id: str, result: str, sources: list, rules: list):
        with self.db:
            changed = self.db.execute("UPDATE tasks SET result=?,sources=?,rules=?,status='needs_review',revision=revision+1,approved_revision=NULL,updated=? WHERE id=? AND status='pending'", (result,json.dumps(sources,ensure_ascii=False),json.dumps(rules,ensure_ascii=False),now_iso(),task_id)).rowcount
            if changed:
                self.db.execute('INSERT INTO history(task_id,event,detail,created) VALUES(?,?,?,?)', (task_id,'prepared',json.dumps({'result':result,'sources':sources,'rules':rules},ensure_ascii=False),now_iso()))

    def command(self, command: dict):
        cid = command.get('id')
        if not isinstance(cid,str) or not 1 <= len(cid) <= 128:
            raise ValueError('A stable command id is required')
        payload = json.dumps(command,sort_keys=True,ensure_ascii=False)
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            previous = self.db.execute('SELECT payload FROM receipts WHERE id=?',(cid,)).fetchone()
            if previous:
                if previous[0] != payload: raise ValueError('Command id reused with different content')
                return
            action = command.get('action')
            if action == 'create':
                title, body = command.get('title'), command.get('body')
                if not isinstance(title,str) or not isinstance(body,str) or not body.strip(): raise ValueError('title and body required')
                task_id = digest('phone:'+cid)[:24]
                self.db.execute('INSERT INTO tasks(id,origin,title,input,updated) VALUES(?,?,?,?,?)',(task_id,'phone:'+cid,title,body,now_iso()))
            elif action in ('revise','approve','complete'):
                task_id = command.get('task_id')
                row = self.get(task_id)
                if type(command.get('revision')) is not int or command['revision'] != row['revision']:
                    raise ValueError('Stale revision: refresh the task before acting')
                if row['status'] not in ('needs_review','reviewed'): raise ValueError('Task is not reviewable')
                if action == 'revise':
                    body = command.get('body')
                    if not isinstance(body,str) or not body.strip(): raise ValueError('Non-empty draft required')
                    self.db.execute("UPDATE tasks SET result=?,revision=revision+1,approved_revision=NULL,status='needs_review',updated=? WHERE id=?",(body,now_iso(),task_id))
                elif action == 'approve':
                    self.db.execute("UPDATE tasks SET status='reviewed',approved_revision=revision,updated=? WHERE id=?",(now_iso(),task_id))
                else:
                    self.db.execute("UPDATE tasks SET status='completed',updated=? WHERE id=?",(now_iso(),task_id))
            else: raise ValueError('Unsupported action; no send or shell action exists')
            self.db.execute('INSERT INTO receipts VALUES(?,?)',(cid,payload))
            self.db.execute('INSERT INTO history(task_id,event,detail,created) VALUES(?,?,?,?)',(task_id,action,payload,now_iso()))

    def export(self, vault: Path):
        for row in self.db.execute('SELECT * FROM tasks ORDER BY updated'):
            # Views are regenerated; phone edits go through revision-checked command files.
            write_note(vault,f'inbox/tasks/{row["id"]}.md',{'task_id':row['id'],'title':row['title'],'status':row['status'],'revision':row['revision'],'approved_revision':row['approved_revision'],'updated':row['updated']},
                f'# {row["title"]}\n\n{row["result"] or "等待后台处理"}\n\n## 依据\n\n'+ '\n'.join('- '+s for s in json.loads(row['sources'])) + '\n\n> 此文件是进度视图。编辑草稿请提交 revise 指令；reviewed 不代表已发送。')


def main(argv=None):
    import argparse
    import sys
    import uuid
    from .config import Config
    parser = argparse.ArgumentParser(description='Create and review durable oneAI tasks; never sends mail')
    sub = parser.add_subparsers(dest='action',required=True)
    create = sub.add_parser('create'); create.add_argument('title'); create.add_argument('--body',required=True)
    sub.add_parser('list')
    show = sub.add_parser('show'); show.add_argument('task_id')
    for action in ('revise','approve','complete'):
        child = sub.add_parser(action); child.add_argument('task_id'); child.add_argument('--revision',type=int,required=True)
        if action == 'revise': child.add_argument('--body',required=True)
    args = parser.parse_args(argv)
    cfg = Config.load(); cfg.ensure_dirs()
    cloud_config = cfg.state_path/'cloud-sync.json'
    if cloud_config.exists():
        from .sync import SSHRemote
        remote = SSHRemote(json.loads(cloud_config.read_text()))
        try:
            if args.action == 'list': value = remote({'action':'tasks'})['tasks']
            elif args.action == 'show': value = remote({'action':'task','task_id':args.task_id})
            else:
                command = {**vars(args),'id':uuid.uuid4().hex}
                value = remote({'action':'command','command':command})
                value['task_id'] = digest('phone:'+command['id'])[:24] if args.action=='create' else args.task_id
            print(json.dumps(value,ensure_ascii=False,indent=2))
        except (ValueError,ConnectionError) as error: parser.exit(2,str(error)+'\n')
        return
    store = Tasks(cfg.state_path/'tasks.sqlite')
    try:
        if args.action == 'list':
            value = [dict(row) for row in store.db.execute('SELECT id,title,status,revision,updated FROM tasks ORDER BY updated DESC')]
        elif args.action == 'show': value = store.get(args.task_id)
        else:
            command = {**vars(args),'id':uuid.uuid4().hex}
            store.command(command)
            value = {'accepted':True,'task_id':digest('phone:'+command['id'])[:24] if args.action=='create' else args.task_id}
        print(json.dumps(value,ensure_ascii=False,indent=2))
    except ValueError as error: parser.exit(2,str(error)+'\n')
    finally: store.db.close()

if __name__ == '__main__': main()
