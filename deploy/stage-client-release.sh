#!/bin/bash
# Stage tested code and verify Linux compatibility; does not expose an HTTP service.
set -euo pipefail
cd /root
printf '%s\n' '756b151df2a9a3f9f586847411436eafc42e5e95608ab74242b65cd8eb681f9d  oneai-app-ui-v2.tar.gz' 'b0f1b0fb0f0882800586eb059b82627c436a5804021c3552884f2bc5d558b8cc  oneai-app-service-v2.tar.gz' | sha256sum -c -
mkdir -p /root/oneai-app-candidate
cp -a /opt/oneai/oneai /opt/oneai/connectors /root/oneai-app-candidate/
tar -xzf oneai-app-ui-v2.tar.gz -C /root/oneai-app-candidate
tar -xzf oneai-app-service-v2.tar.gz -C /root/oneai-app-candidate
cd /root/oneai-app-candidate
/opt/oneai/.venv/bin/python - <<'PY'
import curses, tempfile
from pathlib import Path
from oneai.config import Config
from oneai.tasks import Tasks
from oneai.sync import handle
from oneai.terminal import Client
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp); cfg=Config(root/'vault',root/'state',None); cfg.ensure_dirs()
 store=Tasks(cfg.state_path/'tasks.sqlite')
 for i in range(105): store.create(str(i),'Linux test','body')
 store.db.close(); after=''; sizes=[]
 while True:
  page=handle(cfg,{'action':'views','after':after}); sizes.append(len(page['views']))
  if page['next'] is None: break
  after=page['next']
 assert sizes==[50,50,5],sizes
 client=Client('https://47.82.117.21',root/'terminal')
 print('LINUX_IMPORT_AND_PAGINATION_OK',sizes)
PY
/opt/oneai/.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
root=Path('/var/lib/oneai/state')
for name,table in [('outlook.sqlite','messages'),('tasks.sqlite','tasks')]:
 with sqlite3.connect(f'file:{root/name}?mode=ro',uri=True) as db:
  print(name,db.execute(f'SELECT count(*) FROM {table}').fetchone()[0])
  if table=='tasks': print('task_status_counts',db.execute('SELECT status,count(*) FROM tasks GROUP BY status').fetchall())
PY
printf '%s\n' 'CLIENT_RELEASE_STAGED_NO_PUBLIC_SERVICE_STARTED'
