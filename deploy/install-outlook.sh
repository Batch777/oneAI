#!/bin/sh
# Run as root from a trusted checkout/archive on Ubuntu 24.04.
# Does not authorize a mailbox or start the timer before the first successful sync.
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run as root' >&2; exit 1; }
[ -f pyproject.toml ] && [ -f connectors/outlook/connector.py ] || exit 1
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv ca-certificates
getent passwd oneai >/dev/null || useradd --system --home-dir /var/lib/oneai --shell /usr/sbin/nologin oneai
install -d -m 0700 -o oneai -g oneai /var/lib/oneai
install -d -m 0750 -o root -g oneai /etc/oneai
install -d -m 0755 /opt/oneai
cp -R oneai connectors pyproject.toml /opt/oneai/
python3 -m venv /opt/oneai/.venv
/opt/oneai/.venv/bin/python -m pip install '/opt/oneai[outlook]'
install -m 0644 deploy/systemd/oneai-outlook.service deploy/systemd/oneai-outlook.timer deploy/systemd/oneai-worker.service /etc/systemd/system/
if [ ! -f /etc/oneai/outlook.env ]; then
    install -m 0640 -o root -g oneai deploy/outlook.env.example /etc/oneai/outlook.env
fi
systemctl daemon-reload
printf '%s\n' 'Installed. Set client ID, run auth and sync as oneai, then enable oneai-outlook.timer.'
