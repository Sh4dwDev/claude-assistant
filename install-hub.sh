#!/usr/bin/env bash
# One-command hub install for a VPS, Raspberry Pi or spare Linux box (Debian/Ubuntu/Raspberry Pi OS).
#
#   curl -fsSL https://raw.githubusercontent.com/Sh4dwDev/claude-assistant/main/install-hub.sh | sudo bash
#
# Run the same command again to update. Everything is wrapped in main() so that
# piping into bash only starts once the whole script has downloaded.
set -euo pipefail

REPO_URL="https://github.com/Sh4dwDev/claude-assistant.git"
INSTALL_DIR="/opt/claude-assistant"

say()  { printf '\n\033[1;35m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

get_code() {
  # Run from inside a downloaded copy? Use it. Otherwise download (or update) to /opt.
  local here=""
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  if [ -n "$here" ] && [ -f "$here/docker-compose.yml" ]; then
    APP_DIR="$here"
    return
  fi
  command -v git >/dev/null 2>&1 || { say "Installing git"; apt-get update -qq && apt-get install -y -qq git; }
  if [ -d "$INSTALL_DIR/.git" ]; then
    say "Updating the code in $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
  else
    say "Downloading the code to $INSTALL_DIR"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
  APP_DIR="$INSTALL_DIR"
}

main() {
  [ "$(id -u)" -eq 0 ] || die "Please run it with sudo (see the README)."
  get_code
  cd "$APP_DIR"

  # --- Memory check ----------------------------------------------------------
  local mem_mb
  mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  if [ "$mem_mb" -lt 1800 ]; then
    info "Note: this machine has ${mem_mb} MB RAM. The hub runs fine, but the offline model (milestone 5) wants 2 GB+."
  fi

  # --- Docker ------------------------------------------------------------------
  if ! command -v docker >/dev/null 2>&1; then
    say "Installing Docker"
    curl -fsSL https://get.docker.com | sh
  fi
  docker compose version >/dev/null 2>&1 || die "Docker is installed but 'docker compose' is missing. Install the docker-compose-plugin package."
  systemctl enable --now docker >/dev/null 2>&1 || true

  # --- Tailscale (private network: only your devices can reach the hub) ---------
  if ! command -v tailscale >/dev/null 2>&1; then
    say "Installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  systemctl enable --now tailscaled >/dev/null 2>&1 || true

  if ! tailscale status >/dev/null 2>&1; then
    say "Connect this server to your Tailscale network"
    info "A login link will appear below. Open it on your phone or PC and sign in"
    info "with the same account you use in the Tailscale app."
    tailscale up --hostname=assistant
  fi

  # --- Start the hub -------------------------------------------------------------
  say "Building and starting the hub (first time takes a few minutes)"
  mkdir -p data
  chmod 700 data
  docker compose up -d --build

  info "Waiting for the hub to answer..."
  for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then break; fi
    sleep 2
  done
  curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 || die "The hub did not start. See: cd $APP_DIR && docker compose logs hub"

  # --- HTTPS address inside your tailnet ---------------------------------------------
  say "Publishing the hub on your private Tailscale address (HTTPS)"
  if ! tailscale serve --bg http://127.0.0.1:8000 >/tmp/ts-serve.log 2>&1; then
    cat /tmp/ts-serve.log
    info ""
    info "Tailscale needs HTTPS turned on once for your network:"
    info "  1. Open https://login.tailscale.com/admin/dns"
    info "  2. Make sure MagicDNS is on, then click 'Enable HTTPS'."
    info "  3. Run the install command again."
    exit 1
  fi

  local dns_name
  dns_name=$(tailscale status --json | sed -n 's/.*"DNSName": *"\([^"]*\)\.".*/\1/p' | head -n1)

  say "Done."
  info ""
  info "Open this on your PC or phone (the Tailscale app must be on):"
  info ""
  info "    https://${dns_name}"
  info ""
  info "The setup page asks for a password and your keys, one at a time."
  info "Your data lives in: $APP_DIR/data   (copy that folder to move the hub)"
}

main "$@"
