#!/usr/bin/env bash
# Supervisor for the runner recorder on the box: restarts it when the watchdog exits it.
#   nohup ~/runners/run_runners.sh > ~/runners/supervisor.log 2>&1 &
# Stop: touch ~/runners/STOP (the recorder exits and so does this loop).
# Optional: TYPESAFE_API_KEY in ~/runners/.env turns on Jev (and the news path of the rule).
set -u
cd "$HOME/runners"
[ -f .env ] && set -a && . ./.env && set +a
mkdir -p data logs
while [ ! -f STOP ]; do
  echo "$(date -u +%FT%TZ) start" >> logs/supervisor.log
  python3 -m runners.record --data-dir data --stop-file STOP >> "logs/record_$(date -u +%Y%m%d).log" 2>&1
  echo "$(date -u +%FT%TZ) exit $?" >> logs/supervisor.log
  [ -f STOP ] || sleep 30
done
