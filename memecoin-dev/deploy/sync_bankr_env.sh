#!/usr/bin/env bash
# Assemble the keys the Bankr pipeline needs and merge them into ~/.devbot/.env on the run box.
#   deploy/sync_bankr_env.sh                 # default host: stockholm
#   HOST=stockholm deploy/sync_bankr_env.sh
#
# The keys are split across two machines: this laptop has BANKR_API_KEY and PINATA_JWT, the Iowa
# e2-micro has the model, art and X keys. Values travel in 0600 temp files and are removed on both
# ends; nothing goes through argv, so no secret reaches a process list or shell history. Only key
# names are printed. Re-running replaces the old values and leaves ~/.devbot/.env.bak behind.
set -euo pipefail
HOST=${HOST:-stockholm}
IOWA_NAME=${IOWA_NAME:-instance-20260902-221655}
IOWA_ZONE=${IOWA_ZONE:-us-central1-a}
IOWA_USER=${IOWA_USER:-kapilmcdave}

# Needed to launch at all. Without CLOUDFLARE_* there is no concept and no art.
REQUIRED=(BANKR_API_KEY PINATA_JWT CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN)
# Wanted, but each one degrades gracefully: no X keys means a launch with no tweetUrl, no Telegram
# means no notification, no GROQ/OPENROUTER/POLLINATIONS means fewer model and art fallbacks.
OPTIONAL=(GROQ_API_KEY OPENROUTER_API_KEY POLLINATIONS_TOKEN DEVBOT_MODEL IMAGE_FALLBACK
          X_API_KEY X_API_SECRET X_ACCESS_TOKEN X_ACCESS_SECRET X_HANDLE X_MAX_POSTS_PER_DAY
          DEVBOT_TELEGRAM_TOKEN TELEGRAM_HOME_CHAT)
# Bankr allows 3 launches/wallet/24h and 1/min; 3 a day uses the whole free allowance.
DEFAULTS=(BANKR_MAX_PER_DAY=3 BANKR_GAP_MIN=90 BANKR_CYCLE_MIN=20)

LOCAL_ENV=${LOCAL_ENV:-$HOME/.devbot/.env}
IOWA_ENV=$(mktemp -t bankriowa); OUT=$(mktemp -t bankrout); chmod 600 "$IOWA_ENV" "$OUT"
trap 'rm -f "$IOWA_ENV" "$OUT"' EXIT

echo "pulling the model/art/X keys from the Iowa VM ..."
gcloud compute ssh "$IOWA_USER@$IOWA_NAME" --zone "$IOWA_ZONE" \
  --command 'cat ~/.devbot/.env' >"$IOWA_ENV" 2>/dev/null \
  || echo "  (Iowa unreachable; using this laptop only)"

take() {   # first match wins: laptop, then Iowa
  for src in laptop:"$LOCAL_ENV" iowa:"$IOWA_ENV"; do
    line=$(grep -m1 "^$1=" "${src#*:}" 2>/dev/null || true)
    if [ -n "$line" ]; then printf '%s\n' "$line" >>"$OUT"; echo "${src%%:*}"; return 0; fi
  done
  return 1
}

fail=0
for k in "${REQUIRED[@]}"; do
  src=$(take "$k") && printf '  %-24s <- %s\n' "$k" "$src" \
    || { printf '  %-24s MISSING (required)\n' "$k"; fail=1; }
done
for k in "${OPTIONAL[@]}"; do
  src=$(take "$k") && printf '  %-24s <- %s\n' "$k" "$src" \
    || printf '  %-24s -- (optional, skipped)\n' "$k"
done
[ "$fail" = 0 ] || { echo "FAIL: a required key is missing; nothing was pushed."; exit 1; }
printf '%s\n' "${DEFAULTS[@]}" >>"$OUT"

scp -q "$OUT" "$HOST":/tmp/bankrenv
ssh "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
install -d -m 700 ~/.devbot
touch ~/.devbot/.env && chmod 600 ~/.devbot/.env
cp ~/.devbot/.env ~/.devbot/.env.bak && chmod 600 ~/.devbot/.env.bak
chmod 600 /tmp/bankrenv
NEW=$(mktemp) && chmod 600 "$NEW"
# Drop any existing copy of each incoming key, then append the new values once.
cut -d= -f1 /tmp/bankrenv | sort -u >/tmp/bankrkeys
grep -vE "^($(paste -sd'|' /tmp/bankrkeys))=" ~/.devbot/.env >"$NEW" || true
cat /tmp/bankrenv >>"$NEW"
mv "$NEW" ~/.devbot/.env && chmod 600 ~/.devbot/.env
rm -f /tmp/bankrenv /tmp/bankrkeys
echo "merged: $(cut -d= -f1 ~/.devbot/.env | sort -u | wc -l | tr -d ' ') keys now in ~/.devbot/.env"
REMOTE
