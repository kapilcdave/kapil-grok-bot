"""Net the creator's own bundle out of a launch's early buyers, then measure outside flow vs self-buy.

  python -m research.bundlenet pass1 OUT_wallets.json  TAPE.jsonl.gz [TAPE2...]
  python -m research.bundlenet pass2 OUT_wallets.json  OUT_launches.jsonl  TAPE.jsonl.gz [TAPE2...]

Frozen design in research/PREREG-bundle-netted-dose-response-20261017.md. Pass 1 classifies every
wallet by how many distinct mints it ever traded in the corpus (broad >= 3, mid == 2, single == 1) and
records the feed's gap intervals. Pass 2 scores each eligible launch's first 10 s / 60 s, splitting
early buy SOL into the creator's own (`user`, `creator`, or any single-class wallet) and genuinely
outside (broad wallets only), and keeps the contaminated statistic alongside for comparison.

Pass 1 keeps one packed int a wallet: low 2 bits = distinct-mint count capped at 3, then two 31-bit
mint ids so a third distinct mint can be detected without storing a set.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

STD_V_SOL, STD_V_TOK = 30.0, 1_073_000_000.0
K = STD_V_SOL * STD_V_TOK   # a standard pump.fun curve holds v_sol*v_tok constant; Mayhem rows do not
K_TOL = 1e-6
GAP_S = 3.0          # a feed gap longer than this voids any window overlapping it
EARLY_S = 10.0       # primary window
LATE_S = 60.0        # secondary window
PEAK_S = 600.0       # for the "did the dump beat holding" arm
MASK31 = (1 << 31) - 1


def k_ok(r) -> bool:
    """A trade row is usable only if its reserves satisfy the constant-product invariant.

    The create event of a Mayhem curve reports the standard (30, 1.073e9) pair, so only the trades
    discriminate -- see AMENDMENT 1 in the prereg. 30.8% of corpus rows fail this.
    """
    vs, vt = r.get("v_sol"), r.get("v_tok")
    return bool(vs and vt) and abs(vs * vt / K - 1.0) <= K_TOL


def events(paths):
    for p in paths:
        with gzip.open(p, "rt") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except Exception:
                    continue


def pass1(paths, out: Path) -> int:
    """wallet -> distinct-mint count (capped at 3), plus the feed's gap intervals."""
    mint_id: dict[str, int] = {}
    w: dict[str, int] = {}
    gaps: list[tuple[float, float]] = []
    prev = None
    t_first = t_last = None
    n = 0
    for r in events(paths):
        n += 1
        ts = r.get("recv") or r.get("ts")
        if ts:
            if t_first is None:
                t_first = ts
            if prev is not None and ts - prev > GAP_S:
                gaps.append((prev, ts))
            prev = t_last = ts
        if r.get("t") != "trade" or not k_ok(r):
            continue
        u, m = r.get("user"), r.get("mint")
        if not u or not m:
            continue
        mid = mint_id.get(m)
        if mid is None:
            mid = mint_id[m] = len(mint_id) + 1
        packed = w.get(u, 0)
        cnt = packed & 3
        if cnt >= 3:
            continue
        if cnt == 0:
            w[u] = 1 | (mid << 2)
        elif cnt == 1:
            if (packed >> 2) & MASK31 != mid:
                w[u] = 2 | (packed & (MASK31 << 2)) | (mid << 33)
        else:
            if (packed >> 2) & MASK31 != mid and (packed >> 33) & MASK31 != mid:
                w[u] = 3
        if n % 2_000_000 == 0:
            print(f"  pass1 {n:,} events, {len(w):,} wallets, {len(mint_id):,} mints", flush=True)
    counts = {u: (p & 3) for u, p in w.items()}
    hist = {1: 0, 2: 0, 3: 0}
    for c in counts.values():
        hist[c] += 1
    json.dump({"counts": counts, "gaps": gaps, "t_first": t_first, "t_last": t_last},
              open(out, "w"))
    print(f"pass1 done: {n:,} events, {len(counts):,} wallets {hist}, {len(gaps)} gaps, "
          f"span {(t_last - t_first) / 3600:.2f} h", flush=True)
    return 0


