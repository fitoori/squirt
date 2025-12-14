#!/usr/bin/env bash
# SQUIRT bespoke installer
# - Installs apt + pip prerequisites
# - Deploys the repo to a managed prefix
# - Ensures executables are chmod'd
# - Sets up systemd for the web UI
# - Adds crontab entries
# - Configures a friendly MOTD snippet

set -euo pipefail

# ── Defaults & arguments ──────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SQUIRT_USER:-${SUDO_USER:-$USER}}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
INSTALL_DIR="${SQUIRT_PREFIX:-/opt/squirt}"
CRON_ENABLE=1
MOTD_ENABLE=1
SERVICE_ENABLE=1
NONINTERACTIVE=0

usage() {
  cat <<'USAGE'
Usage: sudo ./install.sh [options]

Options:
  --user <name>       Install for this user (default: detected sudo user)
  --prefix <dir>      Install path (default: /opt/squirt)
  --skip-cron         Do not modify the user's crontab
  --skip-motd         Do not install the motd snippet
  --skip-service      Do not create/enable the systemd service
  --noninteractive    Assume yes for apt operations
  -h, --help          Show this message
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) TARGET_USER="$2"; TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6); shift 2;;
    --prefix) INSTALL_DIR="$2"; shift 2;;
    --skip-cron) CRON_ENABLE=0; shift;;
    --skip-motd) MOTD_ENABLE=0; shift;;
    --skip-service) SERVICE_ENABLE=0; shift;;
    --noninteractive) NONINTERACTIVE=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown option: $1" >&2; usage; exit 1;;
  esac
done

if [[ -z "$TARGET_HOME" ]]; then
  echo "Could not resolve home directory for user $TARGET_USER" >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "This installer must be run as root (sudo)." >&2
  exit 1
fi

# ── Helpers ───────────────────────────────────────────────────────────────
run_as_user() { su - "$TARGET_USER" -c "$*"; }

apt_install() {
  local opts=("-y")
  [[ $NONINTERACTIVE -eq 1 ]] && opts=("-y" "-o" "Dpkg::Options::=--force-confdef" "-o" "Dpkg::Options::=--force-confold")
  export DEBIAN_FRONTEND=${NONINTERACTIVE:+noninteractive}
  apt-get update
  apt-get install "${opts[@]}" "$@"
}

ensure_dirs() {
  mkdir -p "$INSTALL_DIR"
  chown -R "$TARGET_USER":"$TARGET_USER" "$INSTALL_DIR"
}

# ── Install apt prerequisites ─────────────────────────────────────────────
APT_PACKAGES=(
  git rsync
  python3-pip python3-venv python3-setuptools python3-wheel
  python3-numpy python3-pil python3-spidev
  python3-rpi.gpio python3-libgpiod python3-smbus2
  python3-lxml
)

echo "Installing apt prerequisites …"
apt_install "${APT_PACKAGES[@]}"

# ── Deploy repo to prefix ─────────────────────────────────────────────────
ensure_dirs
# Exclude runtime data directories so upgrades do not wipe cached downloads or user uploads.
# These paths are created at runtime by the apps (see static/* usage in xkcd.py, nasa.py, webui.py).
RSYNC_EXCLUDES=(
  '--exclude=.git'
  '--exclude=.venv'
  '--exclude=static/xkcd/'
  '--exclude=static/nasa/'
  '--exclude=static/saved/'
  '--exclude=static/uploads/'
  '--exclude=static/patterns/'
)
rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$SCRIPT_DIR"/ "$INSTALL_DIR"/
chown -R "$TARGET_USER":"$TARGET_USER" "$INSTALL_DIR"
ln -sfn "$INSTALL_DIR" "$TARGET_HOME/squirt"

# ── Python environment ────────────────────────────────────────────────────
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
  echo "Creating virtual environment in $INSTALL_DIR/.venv …"
  run_as_user "python3 -m venv '$INSTALL_DIR/.venv'"
fi
run_as_user "'$INSTALL_DIR/.venv/bin/pip' install --upgrade pip"
run_as_user "'$INSTALL_DIR/.venv/bin/pip' install -r '$INSTALL_DIR/requirements.txt'"

# ── Permissions ───────────────────────────────────────────────────────────
chmod +x "$INSTALL_DIR"/*.py "$INSTALL_DIR"/*.sh

# ── Systemd service for web UI ────────────────────────────────────────────
if [[ $SERVICE_ENABLE -eq 1 ]]; then
  SERVICE_FILE=/etc/systemd/system/squirt-web.service
  echo "Installing systemd service at $SERVICE_FILE"
  cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=SQUIRT web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$TARGET_USER
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$INSTALL_DIR/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStart=$INSTALL_DIR/run-web.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE
  systemctl daemon-reload
  systemctl enable --now squirt-web.service
fi

# ── Crontab entries ───────────────────────────────────────────────────────
if [[ $CRON_ENABLE -eq 1 ]]; then
  echo "Adding crontab entries for $TARGET_USER"
  PATH_PREFIX="PATH=$INSTALL_DIR/.venv/bin:/usr/bin:/bin"
  CRON_ENTRIES=(
    "0 * * * * $PATH_PREFIX $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/xkcd.py"
    "@reboot $PATH_PREFIX $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/status.py && sleep 30 && $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/nasa.py --apod"
  )
  existing=$(run_as_user "crontab -l 2>/dev/null || true")
  for entry in "${CRON_ENTRIES[@]}"; do
    if ! grep -Fq "$entry" <<<"$existing"; then
      existing+=$'\n'$entry
    fi
  done
  printf "%s\n" "$existing" | run_as_user "crontab -"
fi

# ── MOTD snippet ──────────────────────────────────────────────────────────
if [[ $MOTD_ENABLE -eq 1 ]]; then
  MOTD_DIR=/etc/motd.d
  MOTD_FILE=$MOTD_DIR/10-squirt
  mkdir -p "$MOTD_DIR"
  cat > "$MOTD_FILE" <<MOTD
Welcome to SQUIRT!
Repo: $INSTALL_DIR (symlinked at $TARGET_HOME/squirt)
Web UI: systemctl status squirt-web.service
Cron: xkcd hourly, NASA APOD on boot (crontab -e)
Enjoy your inky displays. 💧
MOTD
  chmod 644 "$MOTD_FILE"
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo "\nSQUIRT installation complete"
echo "  Installed to: $INSTALL_DIR (owned by $TARGET_USER)"
echo "  Virtualenv : $INSTALL_DIR/.venv"
[[ $SERVICE_ENABLE -eq 1 ]] && echo "  Service    : squirt-web.service (enabled)"
[[ $CRON_ENABLE -eq 1 ]] && echo "  Crontab    : updated for $TARGET_USER"
[[ $MOTD_ENABLE -eq 1 ]] && echo "  MOTD       : /etc/motd.d/10-squirt"
