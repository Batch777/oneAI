#!/bin/bash
# Only run after confirmation of public HTTPS access and certificate terms.
set -euo pipefail
[ "${ONEAI_ACCEPT_PUBLIC_HTTPS:-}" = yes ] || exit 2
[ -f /root/oneai-app-candidate/deploy/install-web.sh ] || exit 3
backup="/root/oneai-before-app-$(date +%Y%m%d%H%M%S)"
mkdir -m 700 "$backup"
cp -a /opt/oneai/oneai /opt/oneai/connectors /opt/oneai/deploy /opt/oneai/pyproject.toml "$backup/"
trap 'systemctl start oneai-worker.service' EXIT
systemctl stop oneai-worker.service
cp -a /root/oneai-app-candidate/oneai /root/oneai-app-candidate/connectors /root/oneai-app-candidate/deploy /root/oneai-app-candidate/pyproject.toml /opt/oneai/
systemctl start oneai-worker.service
ONEAI_ACCEPT_PUBLIC_HTTPS=yes bash /opt/oneai/deploy/install-web.sh
