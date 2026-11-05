#!/usr/bin/env bash
# One-command server setup for Ubuntu 24.04.
#   sudo deploy/install.sh example.org you@example.org
# Before running: point DNS A records chat.example.org and rtc.example.org to this server's IPv4.
# Safe to re-run: secrets are kept, the stack is rebuilt and restarted.
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  echo "Usage: sudo $0 <domain> <email-for-letsencrypt>" >&2
  exit 2
fi
if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f docker-compose.prod.yml)

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

step "Checking DNS for chat.$DOMAIN and rtc.$DOMAIN"
PUBLIC_IP="$(curl -4fsS https://api.ipify.org || true)"
for host in "chat.$DOMAIN" "rtc.$DOMAIN"; do
  resolved="$(getent ahostsv4 "$host" | awk 'NR==1{print $1}')"
  if [[ -z "$resolved" ]]; then
    echo "  $host does not resolve yet. Add an A record pointing to ${PUBLIC_IP:-this server} and retry." >&2
    exit 1
  fi
  if [[ -n "$PUBLIC_IP" && "$resolved" != "$PUBLIC_IP" ]]; then
    echo "  Warning: $host -> $resolved, but this server is $PUBLIC_IP." >&2
  fi
done

step "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl ufw unattended-upgrades python3 >/dev/null
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null

step "Enabling automatic security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null

step "Configuring the firewall"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp comment 'HTTP (certificate issuance, redirect)' >/dev/null
ufw allow 443/tcp comment 'HTTPS: API and LiveKit signalling' >/dev/null
ufw allow 443/udp comment 'HTTP/3' >/dev/null
ufw allow 7881/tcp comment 'LiveKit media over TCP (fallback)' >/dev/null
ufw allow 3478/udp comment 'TURN relay' >/dev/null
ufw allow 50000:60000/udp comment 'LiveKit media' >/dev/null
ufw --force enable >/dev/null
ufw status verbose | sed 's/^/  /'

step "Generating configuration"
python3 deploy/configure.py "$DOMAIN" --email "$EMAIL"

step "Starting the stack"
"${COMPOSE[@]}" build --pull api
"${COMPOSE[@]}" up -d

step "Waiting for HTTPS on chat.$DOMAIN"
for _ in $(seq 1 60); do
  if curl -fsS "https://chat.$DOMAIN/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 5
done
if [[ "${ok:-0}" != 1 ]]; then
  echo "  The server did not answer over HTTPS yet. Check: ${COMPOSE[*]} logs caddy api" >&2
fi

step "Setting up nightly database backups (kept 14 days)"
mkdir -p /var/backups/voicechat
chmod 700 /var/backups/voicechat
cat >/etc/cron.d/voicechat-backup <<CRON
SHELL=/bin/bash
30 4 * * * root cd $ROOT && ${COMPOSE[*]} exec -T postgres pg_dump -U voicechat voicechat | gzip > /var/backups/voicechat/voicechat-\$(date +\%F).sql.gz && find /var/backups/voicechat -name '*.sql.gz' -mtime +14 -delete
CRON
chmod 644 /etc/cron.d/voicechat-backup

step "Done"
echo "  Server address for the client: https://chat.$DOMAIN"
password_line="$("${COMPOSE[@]}" logs api 2>/dev/null | grep -m1 'one-time password' || true)"
if [[ -n "$password_line" ]]; then
  echo "  First sign-in: login 'admin', password: ${password_line##*one-time password: }"
  echo "  You will be asked to change it right away. Then invite friends from the app."
else
  echo "  The owner account already exists; sign in with your password."
fi
