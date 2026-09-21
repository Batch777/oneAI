#!/bin/bash
# Requires explicit acceptance of Let's Encrypt terms and public authenticated app exposure.
set -euo pipefail
[ "$(id -u)" = 0 ] || exit 1
[ "${ONEAI_ACCEPT_PUBLIC_HTTPS:-}" = yes ] || { echo 'Set ONEAI_ACCEPT_PUBLIC_HTTPS=yes after owner confirmation'; exit 2; }
cd /opt/oneai
# Refuse to replace unrelated services/configuration.
if [ ! -f /etc/nginx/sites-available/oneai ]; then
  if ss -ltnH '( sport = :80 or sport = :443 )' | read -r _; then echo 'Existing web listener: inspect before deployment'; exit 3; fi
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx python3-venv
/opt/oneai/.venv/bin/pip install 'fastapi==0.141.1' 'uvicorn==0.53.0'
python3 -m venv /opt/oneai-certbot
/opt/oneai-certbot/bin/pip install 'certbot>=5.4,<6'
install -d -m 755 /var/www/oneai-acme
# Only the package's default welcome site is disabled; custom sites are untouched.
if [ -L /etc/nginx/sites-enabled/default ]; then unlink /etc/nginx/sites-enabled/default; fi
cat > /etc/nginx/sites-available/oneai <<'NGINX'
server {
  listen 80;
  server_name 47.82.117.21;
  location /.well-known/acme-challenge/ { root /var/www/oneai-acme; }
  location / { return 404; }
}
NGINX
ln -sfn /etc/nginx/sites-available/oneai /etc/nginx/sites-enabled/oneai
nginx -t
systemctl enable --now nginx
systemctl reload nginx
/opt/oneai-certbot/bin/certbot certonly --non-interactive --agree-tos --register-unsafely-without-email --preferred-profile shortlived --webroot --webroot-path /var/www/oneai-acme --ip-address 47.82.117.21 --cert-name oneai-ip --deploy-hook 'systemctl reload nginx'
cat > /etc/nginx/sites-available/oneai <<'NGINX'
server {
  listen 80;
  server_name 47.82.117.21;
  location /.well-known/acme-challenge/ { root /var/www/oneai-acme; }
  location / { return 308 https://47.82.117.21$request_uri; }
}
server {
  listen 443 ssl;
  server_name 47.82.117.21;
  ssl_certificate /etc/letsencrypt/live/oneai-ip/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/oneai-ip/privkey.pem;
  ssl_protocols TLSv1.2 TLSv1.3;
  client_max_body_size 200k;
  add_header Strict-Transport-Security "max-age=86400" always;
  location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 30s;
  }
}
NGINX
install -m 644 deploy/systemd/oneai-web.service /etc/systemd/system/oneai-web.service
cat > /etc/systemd/system/oneai-cert-renew.service <<'UNIT'
[Unit]
Description=Renew oneAI short-lived IP certificate
[Service]
Type=oneshot
ExecStart=/opt/oneai-certbot/bin/certbot renew --quiet --deploy-hook "systemctl reload nginx"
UNIT
cat > /etc/systemd/system/oneai-cert-renew.timer <<'UNIT'
[Unit]
Description=Check oneAI certificate twice daily
[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=1800
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now oneai-web.service oneai-cert-renew.timer
nginx -t
systemctl reload nginx
curl --fail --silent https://47.82.117.21/ > /dev/null
test "$(curl --silent -o /dev/null -w '%{http_code}' https://47.82.117.21/api/tasks)" = 401
systemctl is-active oneai-web.service oneai-worker.service oneai-outlook.timer oneai-cert-renew.timer
printf '%s\n' 'HTTPS_AND_AUTH_GUARD_OK'
