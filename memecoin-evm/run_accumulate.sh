#!/bin/zsh
# Accumulate BSC PancakeSwap-v2 launch tapes until STOP appears or MAXRUNS is hit.
# Scan span is 11200 blocks = 1.40 h of chain at 0.450 s/block, so the interval
# MUST stay under 1.40 h or windows leave gaps (public log retention ~1.2-2.4 h
# means a missed window is lost permanently).
set -u
cd /Users/kapil/proj/memecoin-evm
PY=/usr/bin/python3
INTERVAL=4200          # 1.167 h -> ~0.23 h overlap, no gaps
MAXRUNS=${MAXRUNS:-40} # ~2 days
rm -f STOP
for i in $(seq 1 $MAXRUNS); do
  if [[ -f STOP ]]; then echo "[$(date -u +%FT%TZ)] STOP file present, exiting" >> data/accumulate.log; break; fi
  before=$(wc -l < data/sync_tape.jsonl 2>/dev/null || echo 0)
  $PY -I collect_sync.py >> data/sync.log 2>&1
  after=$(wc -l < data/sync_tape.jsonl 2>/dev/null || echo 0)
  echo "[$(date -u +%FT%TZ)] run $i/$MAXRUNS: $before -> $after launches (+$((after-before)))" >> data/accumulate.log
  [[ -f STOP ]] && break
  sleep $INTERVAL
done
echo "[$(date -u +%FT%TZ)] accumulator finished, total=$(wc -l < data/sync_tape.jsonl)" >> data/accumulate.log
