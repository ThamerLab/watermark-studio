#!/usr/bin/env bash
set -Eeuo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
die() { printf "${RED}✖ Uninstall stopped:${RESET} %s\n" "$*" >&2; exit 1; }
[[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run as root on the Proxmox VE host."
command -v pct >/dev/null 2>&1 || die "This script must run on a Proxmox VE host."
CONFIG=/etc/watermark-studio/instance.conf
[[ -r "$CONFIG" ]] || die "Installation metadata not found at $CONFIG."
# shellcheck disable=SC1090
source "$CONFIG"
[[ "${CTID:-}" =~ ^[0-9]+$ ]] || die "Stored container ID is invalid."
PCT_CONFIG=$(pct config "$CTID" 2>/dev/null) || die "Container $CTID does not exist."
grep -Fq "Watermark Studio managed container" <<<"$PCT_CONFIG" || die "Safety check failed: container $CTID is not marked as Watermark Studio."
HOSTNAME=$(awk -F': ' '$1=="hostname" {print $2}' <<<"$PCT_CONFIG")
STATUS=$(pct status "$CTID" | awk '{print $2}')
printf '%b' "\n${YELLOW}This will permanently delete exactly this LXC:${RESET}\n"
printf "  Container ID : %s\n  Hostname     : %s\n  Status       : %s\n\n" "$CTID" "${HOSTNAME:-unknown}" "$STATUS"
printf "No other container will be modified. Back up any documents you need first.\n"
read -r -p "Type DELETE $CTID to confirm: " CONFIRM
[[ "$CONFIRM" == "DELETE $CTID" ]] || die "Confirmation did not match. Nothing was deleted."
if [[ "$STATUS" == "running" ]]; then pct stop "$CTID"; fi
pct destroy "$CTID"
rm -f /etc/watermark-studio/instance.conf /root/watermark-studio-update.sh /root/watermark-studio-uninstall.sh
rmdir /etc/watermark-studio 2>/dev/null || true
printf "${GREEN}✔ Watermark Studio container %s was deleted.${RESET}\n" "$CTID"
