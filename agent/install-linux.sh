#!/usr/bin/env bash
# Installs the assistant's PC agent on Linux: CachyOS/Arch with Hyprland, or a Raspberry Pi
# as a Wake-on-LAN relay. The dashboard gives you the exact command, e.g.
#   curl -fsSL https://raw.githubusercontent.com/Sh4dwDev/claude-assistant/main/agent/install-linux.sh | HUB=https://... PAIR=123456 bash
# Add ROLE=relay for an always-on device at home that only relays wake signals.
# Run as your normal user (not root). Run again to update.
set -euo pipefail

main() {
  say()  { printf '\n\033[1;35m==>\033[0m %s\n' "$*"; }
  info() { printf '    %s\n' "$*"; }
  die()  { printf '\n\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

  [ "$(id -u)" -ne 0 ] || die "Run this as your normal user, not with sudo."
  local ROLE="${ROLE:-pc}"
  local BASE="${XDG_DATA_HOME:-$HOME/.local/share}/claude-agent"
  local APP="$BASE/app" VENV="$BASE/venv"
  mkdir -p "$BASE"

  # --- 1. System packages ----------------------------------------------------------------
  say "Checking system packages"
  if command -v pacman >/dev/null 2>&1; then
    local pkgs=(python)
    [ "$ROLE" = "pc" ] && pkgs+=(grim playerctl wireplumber git)
    local missing=()
    for p in "${pkgs[@]}"; do pacman -Qi "$p" >/dev/null 2>&1 || missing+=("$p"); done
    if [ "${#missing[@]}" -gt 0 ]; then
      info "Installing: ${missing[*]} (asks for your password)"
      sudo pacman -S --needed --noconfirm "${missing[@]}"
    fi
  elif command -v apt-get >/dev/null 2>&1; then
    python3 -m venv --help >/dev/null 2>&1 || { info "Installing python3-venv (asks for your password)"; sudo apt-get install -y python3-venv; }
  fi
  command -v python3 >/dev/null 2>&1 || die "Python 3 is missing."

  # --- 2. Code ----------------------------------------------------------------------------------
  say "Downloading the agent"
  pkill -f "$APP/agent.py" 2>/dev/null || true
  local tmp; tmp="$(mktemp -d)"
  curl -fsSL https://github.com/Sh4dwDev/claude-assistant/archive/refs/heads/main.tar.gz | tar -xz -C "$tmp"
  rm -rf "$APP"; cp -r "$tmp"/*/agent "$APP"; rm -rf "$tmp"

  # --- 3. Libraries ---------------------------------------------------------------------------------
  say "Installing libraries"
  [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$APP/requirements.txt"

  # --- 4. Pair -------------------------------------------------------------------------------------------
  local cfg="${XDG_CONFIG_HOME:-$HOME/.config}/claude-agent/config.json"
  if [ -n "${PAIR:-}" ] || [ ! -f "$cfg" ]; then
    local hub="${HUB:-}" code="${PAIR:-}"
    [ -n "$hub" ] || read -rp "Hub address (shown in the dashboard): " hub </dev/tty
    [ -n "$code" ] || read -rp "Pairing code (shown in the dashboard): " code </dev/tty
    say "Pairing with your hub"
    "$VENV/bin/python" "$APP/agent.py" pair --hub "$hub" --code "$code" --role "$ROLE" --name "$(hostname)"
  fi

  # --- 5. Autostart ---------------------------------------------------------------------------------------
  cat > "$BASE/run.sh" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" "$APP/agent.py" run
EOF
  chmod +x "$BASE/run.sh"
  local hyprconf="$HOME/.config/hypr/hyprland.conf"
  if [ "$ROLE" = "pc" ] && [ -f "$hyprconf" ]; then
    say "Starting with Hyprland"
    grep -q "claude-agent/run.sh" "$hyprconf" || printf '\n# Assistant PC agent\nexec-once = %s\n' "$BASE/run.sh" >> "$hyprconf"
    if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then
      hyprctl dispatch exec "$BASE/run.sh" >/dev/null
    else
      info "It will start the next time you log in to Hyprland."
    fi
  else
    say "Starting as a background service"
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/claude-agent.service" <<EOF
[Unit]
Description=Assistant agent
After=network-online.target

[Service]
ExecStart=$BASE/run.sh
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable claude-agent.service
    systemctl --user restart claude-agent.service
    loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes || sudo loginctl enable-linger "$USER" || true
  fi

  # --- 6. Claude Code (coding jobs) -------------------------------------------------------------------------
  if [ "$ROLE" = "pc" ] && ! command -v claude >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/claude" ]; then
    read -rp $'\nCoding jobs need Claude Code (uses your Claude Max plan). Install it now? (Y/n) ' a </dev/tty || a=y
    if [[ ! "$a" =~ ^[nN] ]]; then
      curl -fsSL https://claude.ai/install.sh | bash
      info "Now open a terminal, type  claude  and sign in with your Claude account once."
    fi
  fi

  say "Done."
  info "This device should show as online in the dashboard within a few seconds."
  info "Logs: ${XDG_CONFIG_HOME:-$HOME/.config}/claude-agent/agent.log"
}

main "$@"
