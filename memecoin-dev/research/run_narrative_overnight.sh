#!/usr/bin/env bash
# Overnight narrative collection on the LAPTOP (never let the only copy of a tape live on a VM).
#   research/run_narrative_overnight.sh [hours]       default 12
# Writes ~/tapes/narr-tape-<stamp>.jsonl (snipefeed: creates, trades, metadata incl. image) and
# ~/tapes/narr-posts-<stamp>.jsonl (text of every linked X post). caffeinate keeps the Mac awake
# (plugged in, lid open). Stop early: touch ~/tapes/narr-posts-<stamp>.jsonl.STOP and kill the feed.
set -euo pipefail
HOURS=${1:-12}
HERE=$(cd "$(dirname "$0")" && pwd)
FEED=$(cd "$HERE/../../crypto/dexarb" && pwd)
mkdir -p ~/tapes
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TAPE=~/tapes/narr-tape-$STAMP.jsonl
POSTS=~/tapes/narr-posts-$STAMP.jsonl
SECS=$(python3 -c "print(int($HOURS*3600))")
cd "$FEED"
nohup caffeinate -ims python3 snipefeed.py --tape "$TAPE" --duration "$SECS" --meta-workers 4 \
  > ~/tapes/narr-feed-$STAMP.log 2>&1 &
echo "feed pid $!"
cd "$HERE"
nohup caffeinate -ims python3 narrposts.py --tape "$TAPE" --out "$POSTS" --hours "$HOURS" \
  > ~/tapes/narr-posts-$STAMP.log 2>&1 &
echo "posts pid $!"
echo "tape  $TAPE"
echo "posts $POSTS"
