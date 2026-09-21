#!/bin/bash
# Only run after confirmation of public HTTPS access and certificate terms.
set -euo pipefail
[ "${ONEAI_ACCEPT_PUBLIC_HTTPS:-}" = yes ] || exit 2
candidate="${ONEAI_RELEASE_CANDIDATE:-/root/oneai-public-candidate}"
[ -f "$candidate/deploy/install-web.sh" ] || exit 3
backup="/root/oneai-before-app-$(date +%Y%m%d%H%M%S)"
mkdir -m 700 "$backup"
for item in oneai connectors deploy pyproject.toml; do
  if [ -e "/opt/oneai/$item" ]; then cp -a "/opt/oneai/$item" "$backup/"; fi
done
trap 'systemctl start oneai-worker.service' EXIT
systemctl stop oneai-worker.service
cp -a "$candidate/oneai" "$candidate/connectors" "$candidate/deploy" "$candidate/pyproject.toml" /opt/oneai/
systemctl start oneai-worker.service
ONEAI_ACCEPT_PUBLIC_HTTPS=yes bash /opt/oneai/deploy/install-web.sh