def pass2(wallets: Path, out: Path, paths) -> int:
    blob = json.load(open(wallets))
    cls, gaps = blob["counts"], blob["gaps"]
    t_first, t_last = blob["t_first"], blob["t_last"]

    def gapped(a: float, b: float) -> bool:
        return any(gs < b and ge > a for gs, ge in gaps)

    live: dict[str, dict] = {}
    done = 0
    fh = open(out, "w")

    def flush(mint: str) -> None:
        nonlocal done
        L = live.pop(mint)
        if L["eligible"]:
            L.pop("eligible")
            L["self_wallets"] = len(L.pop("_self"))
            L["outside_wallets"] = len(L.pop("_out"))
            L["naive_buyers"] = len(L.pop("_naive"))
            L["clean_buyers"] = len(L.pop("_clean"))
            L["swarm"] = sum(1 for v in L.pop("_broad60").values() if v >= 0.3) >= 5
            fh.write(json.dumps(L) + "\n")
            done += 1
        else:
            for k in ("_self", "_out", "_naive", "_clean", "_broad60"):
                L.pop(k, None)

    n = 0
    for r in events(paths):
        n += 1
        ts = r.get("recv") or r.get("ts")
        t = r.get("t")
        if t == "create":
            m = r.get("mint")
            if not m:
                continue
            ok = (r.get("v_sol") == STD_V_SOL and r.get("v_tok") == STD_V_TOK
                  and ts is not None and ts + LATE_S <= t_last and not gapped(ts, ts + LATE_S))
            live[m] = {"mint": m, "creator": r.get("creator"), "user": r.get("user"),
                       "ts": ts, "slot": r.get("slot"), "name": r.get("name"), "symbol": r.get("symbol"),
                       "eligible": ok, "peak_complete": bool(ts is not None and ts + PEAK_S <= t_last),
                       "self_sol": 0.0, "outside_sol": 0.0, "mid_sol": 0.0, "naive_sol": 0.0,
                       "self_sol60": 0.0, "outside_sol60": 0.0,
                       "net_curve": 0.0, "net_curve60": 0.0,
                       "dev_sell_sol": 0.0, "dev_sell_tok": 0.0, "px_peak": 0.0, "px_last": 0.0,
                       "n_trades": 0, "n_bad": 0,
                       "dev_sol_slot0": 0.0, "outside_sol_after0": 0.0, "early": [],
                       "dev_buy_sol60": 0.0, "dev_buy_tok60": 0.0,
                       "dev_buy_sol600": 0.0, "dev_buy_tok600": 0.0,
                       "dev_sell_sol600": 0.0, "dev_sell_tok600": 0.0,
                       "v_sol_last": STD_V_SOL, "v_tok_last": STD_V_TOK, "n_other600": 0,
                       "_self": set(), "_out": set(), "_naive": set(), "_clean": set(), "_broad60": {}}
        elif t == "trade":
            m = r.get("mint")
            L = live.get(m)
            if L is None:
                continue
            dt = (ts or L["ts"]) - L["ts"]
            if dt > PEAK_S:
                flush(m)
                continue
            if dt <= LATE_S:
                L["n_trades"] += 1
                if not k_ok(r):
                    L["n_bad"] += 1
            if not k_ok(r):
                continue
            u, side, sol = r.get("user"), r.get("side"), float(r.get("sol") or 0.0)
            tok = float(r.get("tok") or 0.0)
            px = (sol / tok) if tok else 0.0
            if px > L["px_peak"]:
                L["px_peak"] = px
            L["px_last"] = px
            if r.get("v_sol") is not None and dt <= LATE_S:
                L["net_curve60"] = r["v_sol"] - STD_V_SOL
                if dt <= EARLY_S:
                    L["net_curve"] = r["v_sol"] - STD_V_SOL
            L["v_sol_last"], L["v_tok_last"] = r["v_sol"], r["v_tok"]
            own = u in (L["user"], L["creator"])
            if not own:
                L["n_other600"] += 1
            c = cls.get(u, 0)
            if own:
                key = "sell" if side == "sell" else "buy"
                L[f"dev_{key}_sol600"] += sol
                L[f"dev_{key}_tok600"] += tok
                if dt <= LATE_S:
                    if side == "sell":
                        L["dev_sell_sol"] += sol
                        L["dev_sell_tok"] += tok
                    else:
                        L["dev_buy_sol60"] += sol
                        L["dev_buy_tok60"] += tok
            if side != "buy":
                continue
            is_self = own or c == 1
            if dt <= LATE_S:
                if c == 3 and not own:
                    L["_broad60"][u] = L["_broad60"].get(u, 0.0) + sol
                    L["outside_sol60"] += sol
                if is_self:
                    L["self_sol60"] += sol
            if dt <= EARLY_S:
                in_slot0 = r.get("slot") is not None and L["slot"] is not None and r["slot"] <= L["slot"]
                if own and in_slot0:
                    L["dev_sol_slot0"] += sol
                if c == 3 and not own and not in_slot0:
                    L["outside_sol_after0"] += sol
                if not own:
                    L["early"].append([u, round(sol, 6), c, 0 if in_slot0 else 1])
                    L["_naive"].add(u)
                    L["naive_sol"] += sol
                if is_self:
                    L["self_sol"] += sol
                    L["_self"].add(u)
                elif c == 3:
                    L["outside_sol"] += sol
                    L["_out"].add(u)
                    L["_clean"].add(u)
                else:
                    L["mid_sol"] += sol
        if n % 2_000_000 == 0:
            print(f"  pass2 {n:,} events, {done:,} launches written, {len(live):,} live", flush=True)
    for m in list(live):
        flush(m)
    fh.close()
    print(f"pass2 done: {n:,} events, {done:,} eligible launches -> {out}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["pass1", "pass2"])
    ap.add_argument("args", nargs="+")
    a = ap.parse_args()
    if a.mode == "pass1":
        return pass1(a.args[1:], Path(a.args[0]))
    return pass2(Path(a.args[0]), Path(a.args[1]), a.args[2:])


if __name__ == "__main__":
    sys.exit(main())
