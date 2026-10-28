#!/usr/bin/env bash
# Deploy the capital-disabled pump.fun paper rig across the two project VMs.
#
# Split, and why it is not arbitrary:
#   IOWA  (instance-20260902-221655, e2-micro, 964 MB) -- feed + paper broker.
#         Both are a few tens of MB. This box also runs a LIVE polymarket-us-mm
#         agent with ~250 MB free, so the 1.95 GB model must not go here: it
#         would OOM-kill a running trader.
#   FRA   (solana-funder-shadow-fra, e2-small, 1976 MB + 4 GB swap) -- the
#         GLiNER2.5-Decide sidecar, bound to its INTERNAL ip only.
#
# The two talk over the default VPC, which already permits 10.128.0.0/9 on all
# TCP via `default-allow-internal`, so no firewall rule is created here.
#
# Nothing in this deployment holds a keypair, builds or signs a transaction.
set -euo pipefail

project="${PROJECT:-project-a9288e9c-05ba-4e63-81d}"
iowa="${IOWA:-instance-20260902-221655}"
iowa_zone="${IOWA_ZONE:-us-central1-a}"
fra="${FRA:-solana-funder-shadow-fra}"
fra_zone="${FRA_ZONE:-europe-west3-b}"
root="$(cd "$(dirname "$0")" && pwd)"

fra_ip="$(gcloud compute instances describe "$fra" --zone="$fra_zone" \
  --project="$project" --format='value(networkInterfaces[0].networkIP)')"
echo "FRA internal ip: $fra_ip"

# One shared secret, generated locally and never printed. The monitoring and
# control paths do not share a credential (`a-monitoring-channel-must-not-share
# -a-credential-with-the-control-channel`) -- there is no control path here at
# all, so this token only gates inference.
token_file="${SNIPE_TOKEN_FILE:-$HOME/.pumpsnipe.token}"
if [[ ! -f "$token_file" ]]; then
  umask 077; head -c 32 /dev/urandom | base64 | tr -d '\n=' > "$token_file"
  echo "wrote a new token to $token_file"
fi
token="$(cat "$token_file")"

bundle="$(mktemp -d)"
trap 'rm -rf "$bundle"' EXIT
mkdir -p "$bundle/app"
cp "$root"/snipedecode.py "$root"/snipefeed.py "$root"/snipepaper.py \
   "$root"/snipescore.py "$root"/snipedecide.py "$root"/snipewallet.py \
   "$root"/test_snipe*.py \
   "$bundle/app/"
cp "$root/preregistrations/pumpfun_gliner_decide_paper_20261005.md" \
   "$root/preregistrations/pumpfun_wallet_history_gliner_paper_20261010.md" "$bundle/app/"
tar -C "$bundle" -czf "$bundle/snipe.tgz" app

# ---------------------------------------------------------------- FRA: decider
cat >"$bundle/install_fra.sh" <<'FRAEOF'
#!/usr/bin/env bash
set -euo pipefail
venv="$HOME/gliner-venv"
app="$HOME/pumpsnipe/app"
mkdir -p "$app" "$HOME/pumpsnipe/data"
tar -C "$HOME/pumpsnipe" --strip-components=1 -xzf /tmp/snipe.tgz app

# 4 GB of swap: the checkpoint is 1.95 GB of fp32 on a 1976 MB box, so the
# weights do not fit in RAM and the page cache has to absorb them. Inference is
# post-gate and rare (a ~1.5% screen pass rate), so paging is affordable here in
# a way it would not be on a hot path.
if ! /sbin/swapon --show | grep -q swapfile; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null && sudo /sbin/swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

if [[ ! -x "$venv/bin/python" ]]; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
  python3 -m venv "$venv"
fi
"$venv/bin/pip" install -q --upgrade pip
"$venv/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu
# peft is an undeclared runtime import of gliner2.training.trainer, which
# gliner2.inference.engine imports, so AutoExtractor cannot load without it.
"$venv/bin/pip" install -q gliner2 transformers sentencepiece peft

sudo install -d -m 755 /etc/pumpsnipe
printf 'SNIPE_DECIDE_TOKEN=%s\nSNIPE_DECIDE_MODEL=%s\nSNIPE_BIND=%s\n' \
  "$SNIPE_TOKEN" "${SNIPE_MODEL:-fastino/GLiNER2.5-Decide}" "$SNIPE_BIND" \
  | sudo tee /etc/pumpsnipe/decide.env >/dev/null
sudo chmod 600 /etc/pumpsnipe/decide.env

# D1 is BLOCKING. A degenerate classification head is a void instrument, and
# this model family has already produced a clean-looking null from one
# (`gliner2-structure-head-works-classification-head-degenerate`). The unit is
# only installed if D1 passes; the selftest's own exit code decides.
cd "$app"
set +e
"$venv/bin/python" snipedecide.py --selftest \
  --selftest-json "$HOME/pumpsnipe/data/d1_selftest.json"
d1=$?
set -e
if [[ $d1 -ne 0 ]]; then
  echo "D1 FAILED: the classification head is degenerate on this checkpoint."
  echo "Refusing to install the sidecar. See ~/pumpsnipe/data/d1_selftest.json"
  exit 2
fi

