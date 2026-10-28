#!/usr/bin/env bash

set -euo pipefail

mkdir -p logs data

BATCH_SIZE="${PUMPGRAPH_BATCH_SIZE:-40}"
RPC_PAUSE="${PUMPGRAPH_RPC_PAUSE:-0.5}"

echo "[$(date -u +%FT%TZ)] birth verification"
python3 pumpbirthaudit_batch.py \
  --all \
  --batch-size "$BATCH_SIZE" \
  --older-limit 10 \
  --pause "$RPC_PAUSE" \
  2>&1 | tee logs/pumpbirthaudit_full.log

echo "[$(date -u +%FT%TZ)] verified same-slot base rate"
python3 pumpsameentry.py \
  --birth-audit data/pumpbirthaudit.jsonl \
  --clip 0.05 \
  --json data/pumpsameentry_verified.json \
  2>&1 | tee logs/pumpsameentry_verified.log

echo "[$(date -u +%FT%TZ)] verified direct-creator persistence"
python3 pumpdeployer.py \
  --birth-audit data/pumpbirthaudit.jsonl \
  --clip 0.05 \
  --json data/pumpdeployer_verified.json \
  2>&1 | tee logs/pumpdeployer_verified.log

echo "[$(date -u +%FT%TZ)] verified latency reachability"
python3 pumpdeplat.py \
  --birth-audit data/pumpbirthaudit.jsonl \
  --json data/pumpdeplat_verified.json \
  2>&1 | tee logs/pumpdeplat_verified.log

echo "[$(date -u +%FT%TZ)] direct funding edges"
python3 pumpfunding_batch.py \
  --all \
  --history-limit 50 \
  --batch-size "$BATCH_SIZE" \
  --pause "$RPC_PAUSE" \
  2>&1 | tee logs/pumpfunding_full.log

echo "[$(date -u +%FT%TZ)] funding graph tests"
python3 pumpfundinganalyze.py \
  --json data/pumpfundinganalyze.json \
  2>&1 | tee logs/pumpfundinganalyze.log

echo "[$(date -u +%FT%TZ)] live funding-lookup feasibility gate"
python3 pumpfundinglookupreport.py \
  --json data/pumpfundinglookup_live.json \
  2>&1 | tee logs/pumpfundinglookupreport.log

echo "[$(date -u +%FT%TZ)] fail-closed live score export"
python3 pumpgraphexport.py \
  --output data/pumpgraph_scores.jsonl \
  --metadata data/pumpgraph_scores_meta.json \
  2>&1 | tee logs/pumpgraphexport.log

echo "[$(date -u +%FT%TZ)] concurrency-aware paper compounding, no tip"
python3 pumpcompound.py \
  --graph-gated \
  --json data/pumpcompound_zero_tip.json \
  2>&1 | tee logs/pumpcompound_zero_tip.log

echo "[$(date -u +%FT%TZ)] concurrency-aware paper compounding, 0.001 SOL roundtrip tip"
python3 pumpcompound.py \
  --graph-gated \
  --roundtrip-tip-sol 0.001 \
  --json data/pumpcompound_tip_001.json \
  2>&1 | tee logs/pumpcompound_tip_001.log

echo "[$(date -u +%FT%TZ)] final go/no-go report"
python3 pumpgraphreport.py \
  2>&1 | tee logs/pumpgraphreport.log

echo "[$(date -u +%FT%TZ)] pipeline complete"
