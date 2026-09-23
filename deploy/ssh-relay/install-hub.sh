#!/bin/bash
# Run as root on the Hong Kong hub. Keys are supplied as files, never secrets.
set -euo pipefail
[ "$EUID" = 0 ] || { echo 'Run as root'; exit 1; }
[ "$#" = 2 ] || { echo 'Usage: install-hub.sh tunnel.pub jump.pub'; exit 2; }
for key in "$@"; do ssh-keygen -l -f "$key" >/dev/null; done
backup="/root/oneai-relay-backup-$(date +%s)"
mkdir -m 700 "$backup"
for user in oneai-tunnel oneai-jump; do
 if ! id "$user" >/dev/null 2>&1; then useradd --create-home --shell /bin/sh "$user"; fi
 install -d -m 700 -o "$user" -g "$user" "/home/$user/.ssh"
 if [ -f "/home/$user/.ssh/authorized_keys" ]; then cp -a "/home/$user/.ssh/authorized_keys" "$backup/$user.keys"; fi
done
printf 'restrict,port-forwarding,permitlisten="127.0.0.1:22390" %s\n' "$(cat "$1")" > /home/oneai-tunnel/.ssh/authorized_keys
printf 'restrict,port-forwarding,permitopen="127.0.0.1:22390" %s\n' "$(cat "$2")" > /home/oneai-jump/.ssh/authorized_keys
for user in oneai-tunnel oneai-jump; do chown "$user:$user" "/home/$user/.ssh/authorized_keys"; chmod 600 "/home/$user/.ssh/authorized_keys"; done
conf=/etc/ssh/sshd_config.d/00-oneai-relay.conf
[ ! -f "$conf" ] || cp -a "$conf" "$backup/relay.conf"
cat > "$conf" <<'EOF'
Match User oneai-tunnel
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding remote
    AllowStreamLocalForwarding no
    PermitListen 127.0.0.1:22390
    PermitOpen none
    GatewayPorts no
    PermitTTY no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    PermitUserRC no
    ForceCommand /bin/false
Match User oneai-jump
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    AllowStreamLocalForwarding no
    PermitOpen 127.0.0.1:22390
    PermitListen none
    GatewayPorts no
    PermitTTY no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    PermitUserRC no
    ForceCommand /bin/false
Match all
EOF
if ! /usr/sbin/sshd -t; then
 if [ -f "$backup/relay.conf" ]; then cp -a "$backup/relay.conf" "$conf"; else rm "$conf"; fi
 echo 'Invalid sshd config; active daemon unchanged' >&2; exit 1
fi
# Assert effective policy, including precedence against pre-existing config.
for user in oneai-tunnel oneai-jump; do
 /usr/sbin/sshd -T -C "user=$user,host=localhost,addr=127.0.0.1" > "$backup/$user.effective"
 grep -qx 'passwordauthentication no' "$backup/$user.effective"
 grep -qx 'forcecommand /bin/false' "$backup/$user.effective"
done
grep -qx 'allowtcpforwarding remote' "$backup/oneai-tunnel.effective"
grep -qx 'allowtcpforwarding local' "$backup/oneai-jump.effective"
systemctl reload ssh
cat /etc/ssh/ssh_host_ed25519_key.pub
printf 'RELAY_READY backup=%s\n' "$backup"
