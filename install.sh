#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/ThamerLab/watermark-studio.git"
RAW_BASE="https://raw.githubusercontent.com/ThamerLab/watermark-studio/main"
MARKER="Watermark Studio managed container"
DEFAULT_HOSTNAME="watermark-studio"
DEFAULT_CORES=2
DEFAULT_MEMORY=4096
DEFAULT_DISK=12
DEFAULT_PORT=8080
CREATED_CT=0
CTID=""

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info() { printf "  ${CYAN}●${RESET} %s\n" "$*"; }
ok() { printf "  ${GREEN}✔${RESET} %s\n" "$*"; }
warn() { printf "  ${YELLOW}!${RESET} %s\n" "$*"; }
die() { printf "\n${RED}✖ Installation failed:${RESET} %s\n" "$*" >&2; [[ "$CREATED_CT" == 1 ]] && printf "  Container %s was preserved for inspection.\n" "$CTID" >&2; exit 1; }
on_error() { local code=$? line=$1; printf "\n${RED}✖ Installation failed${RESET} at line %s (exit %s).\n" "$line" "$code" >&2; [[ "$CREATED_CT" == 1 ]] && printf "  Container %s was preserved for inspection.\n" "$CTID" >&2; exit "$code"; }
trap 'on_error $LINENO' ERR

require_integer() {
  if ! [[ "$1" =~ ^[0-9]+$ ]] || (( "$1" < "$2" || "$1" > "$3" )); then die "$4"; fi
}
prompt_default() { local label=$1 default=$2; read -r -p "$label [$default]: " REPLY; printf '%s' "${REPLY:-$default}"; }

