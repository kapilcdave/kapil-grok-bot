#!/bin/bash
# Condense the pumpsnipe raw tapes to a manifest + aggregates, so the 12 GB can be deleted.
#
# Precedent: [[data-archive-manifests]] -- 53.85 GB was deleted 2026-09-03 and "the manifests are
# what survives". Same pattern here.
#
# Design constraints on this box: 964 MB RAM and 2 shared vCPU, with a live forward collector on it.
# So: never load a 1.4 GB file (one swapped this class of box before), use awk/grep C scans rather
# than per-line Python over 12 GB, sample for distributions, and run the whole thing under `nice`
# so the collector keeps its CPU.
#
# EXACT vs SAMPLED is labelled in the output. Row counts and event-type counts are exact; value
# distributions are from a 1-in-997 systematic sample.
#
# Usage: bash pumpsnipe_condense.sh > manifest.txt
set -u
D=~/pumpsnipe/data
OUT=~/pumpsnipe_condensed
mkdir -p "$OUT/samples"

echo "# pumpsnipe tape manifest"
echo "# generated $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname)"
echo "# counts EXACT (full scan); distributions SAMPLED 1-in-997"
echo "# NOTE: no sha256 recorded -- CPU was reserved for the live dlmmfwd collector. Integrity"
echo "#       record is (bytes, rows, first row, last row) per tape."
echo

TOT_ROWS=0; TOT_BYTES=0; TOT_CREATE=0; TOT_META=0

for f in "$D"/tape-*.jsonl; do
  b=$(basename "$f")
  bytes=$(stat -c %s "$f")
  s="$OUT/samples/${b%.jsonl}.sample.jsonl"

  read -r rows creates metas < <(LC_ALL=C nice -n 19 awk -v S="$s" '
    {n++}
    index($0,"\"t\":\"create\""){c++}
    index($0,"\"t\":\"meta\""){m++}
    n%997==0{print > S}
    END{printf "%d %d %d\n", n, c, (m+0)}
  ' "$f")

  first=$(head -c 400 "$f")
  last=$(tail -n 1 "$f" | head -c 400)

  echo "## $b"
  echo "  bytes       : $bytes"
  echo "  rows        : $rows   (exact)"
  echo "  create rows : $creates (exact)"
  echo "  meta rows   : $metas   (exact)"
  echo "  trade rows  : $((rows - creates - metas)) (derived)"
  echo "  first       : $first"
  echo "  last        : $last"
  echo

  TOT_ROWS=$((TOT_ROWS+rows)); TOT_BYTES=$((TOT_BYTES+bytes))
  TOT_CREATE=$((TOT_CREATE+creates)); TOT_META=$((TOT_META+metas))
done

echo "## TOTALS"
echo "  tapes       : $(ls -1 "$D"/tape-*.jsonl | wc -l)"
echo "  bytes       : $TOT_BYTES"
echo "  rows        : $TOT_ROWS"
echo "  create rows : $TOT_CREATE"
echo "  meta rows   : $TOT_META"
echo "  trade rows  : $((TOT_ROWS - TOT_CREATE - TOT_META))"
echo "  sample rows : $(cat "$OUT"/samples/*.sample.jsonl 2>/dev/null | wc -l)"
