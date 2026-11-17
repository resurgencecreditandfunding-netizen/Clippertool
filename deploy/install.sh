#!/usr/bin/env bash
# ClipCore OS — server installer / updater for Ubuntu 22.04 or 24.04.
#
# Install (no domain — dashboard at http://SERVER_IP):
#   curl -fsSL https://raw.githubusercontent.com/resurgencecreditandfunding-netizen/clippertool/main/deploy/install.sh | sudo bash
#
# Install with a domain (automatic HTTPS — recommended):
#   curl -fsSL https://raw.githubusercontent.com/resurgencecreditandfunding-netizen/clippertool/main/deploy/install.sh | sudo DOMAIN=clips.example.com bash
#
# Update to the latest version later: run the same command again. Your data is kept.
set -euo pipefail

REPO="${REPO:-https://github.com/resurgencecreditandfunding-netizen/clippertool.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/clipcore
DATA_DIR=/var/lib/clipcore
CONF=/etc/clipcore.env

say() { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this with sudo (see the command at the top of this file)."
. /etc/os-release 2>/dev/null || true
[ "${ID:-}" = "ubuntu" ] || die "This installer supports Ubuntu 22.04 / 24.04. Create the server with Ubuntu."

# Remember settings between runs so updates keep the domain.
[ -f "$CONF" ] && . "$CONF"
DOMAIN="${DOMAIN:-${SAVED_DOMAIN:-}}"
WITH_CAPTIONS="${WITH_CAPTIONS:-${SAVED_WITH_CAPTIONS:-0}}"
cat > "$CONF" <<EOF
SAVED_DOMAIN="$DOMAIN"
SAVED_WITH_CAPTIONS="$WITH_CAPTIONS"
EOF

say "Installing system packages (Python, ffmpeg, git, web server)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git curl ufw fonts-dejavu-core >/dev/null
apt-get install -y -qq fonts-inter >/dev/null 2>&1 || true
if ! command -v caddy >/dev/null; then
  apt-get install -y -qq caddy >/dev/null 2>&1 || {
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq && apt-get install -y -qq caddy >/dev/null
  }
fi

# Video rendering can spike memory; a swap file stops small servers from crashing.
if [ "$(swapon --show --noheadings | wc -l)" -eq 0 ]; then
  say "Adding a 4 GB swap file…"
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "Creating the clipcore service user…"
id clipcore >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin clipcore
mkdir -p "$DATA_DIR"
chown clipcore:clipcore "$DATA_DIR"
chmod 750 "$DATA_DIR"

say "Downloading ClipCore OS…"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch -q origin "$BRANCH"
  git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

say "Installing ClipCore OS (this takes a minute)…"
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q "$APP_DIR"
if [ "$WITH_CAPTIONS" = "1" ]; then
  "$APP_DIR/.venv/bin/pip" install -q "$APP_DIR[captions]" || echo "Captions package failed to install; continuing without captions."
fi

say "Setting up the background service…"
cat > /etc/systemd/system/clipcore.service <<EOF
[Unit]
Description=ClipCore OS
After=network-online.target
Wants=network-online.target

[Service]
User=clipcore
Group=clipcore
Environment=CLIPCORE_DATA_DIR=$DATA_DIR
Environment=HOME=$DATA_DIR
WorkingDirectory=$DATA_DIR
ExecStart=$APP_DIR/.venv/bin/clipcore serve --watch --host 127.0.0.1 --port 8787
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q clipcore
systemctl restart clipcore

say "Configuring the web server…"
if [ -n "$DOMAIN" ]; then SITE="$DOMAIN"; else SITE=":80"; fi
cat > /etc/caddy/Caddyfile <<EOF
$SITE {
    encode gzip
    reverse_proxy 127.0.0.1:8787
}
EOF
systemctl enable -q caddy
systemctl restart caddy

say "Locking down the firewall (SSH + web only)…"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

sleep 3
if systemctl is-active -q clipcore; then STATUS="running"; else STATUS="NOT running. Check: journalctl -u clipcore -n 50"; fi
IP="$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
if [ -n "$DOMAIN" ]; then URL="https://$DOMAIN"; else URL="http://$IP"; fi

cat <<EOF

────────────────────────────────────────────────────────────
  ClipCore OS is $STATUS

  Open:  $URL

  1. Create your dashboard password (first visit only)
  2. Go to Settings and paste your Twitch Client ID + Secret
  3. Click "Test connection"

  Update later:  run the same install command again
  Logs:          journalctl -u clipcore -f
  Forgot password:  sudo -u clipcore CLIPCORE_DATA_DIR=$DATA_DIR $APP_DIR/.venv/bin/clipcore reset-password
────────────────────────────────────────────────────────────
EOF
if [ -z "$DOMAIN" ]; then
  echo "  Note: without a domain the connection is not encrypted (http)."
  echo "  Add one later by re-running with DOMAIN=clips.yourdomain.com"
  echo
fi
