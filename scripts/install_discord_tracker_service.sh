#!/usr/bin/env bash
set -euo pipefail

ROOT="${LIFEOS_ROOT:-/home/ubuntu/hermis-life-os}"
SERVICE_NAME="${DISCORD_TRACKER_SERVICE_NAME:-hermis-discord-tracker}"
RUN_USER="${DISCORD_TRACKER_USER:-$(id -un)}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$SERVICE_FILE" >/dev/null <<SERVICE
[Unit]
Description=Hermis Life OS Discord Tracker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${ROOT}
EnvironmentFile=${ROOT}/.env.discord-tracker
ExecStart=${ROOT}/scripts/run_discord_tracker.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager
