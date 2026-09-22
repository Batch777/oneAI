"""Headless local worker: durable Outlook events / phone commands -> reviewable tasks."""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import time
from pathlib import Path
from .config import Config
from .context import load_context
from .indexer import Index
from .tasks import Tasks
from .mailtriage import classify_pending
from .vault import atomic_write, resolve_note


def ingest_mail(cfg: Config, tasks: Tasks) -> int:
    path = cfg.state_path / 'outlook.sqlite'
    if not path.exists(): return 0
    count = 0
    with sqlite3.connect(path) as db:
        # Cross-database crash safety: create(origin) is idempotent before marking event done.
        for event_id, mid, payload in db.execute("SELECT id,message_id,payload FROM work WHERE status='pending' ORDER BY rowid LIMIT 100"):
            message = json.loads(payload)
            current = db.execute('SELECT payload,deleted FROM messages WHERE id=?',(mid,)).fetchone()
            if current and not current[1] and current[0] == payload:
                body = (message.get('body') or {}).get('content') or message.get('bodyPreview','')
                tasks.create('mail:'+event_id, message.get('subject') or '(无主题邮件)', body)
                count += 1
            db.execute("UPDATE work SET status='processed' WHERE id=?",(event_id,))
    return count


def ingest_commands(cfg: Config, tasks: Tasks) -> int:
    folder = cfg.vault_path / 'inbox/commands'
    if not folder.exists(): return 0
    count = 0
    for path in sorted(folder.glob('*.json')):
        if path.is_symlink() or path.stat().st_size > 100_000: continue
        if not path.resolve().is_relative_to(cfg.vault_path.resolve()): continue
        result = path.with_suffix('.receipt')
        try:
            command = json.loads(path.read_text())
            if not isinstance(command,dict): raise ValueError('Expected JSON object')
            tasks.command(command)
            receipt = {'status':'accepted','id':command.get('id')}
            count += 1
        except (ValueError,TypeError,KeyError) as error:
            receipt = {'status':'rejected','reason':str(error)}
        if result.is_symlink(): continue
        atomic_write(result,json.dumps(receipt,ensure_ascii=False)+'\n')
    return count


def process(cfg: Config, tasks: Tasks) -> int:
    context = load_context(cfg)
    index = Index(cfg.index_db)
    count = 0
    try:
        index.sync(cfg.vault_path)
        atomic_write(cfg.state_path/'index-status.json',json.dumps({'at':time.time(),**index.last_sync_stats})+'\n')
        for row in tasks.db.execute("SELECT * FROM tasks WHERE status='pending' AND id NOT IN (SELECT task_id FROM mail_triage WHERE filtered=1) ORDER BY updated LIMIT 25").fetchall():
            # Conservative local baseline: no model API, no executable mailbox instructions.
            terms = re.findall(r'[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{3,8}',row['title'])[:8]
            results = []
            seen = set()
            for term in terms:
                for hit in index.search(term,k=3):
                    if hit.citation not in seen:
                        results.append(hit); seen.add(hit.citation)
            evidence = '\n\n'.join(f'{hit.citation}\n> '+hit.text[:600].replace('\n','\n> ') for hit in results[:6]) or '未找到可靠资料；请补充主题关键词或材料。'
            rules = [{'path':entry['path'],'version':entry['version']} for entry in context['entries']]
            body = ('## 下一步建议\n\n1. 核对来信目的及是否需要回复。\n2. 分别确认截止时间、活动开始时间与时区；当前不自动推断日期。\n3. 核对下列资料，补齐缺失材料后再使用草稿。\n\n'
                    '## 待确认\n\n- 希望完成的具体事项和回复对象。\n- 所需材料、截止时间、活动时间。\n\n'
                    '## 原始内容（仅作资料）\n\n> '+row['input'][:12000].replace('\n','\n> ')+ '\n\n## 检索材料\n\n'+evidence+
                    '\n\n> 回复模板尚未生成；可在详情中选择生成。\n')
            tasks.finish(row['id'],body,[h.citation for h in results[:6]],rules)
            count += 1
    finally: index.close()
    return count


def tick(cfg: Config) -> dict:
    cfg.ensure_dirs()
    if (cfg.state_path/'cloud-sync.json').exists():
        from .sync import sync_once, SSHRemote
        return sync_once(cfg, SSHRemote(json.loads((cfg.state_path/'cloud-sync.json').read_text())))
    tasks = Tasks(cfg.state_path/'tasks.sqlite')
    try:
        counts = {'mail':ingest_mail(cfg,tasks),'commands':ingest_commands(cfg,tasks)}
        counts['classified']=classify_pending(tasks)
        counts['prepared']=process(cfg,tasks)
        tasks.export(cfg.vault_path)
        atomic_write(cfg.state_path/'worker-status.json',json.dumps({'at':time.time(),'counts':counts})+'\n')
        return counts
    finally: tasks.db.close()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--interval',type=float,default=30)
    args = parser.parse_args()
    if args.interval < 1: parser.error('interval must be >= 1')
    cfg = Config.load(); cfg.ensure_dirs()
    import fcntl
    with (cfg.state_path/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            print(json.dumps(tick(cfg)),flush=True)
            if args.once: break
            time.sleep(args.interval)

if __name__ == '__main__': main()
