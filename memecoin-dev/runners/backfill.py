"""Collect `features` after the fact for coins already on a tape, for exploration only.

    python -m runners.backfill OUT.jsonl 'data/runners_*.jsonl*' ['archive/runners_*.jsonl*' ...]

Only facts fixed at launch are collected (launch, first-hour candles, dev buy, post time, text, Jev
kind); the organic-flow numbers would be read AFTER the outcome, so they are left out. Resumable: mints
already in OUT are skipped.
"""
from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import features, score


def main() -> None:
    out_path, patterns = Path(sys.argv[1]), sys.argv[2:]
    done = set()
    if out_path.exists():
        done = {json.loads(l)["mint"] for l in out_path.open() if l.strip()}
    coins: dict[str, dict] = {}
    for pat in patterns:
        coins.update(score.load(pat))
    todo = [(m, c) for m, c in coins.items() if m not in done]
    print(f"{len(coins)} coins, {len(todo)} to collect", flush=True)
    lock = threading.Lock()

    def one(item: tuple[str, dict]) -> None:
        m, c = item
        a, e = c["admit"], c["enrich"]
        st = e.get("story") or {}
        heads = [h["title"] for h in st.get("news") or [] if h["ts"] <= a["ts"]]
        try:
            f = features.collect(a, st.get("post_url", ""), st.get("post", ""), heads, at_admission=False)
        except Exception as ex:   # noqa: BLE001 -- one coin must not stop the backfill
            print(f"{m} {type(ex).__name__}: {str(ex)[:120]}", flush=True)
            return
        with lock, out_path.open("a") as fh:
            fh.write(json.dumps({"mint": m, **f}, separators=(",", ":")) + "\n")
            print(f"{a.get('symbol', '')[:10]:<10} pad {f['launch'].get('launchpad')} grad {f['launch'].get('grad_lag_s')} "
                  f"dev {f['dev'].get('dev_buy_sol')} first {f['candles'].get('mc_first_hi')} kind {f['kind'].get('kind')}",
                  flush=True)

    with ThreadPoolExecutor(4) as ex:
        list(ex.map(one, todo))


if __name__ == "__main__":
    main()
