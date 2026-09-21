#!/bin/sh
# Root-only. Creates a dedicated key-only, forced-command transport, no shell access.
set -eu
[ "$(id -u)" -eq 0 ] || exit 1
[ -f /opt/oneai/oneai/sync.py ] || exit 1
keyfile=${1:?public key file required}
grep -Eq '^ssh-ed25519 [A-Za-z0-9+/=]+( .*)?$' "$keyfile"
install -d -m 0755 /etc/oneai/ssh
{ printf 'restrict,command="/usr/local/libexec/oneai-sync" '; cat "$keyfile"; } > /etc/oneai/ssh/authorized_keys
chmod 0644 /etc/oneai/ssh/authorized_keys
install -d -m 0755 /usr/local/libexec
cat > /usr/local/libexec/oneai-sync <<'EOF'
#!/bin/sh
set -eu
umask 077
export ONEAI_STATE_PATH=/var/lib/oneai ONEAI_VAULT_PATH=/var/lib/oneai/vault
cd /opt/oneai
exec /opt/oneai/.venv/bin/python -m oneai.sync serve
EOF
chmod 0755 /usr/local/libexec/oneai-sync
cat > /etc/ssh/sshd_config.d/60-oneai-sync.conf <<'EOF'
Match User oneai
    AuthorizedKeysFile /etc/oneai/ssh/authorized_keys
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    ForceCommand /usr/local/libexec/oneai-sync
    DisableForwarding yes
    PermitTTY no
    PermitUserRC no
Match all
EOF
install -d -m 0755 /run/sshd
/usr/sbin/sshd -t
usermod --shell /bin/sh oneai
systemctl enable --now ssh
systemctl reload ssh
printf '%s\n' 'ONEAI_SYNC_TRANSPORT_READY'
