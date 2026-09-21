#!/bin/bash
# Run on the existing oneAI relay after updating application code.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run as root on the oneAI relay'; exit 1; }
test -f /etc/oneai/outlook.env
test -d /var/lib/oneai
cd /opt/oneai
.venv/bin/pip install 'pywebpush==2.5.0'
runuser -u oneai -- env ONEAI_STATE_PATH=/var/lib/oneai ONEAI_VAULT_PATH=/var/lib/oneai/vault .venv/bin/python - <<'PY'
from oneai.config import Config
from oneai.push import initialize
initialize(Config.load())
print('Web Push key ready; existing key preserved')
PY
install -m 644 deploy/systemd/oneai-push.service deploy/systemd/oneai-push.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now oneai-push.timer
systemctl start oneai-push.service
systemctl is-active oneai-push.timer
