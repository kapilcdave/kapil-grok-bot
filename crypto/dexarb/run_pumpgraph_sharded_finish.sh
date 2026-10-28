#!/usr/bin/env bash

# Resume the two read-only funding shards until clean, then build every
# fail-closed analysis/report artifact. No transaction construction, signing,
# or submission code is invoked.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

mkdir -p data logs

RPC_URL_FILE="${PUMPGRAPH_RPC_URL_FILE:-/private/tmp/claude-501/-Users-kapil-proj/6e9fa0ce-caa1-48c6-a4f7-403e0217fc4e/scratchpad/rpc.url}"
RUFF="${PUMPGRAPH_RUFF:-../crypto-grid-research/.venv/bin/ruff}"
CARGO="${PUMPGRAPH_CARGO:-/opt/homebrew/bin/cargo}"
UID_VALUE="$(id -u)"

restore_swinghist() {
  launchctl enable "gui/${UID_VALUE}/com.kapil.swinghist" 2>/dev/null || true
  if ! launchctl print "gui/${UID_VALUE}/com.kapil.swinghist" >/dev/null 2>&1; then
    launchctl submit \
      -l com.kapil.swinghist \
      -o "$ROOT/logs/swinghist_launchd.out" \
      -e "$ROOT/logs/swinghist_launchd.err" \
      -- /usr/bin/caffeinate -dimsu /usr/bin/python3 -u \
      "$ROOT/swinghist.py" \
      --stage2 \
      --rpc-url-file "$RPC_URL_FILE" \
      --rps 0.6 \
      --conc 2 \
      --sample-pools 800 \
      --max-points-per-pool 2 \
      --pools "$ROOT/data/pumpswap_pool_accounts.json" \
      --rows "$ROOT/data/swinghist_rows.jsonl" \
      >/dev/null 2>&1 || true
  fi
}
trap restore_swinghist EXIT

shard_is_clean() {
  python3 - "$1" <<'PY'
import json
import sys

with open(sys.argv[1]) as handle:
    data = json.load(handle)
summary = data["summary"]
statuses = set(summary["status_counts"])
raise SystemExit(
    0 if (
        summary["n"] == 4236
        and summary["errors"] == 0
        and statuses <= {
            "direct_funder_found",
            "no_direct_funder_in_history",
        }
    ) else 1
)
PY
}

run_shard() {
  local name="$1"
  local rpc_url="$2"
  local birth_audit="$3"
  local jsonl="$4"
  local summary="$5"
  local pause="$6"
  local transaction_batch_size="$7"

  while true; do
    echo "[$(date -u +%FT%TZ)] ${name}: resume"
    if SOLANA_RPC_URL="$rpc_url" python3 pumpfunding_batch.py \
      --all \
      --birth-audit "$birth_audit" \
      --jsonl "$jsonl" \
      --summary "$summary" \
      --history-limit 50 \
      --batch-size 1 \
      --transaction-batch-size "$transaction_batch_size" \
      --pause "$pause"
    then
      if shard_is_clean "$summary"; then
        echo "[$(date -u +%FT%TZ)] ${name}: clean"
        break
      fi
    fi
    echo "[$(date -u +%FT%TZ)] ${name}: retrying after 15 seconds"
    sleep 15
  done
}

if [[ ! -s "$RPC_URL_FILE" ]]; then
  echo "missing configured RPC URL file: $RPC_URL_FILE" >&2
  exit 1
fi

CONFIGURED_RPC="$(cat "$RPC_URL_FILE")"
run_shard \
  configured-shard-a \
  "$CONFIGURED_RPC" \
  data/pumpbirthaudit_funding2_shard0.jsonl \
  data/pumpfunding2_shard0.jsonl \
  data/pumpfunding2_shard0_summary.json \
  0.8 \
  20 \
  >>logs/pumpfunding_shard0.log 2>&1 &
SHARD0_PID=$!

run_shard \
  configured-shard-b \
  "$CONFIGURED_RPC" \
  data/pumpbirthaudit_funding2_shard1.jsonl \
  data/pumpfunding2_shard1.jsonl \
  data/pumpfunding2_shard1_summary.json \
  0.8 \
  20 \
  >>logs/pumpfunding_shard1.log 2>&1 &
SHARD1_PID=$!

wait "$SHARD0_PID"
wait "$SHARD1_PID"

echo "[$(date -u +%FT%TZ)] merge clean funding shards"
python3 pumpfundingmerge.py \
  --inputs 'data/pumpfunding2_shard*.jsonl' \
  --output data/pumpfunding.jsonl \
  --summary data/pumpfunding_summary.json \
  >logs/pumpfundingmerge.log 2>&1

echo "[$(date -u +%FT%TZ)] funding graph tests"
python3 pumpfundinganalyze.py \
  --json data/pumpfundinganalyze.json \
  >logs/pumpfundinganalyze.log 2>&1

echo "[$(date -u +%FT%TZ)] live funding lookup gate"
python3 pumpfundinglookupreport.py \
  --json data/pumpfundinglookup_live.json \
  >logs/pumpfundinglookupreport.log 2>&1

echo "[$(date -u +%FT%TZ)] fail-closed shadow export"
python3 pumpgraphexport.py \
  --output data/pumpgraph_scores.jsonl \
  --metadata data/pumpgraph_scores_meta.json \
  >logs/pumpgraphexport.log 2>&1

echo "[$(date -u +%FT%TZ)] graph-gated paper replays"
python3 pumpcompound.py \
  --graph-gated \
  --json data/pumpcompound_zero_tip.json \
  >logs/pumpcompound_zero_tip.log 2>&1
python3 pumpcompound.py \
  --graph-gated \
  --roundtrip-tip-sol 0.001 \
  --json data/pumpcompound_tip_001.json \
  >logs/pumpcompound_tip_001.log 2>&1

echo "[$(date -u +%FT%TZ)] final report"
python3 pumpgraphreport.py >logs/pumpgraphreport.log 2>&1

echo "[$(date -u +%FT%TZ)] validation"
python3 -m pytest -q >logs/pumpgraph_pytest.log 2>&1
"$RUFF" check \
  pumpbirthaudit.py \
  pumpbirthaudit_batch.py \
  pumpbirthaudit_resume.py \
  pumpfunding.py \
  pumpfunding_batch.py \
  pumpfundingmerge.py \
  pumpfundinganalyze.py \
  pumpfundingshadow.py \
  pumpfundinglookupreport.py \
  pumpdeployer.py \
  pumpdeplat.py \
  pumpsameentry.py \
  pumpcompound.py \
  pumpgraphexport.py \
  pumpgraphshadow.py \
  pumpgraphreport.py \
  test_pumpbirthaudit.py \
  test_pumpbirthaudit_batch.py \
  test_pumpbirthaudit_resume.py \
  test_pumpfunding.py \
  test_pumpfunding_batch.py \
  test_pumpfundingmerge.py \
  test_pumpfundinganalyze.py \
  test_pumpfundingshadow.py \
  test_pumpfundinglookupreport.py \
  test_pumpdeployer.py \
  test_pumpdeplat.py \
  test_pumpsameentry.py \
  test_pumpcompound.py \
  test_pumpgraphexport.py \
  test_pumpgraphshadow.py \
  test_pumpgraphreport.py \
  >logs/pumpgraph_ruff.log 2>&1
(
  cd "$ROOT/../../memecoin/engine"
  "$CARGO" fmt --check
  "$CARGO" test --features websocket --bin websocket_probe
) >"$ROOT/logs/pumpgraph_rust.log" 2>&1

echo "[$(date -u +%FT%TZ)] sharded pipeline complete"
