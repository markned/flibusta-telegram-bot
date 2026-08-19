#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-books.technique.ink}"
EMAIL="${2:-}"
UPSTREAM="127.0.0.1:8081"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash $0 ${DOMAIN} your-email@example.com" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx

cat >"/etc/nginx/sites-available/${DOMAIN}" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    client_max_body_size 64k;

    location / {
        proxy_pass http://${UPSTREAM};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 70s;
    }
}
NGINX

ln -sfn "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

CERTBOT_ARGS=(--nginx -d "${DOMAIN}" --non-interactive --agree-tos --redirect)
if [ -n "${EMAIL}" ]; then
  CERTBOT_ARGS+=(--email "${EMAIL}")
else
  CERTBOT_ARGS+=(--register-unsafely-without-email)
fi
certbot "${CERTBOT_ARGS[@]}"

echo "Web proxy is ready: https://${DOMAIN}"
