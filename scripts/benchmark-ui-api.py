"""Synthetic local API benchmark; no real mailbox, credentials or network access.

Run from repo root: .venv/bin/python scripts/benchmark-ui-api.py
Numbers include TestClient overhead, not TLS/network/mobile rendering.
"""
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from oneai.config import Config
from oneai.tasks import Tasks
from oneai.web import auth
from oneai.web.app import create_app


def measure(call, samples=25):
    call()  # warm up
    times=[]
    for _ in range(samples):
        start=time.perf_counter(); call(); times.append((time.perf_counter()-start)*1000)
    return {'p50_ms':round(statistics.median(times),2),'p95_ms':round(sorted(times)[int(.95*(samples-1))],2),'samples':samples}


def main():
    with tempfile.TemporaryDirectory(prefix='oneai-ui-benchmark-') as directory:
        root=Path(directory);cfg=Config(root/'vault',root/'state',None)
        app=create_app(cfg,origin='https://testserver');s=Tasks(cfg.state_path/'tasks.sqlite')
        with s.db:
            s.db.executemany('INSERT INTO tasks(id,origin,title,input,status,updated,mail_received) VALUES(?,?,?,?,?,?,?)',
                [(str(i),f'mail:{i}',f'Synthetic message {i}','Synthetic body '*100,'needs_review' if i%5 else 'completed',f'2026-09-{i%28+1:02d}T12:00:00+00:00',f'2026-09-{i%28+1:02d}T12:00:00+00:00') for i in range(10000)])
            s.db.executemany('INSERT INTO mail_triage VALUES(?,?,?,?,?,?,?)',[(str(i),'promotion',.99,'synthetic','test',1,'v1') for i in range(0,10000,7)])
        result={'dataset_rows':10000,'environment':'local synthetic TestClient, no network, warm sequential requests','sqlite':sqlite3.sqlite_version,'journal_mode':s.db.execute('PRAGMA journal_mode').fetchone()[0]}
        where=" FROM tasks WHERE status='needs_review' AND id NOT IN (SELECT task_id FROM mail_triage WHERE filtered=1)"
        for label,query in [('count','SELECT count(*)'+where),('page50','SELECT id,title,status,revision,updated,mail_received'+where+' ORDER BY COALESCE(mail_received,updated) DESC,id DESC LIMIT 50'),('deep_offset','SELECT id,title'+where+' ORDER BY COALESCE(mail_received,updated) DESC,id DESC LIMIT 50 OFFSET 5000')]:
            result[label]=measure(lambda:s.db.execute(query).fetchall())
        s.db.close()
        with TestClient(app,base_url='https://testserver') as client:
            client.headers['origin']='https://testserver'
            assert client.post('/api/login',json={'code':auth.pair(cfg)}).status_code==200
            def get(path):
                response=client.get(path);assert response.status_code==200;return response
            for label,path in [('list_api','/api/tasks?status=needs_review'),('status_api','/api/status'),('empty_alerts_api','/api/mail/alerts')]:
                result[label]=measure(lambda:get(path))
        print(json.dumps(result,indent=2))


if __name__=='__main__':main()
