#!/usr/bin/env bash
# Can this box reach Bankr's deploy endpoint at all? Run it before moving the unit to a new host.
#   deploy/probe_bankr.sh stockholm laptop
#
# Bankr is behind an AWS WAF. A blocked box answers POST /token-launches/deploy with 403 and
# "server: awselb/2.0" + text/html, while its GETs still return 200 — so a GET check proves nothing.
# A box that passes the WAF answers 401 to the dummy key below. No real key is used.
set -uo pipefail
BODY='{"tokenName":"Probe","tokenSymbol":"PRB","chain":"base","provider":"doppler","simulateOnly":true}'
PROBE='printf "  egress %s\n" "$(curl -s --max-time 25 https://api.bankr.bot/token-launches -o /dev/null -w %{http_code}) GET /token-launches"
code=$(curl -s --max-time 25 -o /dev/null -w "%{http_code}" -X POST https://api.bankr.bot/token-launches/deploy -H "content-type: application/json" -H "x-api-key: probe" -d '"'$BODY'"')
case "$code" in
  401|403) :;;
esac
printf "  %s POST /token-launches/deploy -> " "$(curl -s --max-time 15 https://api.ipify.org)"
if [ "$code" = 403 ]; then echo "403 WAF-BLOCKED (do not run the unit here)"; else echo "$code (passes the WAF)"; fi'

for host in "${@:-stockholm laptop}"; do
  echo "=== $host ==="
  if [ "$host" = laptop ]; then bash -c "$PROBE"; else ssh -o ConnectTimeout=15 "$host" "$PROBE"; fi
done
