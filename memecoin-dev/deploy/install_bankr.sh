#!/usr/bin/env bash
# Install the Bankr $0-launch pipeline as an always-on user-systemd unit on an ssh-reachable box.
#   deploy/install_bankr.sh                 # default host: stockholm
#   HOST=stockholm deploy/install_bankr.sh
#
# Stockholm, not the Iowa e2-micro: Bankr sits behind an AWS WAF that answers POST
# /token-launches/deploy with a 403 awselb HTML page from the GCP IP, while GETs pass. Stockholm's
# eu-north-1 IP gets a normal 401/200 from the same endpoint. Re-probe with deploy/probe_bankr.sh
# before moving the unit to another box.
#
# Idempotent. Ships into ~/bankr with its own venv (every module the pipeline imports is stdlib, so
# there is nothing to pip install) and enables linger so the unit survives logout and reboot.
# It refuses to start until ~/.devbot/.env has the keys; push those with deploy/sync_bankr_env.sh.
set -euo pipefail
HOST=${HOST:-stockholm}
HERE=$(cd "$(dirname "$0")/.." && pwd)

# COPYFILE_DISABLE + --no-xattrs: keep macOS metadata out, or GNU tar on the box warns per file.
COPYFILE_DISABLE=1 tar --no-xattrs --exclude __pycache__ -C "$HERE" -czf /tmp/bankr.tgz \
  devbot bankr_pipeline.py bankr_launch.py deploy/bankr-pipeline.service
scp -q /tmp/bankr.tgz "$HOST":/tmp/bankr.tgz
rm -f /tmp/bankr.tgz
ssh "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
export XDG_RUNTIME_DIR=/run/user/$(id -u)
mkdir -p ~/bankr && tar -C ~/bankr -xzf /tmp/bankr.tgz && rm -f /tmp/bankr.tgz
mkdir -p ~/bankr/data ~/.config/systemd/user
install -d -m 700 ~/.devbot

cd ~/bankr
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -c 'import bankr_pipeline' || { echo "FAIL: bankr_pipeline does not import"; exit 1; }

cp ~/bankr/deploy/bankr-pipeline.service ~/.config/systemd/user/bankr-pipeline.service
sudo loginctl enable-linger "$(id -un)"      # user units start at boot, not at login
systemctl --user daemon-reload

MISSING=""
for k in BANKR_API_KEY PINATA_JWT CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN; do
  grep -q "^$k=" ~/.devbot/.env 2>/dev/null || MISSING="$MISSING $k"
done
if [ -z "$MISSING" ]; then
  systemctl --user enable bankr-pipeline.service
  systemctl --user restart bankr-pipeline.service
  sleep 5
  systemctl --user --no-pager status bankr-pipeline.service | head -14
else
  echo "Installed but NOT started. Missing from ~/.devbot/.env:$MISSING"
  echo "Run deploy/sync_bankr_env.sh, then re-run this script."
fi
free -m | head -2
REMOTE
