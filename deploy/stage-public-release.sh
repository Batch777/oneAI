#!/bin/bash
set -euo pipefail
cd /root
printf '%s\n' '9be546f45c1dfd81cd5dc905d4cb8982b7762c4cc50b55937687573771658bd4  oneai-public-ui-v4.tar.gz' '82afbc9d0514888fe1bda7aa03cf3defc1aaf4d250d0fad0f1e0ceb9866d2aa5  oneai-public-sessions-v4.tar.gz' '2233d51998e42a3496f7709321eec30c056a17832c853571829d07421aa6c5c2  oneai-public-deploy-v4.tar.gz' '243a553b514e192233e1cf3b2a2b77b07aa7096b3c05e2f4bae0b2f3f21161ff  oneai-public-core-v4.tar.gz' '1dfbcfef2c8edcbcfec075d0d81a90ce0705e5663b42fdfb3f4a3a28f5c38436  oneai-public-connectors-v4.tar.gz' | sha256sum -c -
mkdir -p /root/oneai-public-candidate
cp -a /opt/oneai/oneai /opt/oneai/connectors /root/oneai-public-candidate/
tar -xzf oneai-public-ui-v4.tar.gz -C /root/oneai-public-candidate
tar -xzf oneai-public-sessions-v4.tar.gz -C /root/oneai-public-candidate
tar -xzf oneai-public-deploy-v4.tar.gz -C /root/oneai-public-candidate
tar -xzf oneai-public-core-v4.tar.gz -C /root/oneai-public-candidate
tar -xzf oneai-public-connectors-v4.tar.gz -C /root/oneai-public-candidate
/opt/oneai/.venv/bin/pip install --quiet 'fastapi==0.141.1' 'uvicorn==0.53.0' 'pytest>=8,<10' 'httpx>=0.27,<1'
cd /root/oneai-public-candidate
/opt/oneai/.venv/bin/python -m pytest tests/core/test_sessions.py tests/core/test_web.py tests/core/test_papers.py tests/core/test_indexer.py tests/core/test_sync.py -q
df -h /var/lib/oneai
ss -ltnH '( sport = :80 or sport = :443 or sport = :8765 )'
systemctl is-active oneai-worker.service oneai-outlook.timer
printf '%s\n' 'PUBLIC_RELEASE_STAGED_NOT_ACTIVATED'
