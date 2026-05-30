#!/usr/bin/env bash
# Run on the EC2 instance (as root or with sudo) after cloning/copying this repo.
# Installs start-bot.sh, systemd unit, and env file template.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/silencer"
ENV_FILE="/etc/silencer-bot.env"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

install -d "$INSTALL_DIR"
install -m 755 "$SCRIPT_DIR/start-bot.sh" "$INSTALL_DIR/start-bot.sh"
install -m 644 "$SCRIPT_DIR/silencer-bot.service" /etc/systemd/system/silencer-bot.service

if [[ ! -f "$ENV_FILE" ]]; then
  install -m 600 "$SCRIPT_DIR/silencer-bot.env.example" "$ENV_FILE"
  echo "Created $ENV_FILE"
  echo "IMPORTANT: Edit $ENV_FILE and set DISCORD_TOKEN and OLLAMA_API_KEY."
else
  echo "Keeping existing $ENV_FILE"
fi

systemctl daemon-reload
systemctl enable silencer-bot.service
echo "Installed. Edit $ENV_FILE (tokens required), then: systemctl start silencer-bot"
