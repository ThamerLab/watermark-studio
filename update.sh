#!/usr/bin/env bash
set -Eeuo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; RESET='\033[0m'
die() { printf "${RED}✖ Update failed:${RESET} %s\n" "$*" >&2; exit 1; }
[[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run as root on the Proxmox VE host."
command -v pct >/dev/null 2>&1 || die "This script must run on a Proxmox VE host."
CONFIG=/etc/watermark-studio/instance.conf
[[ -r "$CONFIG" ]] || die "Installation metadata not found at $CONFIG."
# shellcheck disable=SC1090
source "$CONFIG"
[[ "${CTID:-}" =~ ^[0-9]+$ && "${APP_PORT:-}" =~ ^[0-9]+$ ]] || die "Installation metadata is invalid."
pct config "$CTID" 2>/dev/null | grep -Fq "Watermark Studio managed container" || die "Container $CTID is not a Watermark Studio managed container."
printf "${CYAN}●${RESET} Updating Watermark Studio in container %s\n" "$CTID"
if ! pct status "$CTID" | grep -q running; then pct start "$CTID"; sleep 4; fi
pct exec "$CTID" -- bash -s <<'INNER_UPDATE'
set -Eeuo pipefail
cd /opt/watermark-studio
cp .env /tmp/watermark-studio.env
git fetch --depth 1 origin main
git reset --hard origin/main
mv /tmp/watermark-studio.env .env
docker compose build --pull
docker compose up -d --remove-orphans
port=$(sed -n 's/^APP_PORT=//p' .env | tail -n1)
for _ in {1..60}; do
  if curl -fsS "http://127.0.0.1:${port}/health" 2>/dev/null | grep -Eq '"status"[[:space:]]*:[[:space:]]*"healthy"'; then exit 0; fi
  sleep 4
done
docker compose logs --tail=40 app
exit 1
INNER_UPDATE
IP=$(pct exec "$CTID" -- sh -c "ip -4 -o addr show dev eth0 scope global | awk '{print \$4}' | cut -d/ -f1 | head -n1")
curl -fsS --connect-timeout 5 "http://${IP}:${APP_PORT}/health" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"healthy"' || die "Container health passed but host HTTP verification failed."
printf "${GREEN}✔ Watermark Studio updated successfully${RESET}\nURL: http://%s:%s\n" "$IP" "$APP_PORT"