select_from() {
  local label=$1; shift
  local options=("$@") choice i
  ((${#options[@]} > 0)) || die "No suitable ${label} found."
  if ((${#options[@]} == 1)); then printf '%s' "${options[0]}"; return; fi
  printf '\n%s:\n' "$label" >&2
  for i in "${!options[@]}"; do printf '  %d) %s\n' "$((i+1))" "${options[$i]}" >&2; done
  read -r -p "Choose [1]: " choice
  choice=${choice:-1}
  require_integer "$choice" 1 "${#options[@]}" "Invalid selection."
  printf '%s' "${options[$((choice-1))]}"
}

clear
printf '%b' "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "  Watermark Studio · Proxmox Installer\n"
printf '%b' "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n\n"

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run this installer as root in the Proxmox VE shell."
if ! command -v pveversion >/dev/null 2>&1 || ! command -v pct >/dev/null 2>&1 || ! command -v pvesm >/dev/null 2>&1; then
  die "This script must run on a Proxmox VE host."
fi
[[ -t 0 ]] || die "An interactive Proxmox shell is required."
info "Checking network connectivity"
curl -fsI --connect-timeout 10 "https://raw.githubusercontent.com/ThamerLab/watermark-studio/main/install.sh" >/dev/null || die "Cannot reach GitHub. Check DNS and internet access."
ok "Proxmox VE and network checks passed"

NEXT_ID=$(pvesh get /cluster/nextid 2>/dev/null)
require_integer "$NEXT_ID" 100 999999999 "Could not determine an available container ID."
mapfile -t ROOT_STORAGES < <(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}')
mapfile -t TEMPLATE_STORAGES < <(pvesm status -content vztmpl 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}')
((${#ROOT_STORAGES[@]})) || die "No active storage supporting container root disks was found."
((${#TEMPLATE_STORAGES[@]})) || die "No active storage supporting LXC templates was found."
mapfile -t BRIDGES < <(ip -o link show type bridge 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1 | grep '^vmbr' || true)
((${#BRIDGES[@]})) || BRIDGES=("vmbr0")

printf "Select installation mode:\n  1) Default — 2 CPU, 4096 MB RAM, 12 GB disk, DHCP\n  2) Advanced\n"
read -r -p "Choice [1]: " MODE
MODE=${MODE:-1}
[[ "$MODE" == "1" || "$MODE" == "2" ]] || die "Invalid installation mode."

CTID="$NEXT_ID"; HOSTNAME="$DEFAULT_HOSTNAME"; CORES="$DEFAULT_CORES"; MEMORY="$DEFAULT_MEMORY"; DISK="$DEFAULT_DISK"; APP_PORT="$DEFAULT_PORT"
ROOT_STORAGE="${ROOT_STORAGES[0]}"; TEMPLATE_STORAGE="${TEMPLATE_STORAGES[0]}"; BRIDGE="${BRIDGES[0]}"; NET_CONFIG="ip=dhcp"

if [[ "$MODE" == "2" ]]; then
  CTID=$(prompt_default "Container ID" "$NEXT_ID"); require_integer "$CTID" 100 999999999 "Invalid container ID."
  pct status "$CTID" >/dev/null 2>&1 && die "Container ID $CTID already exists."
  HOSTNAME=$(prompt_default "Hostname" "$DEFAULT_HOSTNAME")
  [[ "$HOSTNAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}$ ]] || die "Invalid hostname."
  CORES=$(prompt_default "CPU cores" "$DEFAULT_CORES"); require_integer "$CORES" 1 128 "CPU must be between 1 and 128."
  MEMORY=$(prompt_default "RAM in MB" "$DEFAULT_MEMORY"); require_integer "$MEMORY" 512 1048576 "RAM must be between 512 and 1048576 MB."
  DISK=$(prompt_default "Disk in GB" "$DEFAULT_DISK"); require_integer "$DISK" 8 1048576 "Disk must be at least 8 GB."
  ROOT_STORAGE=$(select_from "Container storage" "${ROOT_STORAGES[@]}")
  TEMPLATE_STORAGE=$(select_from "Template storage" "${TEMPLATE_STORAGES[@]}")
  BRIDGE=$(select_from "Network bridge" "${BRIDGES[@]}")
  read -r -p "Use DHCP? [Y/n]: " USE_DHCP
  if [[ "${USE_DHCP:-Y}" =~ ^[Nn]$ ]]; then
    STATIC_IP=$(prompt_default "IPv4 address with CIDR" "192.168.10.123/24")
    GATEWAY=$(prompt_default "IPv4 gateway" "192.168.10.1")
    [[ "$STATIC_IP" =~ ^[0-9.]+/[0-9]{1,2}$ && "$GATEWAY" =~ ^[0-9.]+$ ]] || die "Invalid static network settings."
    NET_CONFIG="ip=${STATIC_IP},gw=${GATEWAY}"
  fi
  APP_PORT=$(prompt_default "Application port" "$DEFAULT_PORT"); require_integer "$APP_PORT" 1 65535 "Port must be between 1 and 65535."
  printf "\nConfiguration summary:\n  CT ID: %s\n  Hostname: %s\n  CPU/RAM/Disk: %s / %s MB / %s GB\n  Storage: %s\n  Bridge: %s (%s)\n  Port: %s\n" "$CTID" "$HOSTNAME" "$CORES" "$MEMORY" "$DISK" "$ROOT_STORAGE" "$BRIDGE" "$NET_CONFIG" "$APP_PORT"
  read -r -p "Create this container? [y/N]: " CONFIRM
  [[ "$CONFIRM" =~ ^[Yy]$ ]] || die "Cancelled by user."
fi

pct status "$CTID" >/dev/null 2>&1 && die "Container ID $CTID already exists."
info "Refreshing Debian template index"
pveam update >/dev/null
TEMPLATE=$(pveam available --section system 2>/dev/null | awk '$2 ~ /^debian-13-standard_.*_amd64\.tar\.(zst|gz)$/ {print $2}' | sort -V | tail -n1)
[[ -n "$TEMPLATE" ]] || die "No Debian 13 LXC template is available. Update Proxmox VE and retry."
TEMPLATE_VOLUME=$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk -v name="$TEMPLATE" '$1 ~ name"$" {print $1; exit}')
if [[ -z "$TEMPLATE_VOLUME" ]]; then
  info "Downloading Debian 13 LXC template"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
  TEMPLATE_VOLUME="${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}"
else
  ok "Using existing template $TEMPLATE"
fi

info "Creating unprivileged Debian 13 container $CTID"
pct create "$CTID" "$TEMPLATE_VOLUME" \
  --hostname "$HOSTNAME" \
  --cores "$CORES" \
  --memory "$MEMORY" \
  --swap 512 \
  --rootfs "${ROOT_STORAGE}:${DISK}" \
  --net0 "name=eth0,bridge=${BRIDGE},${NET_CONFIG},type=veth" \
  --unprivileged 1 \
  --features "nesting=1,keyctl=1" \
  --onboot 1 \
  --tags "watermark-studio" \
  --description "$MARKER" >/dev/null
CREATED_CT=1
ok "Container $CTID created"

info "Starting container and waiting for networking"
pct start "$CTID"
for _ in {1..60}; do
  if pct exec "$CTID" -- sh -c 'ip -4 -o addr show scope global | grep -q . && getent hosts download.docker.com >/dev/null 2>&1'; then break; fi
  sleep 2
done
pct exec "$CTID" -- sh -c 'ip -4 -o addr show scope global | grep -q . && getent hosts download.docker.com >/dev/null 2>&1' || die "The container did not obtain working network connectivity."
ok "Container network is ready"

info "Installing Docker and Watermark Studio (this can take several minutes)"
pct exec "$CTID" -- bash -s -- "$APP_PORT" "$REPO_URL" <<'INNER_INSTALL'
set -Eeuo pipefail
APP_PORT=$1
REPO_URL=$2
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git >/dev/null
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian %s stable\n' "$(dpkg --print-architecture)" "$VERSION_CODENAME" >/etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
systemctl enable --now docker >/dev/null
rm -rf /opt/watermark-studio
git clone --depth 1 --branch main "$REPO_URL" /opt/watermark-studio >/dev/null
cd /opt/watermark-studio
umask 077
printf 'APP_PORT=%s\nFILE_RETENTION_MINUTES=60\nMAX_UPLOAD_MB=100\nLIBREOFFICE_TIMEOUT_SECONDS=180\nMAX_BATCH_FILES=20\nMAX_BATCH_ROWS=100\nMAX_BATCH_OUTPUTS=500\nMAX_BATCH_TOTAL_MB=500\n' "$APP_PORT" >.env
docker compose up -d --build --remove-orphans
for _ in {1..90}; do
  if curl -fsS "http://127.0.0.1:${APP_PORT}/health" 2>/dev/null | grep -Eq '"status"[[:space:]]*:[[:space:]]*"healthy"'; then exit 0; fi
  sleep 4
done
docker compose ps
docker compose logs --tail=40 app
exit 1
INNER_INSTALL
ok "Application container is healthy"

LXC_IP=$(pct exec "$CTID" -- sh -c "ip -4 -o addr show dev eth0 scope global | awk '{print \$4}' | cut -d/ -f1 | head -n1")
[[ -n "$LXC_IP" ]] || die "Could not determine the container IPv4 address."
info "Verifying HTTP from the Proxmox host"
for _ in {1..15}; do
  if curl -fsS --connect-timeout 3 "http://${LXC_IP}:${APP_PORT}/health" 2>/dev/null | grep -Eq '"status"[[:space:]]*:[[:space:]]*"healthy"'; then HTTP_OK=1; break; fi
  sleep 2
done
[[ "${HTTP_OK:-0}" == 1 ]] || die "The internal health check passed, but HTTP is not reachable at ${LXC_IP}:${APP_PORT}."

install -d -m 0700 /etc/watermark-studio
printf 'CTID=%q\nAPP_PORT=%q\nMARKER=%q\n' "$CTID" "$APP_PORT" "$MARKER" >/etc/watermark-studio/instance.conf
curl -fsSL "$RAW_BASE/update.sh" -o /root/watermark-studio-update.sh
curl -fsSL "$RAW_BASE/uninstall.sh" -o /root/watermark-studio-uninstall.sh
chmod 0700 /root/watermark-studio-update.sh /root/watermark-studio-uninstall.sh

trap - ERR
printf '%b' "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf " Watermark Studio\n"
printf '%b' "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n\n"
printf '%b' "${GREEN}✅ Installation successful${RESET}\n\n"
printf "Container ID : %s\nIP Address   : %s\nPort         : %s\n\n" "$CTID" "$LXC_IP" "$APP_PORT"
printf "🌐 Open:\nhttp://%s:%s\n\n" "$LXC_IP" "$APP_PORT"
printf "Update       : bash /root/watermark-studio-update.sh\n"
printf "Uninstall    : bash /root/watermark-studio-uninstall.sh\n\n"
printf '%b' "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
