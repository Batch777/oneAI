#!/bin/bash
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run as root on the oneAI relay'; exit 1; }
cd /opt/oneai
test -f /etc/oneai/outlook.env
install -m 644 deploy/systemd/oneai-triage.service deploy/systemd/oneai-triage.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now oneai-triage.timer
systemctl start oneai-triage.service
systemctl is-active oneai-triage.timer
