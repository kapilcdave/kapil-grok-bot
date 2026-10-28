#!/usr/bin/env bash

set -euo pipefail

project="project-a9288e9c-05ba-4e63-81d"
zone="europe-west3-b"
instance="solana-funder-shadow-fra"
root="$(cd "$(dirname "$0")" && pwd)"
engine="$root/../../memecoin/engine"
bundle="$(mktemp -d)"
trap 'rm -rf "$bundle"' EXIT

mkdir -p "$bundle/app/data" "$bundle/engine"
cp "$root"/pump*.py "$bundle/app/"
cp "$root/data/pumpfunder_research_scores.jsonl" "$bundle/app/data/"
cp "$engine/Cargo.toml" "$engine/Cargo.lock" "$bundle/engine/"
cp -R "$engine/src" "$engine/vendor" "$bundle/engine/"

cat >"$bundle/install.sh" <<'REMOTE'
#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential ca-certificates curl pkg-config python3

sudo useradd --system --home /var/lib/pumpfunder \
  --shell /usr/sbin/nologin pumpfunder 2>/dev/null || true
sudo install -d -o root -g root -m 755 /opt/pumpfunder
sudo install -d -o pumpfunder -g pumpfunder -m 750 \
  /var/lib/pumpfunder /var/log/pumpfunder
sudo install -d -o root -g root -m 755 /etc/pumpfunder

sudo cp -R app/. /opt/pumpfunder/
sudo chown -R root:root /opt/pumpfunder
sudo chmod -R a-w /opt/pumpfunder

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
  sh -s -- -y --profile minimal
source "$HOME/.cargo/env"
(
  cd engine
  CARGO_BUILD_JOBS=1 cargo build --release \
    --features websocket --bin websocket_probe
)
sudo install -o root -g root -m 755 \
  engine/target/release/websocket_probe \
  /opt/pumpfunder/websocket_probe

sudo tee /etc/pumpfunder/env >/dev/null <<'EOF'
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
SOLANA_RPC_WS_URL=wss://api.mainnet-beta.solana.com
CAPITAL_ENABLED=false
EOF
sudo chmod 600 /etc/pumpfunder/env

sudo tee /opt/pumpfunder/run_births.sh >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd /var/lib/pumpfunder
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
[[ ! -f births.jsonl ]] || mv births.jsonl "births-${stamp}.jsonl"
[[ ! -f births.meta.json ]] || \
  mv births.meta.json "births-${stamp}.meta.json"
exec /opt/pumpfunder/websocket_probe \
  --output /var/lib/pumpfunder/births.jsonl \
  --metadata /var/lib/pumpfunder/births.meta.json \
  --max-updates 100000000 \
  --max-seconds 86400
EOF

sudo tee /opt/pumpfunder/run_watcher.sh >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd /opt/pumpfunder
exec python3 pumpfunderwatch.py \
  --db /var/lib/pumpfunder/funders.sqlite \
  --scores data/pumpfunder_research_scores.jsonl \
  --journal /var/lib/pumpfunder/funder-watch.jsonl \
  --limit 10 \
  --qps 1 \
  --interval-seconds 5
EOF

sudo tee /opt/pumpfunder/run_shadow.sh >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
while [[ ! -f /var/lib/pumpfunder/births.jsonl ]]; do
  sleep 1
done
tail -n +1 -F /var/lib/pumpfunder/births.jsonl | \
  python3 /opt/pumpfunder/pumpfunderpreindex.py shadow \
    --db /var/lib/pumpfunder/funders.sqlite \
    --input /dev/stdin \
    --scores /opt/pumpfunder/data/pumpfunder_research_scores.jsonl \
    --output /var/lib/pumpfunder/shadow-decisions.jsonl \
    --max-age-slots 20000000 \
    --deadline-seconds 0.002 \
    --require-invalidator-coverage
EOF
sudo chmod 755 /opt/pumpfunder/run_*.sh

for service in births watcher shadow; do
  sudo tee "/etc/systemd/system/pumpfunder-${service}.service" \
    >/dev/null <<EOF
[Unit]
Description=Pump.fun funder ${service} capital-disabled shadow
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pumpfunder
Group=pumpfunder
EnvironmentFile=/etc/pumpfunder/env
ExecStart=/opt/pumpfunder/run_${service}.sh
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/pumpfunder /var/log/pumpfunder

[Install]
WantedBy=multi-user.target
EOF
done

sudo systemctl daemon-reload
sudo systemctl enable --now \
  pumpfunder-births.service \
  pumpfunder-watcher.service \
  pumpfunder-shadow.service
sudo systemctl --no-pager --full status \
  pumpfunder-births.service \
  pumpfunder-watcher.service \
  pumpfunder-shadow.service
REMOTE
chmod 755 "$bundle/install.sh"

tar -C "$bundle" -czf "$bundle/pumpfunder-shadow.tgz" \
  app engine install.sh
gcloud compute scp "$bundle/pumpfunder-shadow.tgz" \
  "$instance:/tmp/pumpfunder-shadow.tgz" \
  --project="$project" --zone="$zone"
gcloud compute ssh "$instance" \
  --project="$project" --zone="$zone" \
  --command='set -e; work=$(mktemp -d); tar -C "$work" -xzf /tmp/pumpfunder-shadow.tgz; cd "$work"; ./install.sh'
