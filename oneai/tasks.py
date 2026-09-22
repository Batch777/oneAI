"""Durable local tasks. Approval records acknowledge a draft, never authorize sending."""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
import json
import os
import sqlite3
from pathlib import Path
from .vault import now_iso, write_note, resolve_note


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Tasks:
    def __init__(self, path: Path, read_only: bool = False):
        if read_only:
            self.db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=5)
            self.db.row_factory = sqlite3.Row
            return
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
        CREATE TABLE IF NOT EXISTS mail_triage(
          task_id TEXT PRIMARY KEY,category TEXT NOT NULL,confidence REAL NOT NULL,
          reason TEXT NOT NULL,source TEXT NOT NULL,filtered INTEGER NOT NULL,version TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS tasks_status_updated ON tasks(status,updated DESC,id DESC);
        CREATE INDEX IF NOT EXISTS tasks_updated ON tasks(updated DESC,id DESC);
        CREATE INDEX IF NOT EXISTS mail_triage_filtered ON mail_triage(filtered,task_id);
        CREATE TABLE IF NOT EXISTS reply_requests(task_id TEXT PRIMARY KEY,revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS mail_triage_evidence(task_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history(
          seq INTEGER PRIMARY KEY,task_id TEXT NOT NULL,event TEXT NOT NULL,
          detail TEXT NOT NULL,created TEXT NOT NULL);
        ''')

        if 'mail_received' not in {r[1] for r in self.db.execute('PRAGMA table_info(tasks)')}:
            self.db.execute('ALTER TABLE tasks ADD COLUMN mail_received TEXT')
        self.db.execute('CREATE INDEX IF NOT EXISTS tasks_status_display_date ON tasks(status,COALESCE(mail_received,updated) DESC,id DESC)')
        self.db.execute('CREATE INDEX IF NOT EXISTS tasks_display_date ON tasks(COALESCE(mail_received,updated) DESC,id DESC)')

    @staticmethod
    def received_date(value):
        try:
            date=datetime.fromisoformat(value.replace('Z','+00:00'))
            if date.tzinfo is None:return None
            return date.astimezone(timezone.utc).isoformat()
        except (ValueError,TypeError,AttributeError):return None

    def backfill_mail_dates(self, path):
        if not path.exists():return
        missing=self.db.execute("SELECT id,origin FROM tasks WHERE origin LIKE 'mail:%' AND mail_received IS NULL").fetchall()
        if not missing:return
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as source, self.db:
            for row in missing:
                event=source.execute('SELECT payload FROM work WHERE id=?',(row['origin'][5:],)).fetchone()
                if event:
                    try:date=self.received_date(json.loads(event[0]).get('receivedDateTime'))
                    except (ValueError,AttributeError):continue
                    if date:self.db.execute('UPDATE tasks SET mail_received=? WHERE id=?',(date,row['id']))

    def create(self, origin: str, title: str, body: str, received: str | None = None) -> str:
        task_id = digest(origin)[:24]
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO tasks(id,origin,title,input,updated) VALUES(?,?,?,?,?)', (task_id,origin,title,body,now_iso()))
            date=self.received_date(received) if origin.startswith('mail:') else None
            if date:self.db.execute('UPDATE tasks SET mail_received=? WHERE id=?',(date,task_id))
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
            elif action == 'generate_reply':
                task_id=command.get('task_id');row=self.get(task_id)
                if type(command.get('revision')) is not int or command['revision']!=row['revision']:
                    raise ValueError('Stale revision: refresh before generating a reply')
                if row['status'] not in ('needs_review','reviewed'):
                    raise ValueError('Task is not reviewable')
                if self.db.execute('SELECT 1 FROM reply_requests WHERE task_id=?',(task_id,)).fetchone():
                    raise ValueError('Reply already generated; edit the existing draft')
                base=row['result']
                if row['revision']==1:base=base.split('\n\n## 回复草稿模板')[0]
                reply='\n\n## 回复草稿\n\n您好，已收到您的来信。\n\n[请补充已核实的信息、答复和待确认问题。]\n\n谢谢。\n\n> 这是待编辑模板，未调用模型，尚未发送。\n'
                self.db.execute("UPDATE tasks SET result=?,revision=revision+1,approved_revision=NULL,status='needs_review',updated=? WHERE id=?",(base+reply,now_iso(),task_id))
                self.db.execute('INSERT INTO reply_requests VALUES(?,?)',(task_id,row['revision']+1))
            elif action == 'restore_mail':
                task_id=command.get('task_id'); self.get(task_id)
                self.db.execute("UPDATE mail_triage SET filtered=0,source='user',reason='用户恢复到任务列表' WHERE task_id=?",(task_id,))
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
            if action in ('generate_reply','revise','approve','complete'):
                self.db.execute("UPDATE mail_triage SET filtered=0 WHERE task_id=?",(task_id,))
            self.db.execute('INSERT INTO receipts VALUES(?,?)',(cid,payload))
            self.db.execute('INSERT INTO history(task_id,event,detail,created) VALUES(?,?,?,?)',(task_id,action,payload,now_iso()))

    @staticmethod
    def view(row):
        import frontmatter
        metadata={key:row[key] for key in ('id','title','status','revision','approved_revision','updated')}
        metadata['task_id']=metadata.pop('id')
        body=f'# {row["title"]}\n\n{row["result"] or "等待后台处理"}\n\n## 依据\n\n'+ '\n'.join('- '+s for s in json.loads(row['sources'])) + '\n\n> 此文件是进度视图。编辑草稿请提交 revise 指令；reviewed 不代表已发送。'
        return frontmatter.dumps(frontmatter.Post(body,**metadata))+'\n'

    def export(self, vault: Path):
        from .vault import atomic_write
        for row in self.db.execute('SELECT * FROM tasks ORDER BY updated'):
            atomic_write(resolve_note(vault,f'inbox/tasks/{row["id"]}.md'),self.view(row))


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
