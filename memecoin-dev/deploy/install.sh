#!/usr/bin/env bash
# Copy devbot to the Iowa e2-micro and install it as a user-systemd unit next to Hermes.
#   NAME=<vm> ZONE=us-central1-a BOT_USER=kapilmcdave deploy/install.sh
# Idempotent. It does not start the bot until ~/.devbot/.env exists on the VM.
set -euo pipefail
: "${NAME:?set NAME to the VM name}"
ZONE=${ZONE:-us-central1-a}
BOT_USER=${BOT_USER:-kapilmcdave}
HERE=$(cd "$(dirname "$0")/.." && pwd)

# COPYFILE_DISABLE + --no-xattrs: keep macOS metadata out, or GNU tar on the VM warns per file.
COPYFILE_DISABLE=1 tar --no-xattrs --exclude __pycache__ -C "$HERE" -czf /tmp/devbot.tgz \
  devbot deploy requirements.txt .env.example
gcloud compute scp --zone "$ZONE" /tmp/devbot.tgz "$NAME":/tmp/devbot.tgz
gcloud compute ssh --zone "$ZONE" "$NAME" -- sudo -iu "$BOT_USER" bash -s <<'REMOTE'
set -euo pipefail
mkdir -p ~/devbot && tar -C ~/devbot -xzf /tmp/devbot.tgz
install -d -m 700 ~/.devbot
cd ~/devbot
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p ~/.config/systemd/user
cp deploy/devbot.service ~/.config/systemd/user/devbot.service
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user daemon-reload
if [ -f ~/.devbot/.env ]; then
  systemctl --user enable devbot.service
  systemctl --user restart devbot.service
  sleep 3; systemctl --user --no-pager status devbot.service | head -12
else
  echo "Installed. Create ~/.devbot/.env (0600) from ~/devbot/.env.example, add the dev keypair, then re-run."
fi
free -m | head -2
REMOTE