sudo tee /etc/systemd/system/pumpsnipe-decide.service >/dev/null <<UNIT
[Unit]
Description=GLiNER2.5-Decide sidecar (text verdicts only, no capital path)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
EnvironmentFile=/etc/pumpsnipe/decide.env
ExecStart=$venv/bin/python $app/snipedecide.py --host $SNIPE_BIND --port 8137
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now pumpsnipe-decide.service
sleep 45
curl -fsS "http://$SNIPE_BIND:8137/health" || echo "health not up yet"
FRAEOF

# -------------------------------------------------- IOWA: feed + paper broker
cat >"$bundle/install_iowa.sh" <<'IOWAEOF'
#!/usr/bin/env bash
set -euo pipefail
venv="$HOME/pumpsnipe/venv"
app="$HOME/pumpsnipe/app"
data="$HOME/pumpsnipe/data"
mkdir -p "$app" "$data"
tar -C "$HOME/pumpsnipe" --strip-components=1 -xzf /tmp/snipe.tgz app
[[ -x "$venv/bin/python" ]] || python3 -m venv "$venv"
"$venv/bin/pip" install -q --upgrade pip
"$venv/bin/pip" install -q websockets pytest
cd "$app" && "$venv/bin/python" -m pytest -q test_snipe*.py

sudo install -d -m 755 /etc/pumpsnipe
printf 'SNIPE_DECIDE_TOKEN=%s\nSNIPE_DECIDE_URL=%s\n' \
  "$SNIPE_TOKEN" "$SNIPE_DECIDE_URL" | sudo tee /etc/pumpsnipe/paper.env >/dev/null
sudo chmod 600 /etc/pumpsnipe/paper.env

# Each service rotates its own output on start, so a restart never appends to a
# window it did not collect, and a stats line marks the end of every tape.
cat >"$app/run_feed.sh" <<'RUN'
#!/usr/bin/env bash
set -euo pipefail
data="$HOME/pumpsnipe/data"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
exec "$HOME/pumpsnipe/venv/bin/python" "$HOME/pumpsnipe/app/snipefeed.py" \
  --tape "$data/tape-$stamp.jsonl" --duration 86400 --meta-workers 4
RUN

cat >"$app/run_paper.sh" <<'RUN'
#!/usr/bin/env bash
set -euo pipefail
data="$HOME/pumpsnipe/data"
# Follow the newest tape. A tape appears before the broker starts because the
# feed unit is ordered first; if none exists yet, wait rather than crash-loop.
for _ in $(seq 1 120); do
  tape="$(ls -t "$data"/tape-*.jsonl 2>/dev/null | head -1)"
  [[ -n "${tape:-}" ]] && break
  sleep 5
done
[[ -n "${tape:-}" ]] || { echo "no tape after 10 min"; exit 1; }
stamp="$(basename "$tape" .jsonl | sed 's/^tape-//')"
exec "$HOME/pumpsnipe/venv/bin/python" "$HOME/pumpsnipe/app/snipepaper.py" \
  --tape "$tape" --ledger "$data/ledger-$stamp.jsonl" --follow \
  --clip-sol "${SNIPE_CLIP_SOL:-0.25}"
RUN
chmod 755 "$app"/run_feed.sh "$app"/run_paper.sh

for svc in feed paper; do
  sudo tee "/etc/systemd/system/pumpsnipe-$svc.service" >/dev/null <<UNIT
[Unit]
Description=pump.fun paper rig: $svc (capital disabled)
After=network-online.target$( [[ $svc == paper ]] && echo " pumpsnipe-feed.service" )
Wants=network-online.target

[Service]
Type=simple
User=$USER
EnvironmentFile=/etc/pumpsnipe/paper.env
ExecStart=$app/run_$svc.sh
Restart=always
RestartSec=10
# The box runs a live trader. Cap this rig so it can never be the reason that
# agent is reaped: the limit is a fraction of what the box has, not a number
# guessed in a vacuum.
MemoryMax=180M
Nice=10
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
done
sudo systemctl daemon-reload
sudo systemctl enable --now pumpsnipe-feed.service pumpsnipe-paper.service
sleep 20
systemctl is-active pumpsnipe-feed.service pumpsnipe-paper.service
IOWAEOF

chmod 755 "$bundle"/install_*.sh

echo "== FRA =="
gcloud compute scp "$bundle/snipe.tgz" "$bundle/install_fra.sh" \
  "$fra:/tmp/" --project="$project" --zone="$fra_zone"
gcloud compute ssh "$fra" --project="$project" --zone="$fra_zone" \
  --command="chmod +x /tmp/install_fra.sh && SNIPE_TOKEN='$token' SNIPE_BIND='$fra_ip' /tmp/install_fra.sh"

echo "== IOWA =="
gcloud compute scp "$bundle/snipe.tgz" "$bundle/install_iowa.sh" \
  "$iowa:/tmp/" --project="$project" --zone="$iowa_zone"
gcloud compute ssh "$iowa" --project="$project" --zone="$iowa_zone" \
  --command="chmod +x /tmp/install_iowa.sh && SNIPE_TOKEN='$token' SNIPE_DECIDE_URL='http://$fra_ip:8137' /tmp/install_iowa.sh"

echo
echo "Deployed. Score with:"
echo "  gcloud compute ssh $iowa --zone=$iowa_zone --project=$project \\"
echo "    --command='cd ~/pumpsnipe/app && ~/pumpsnipe/venv/bin/python snipescore.py \\"
echo "      --ledger \$(ls -t ~/pumpsnipe/data/ledger-*.jsonl | head -1)'"
