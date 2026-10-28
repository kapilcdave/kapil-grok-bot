"""Learned pick score: every feature mixed, weights learned from what happened, retrained as data comes in.

Operator, 2026-10-22: "it has to be dynamic, we can't hardcode filters or we miss things." On 10-21 a
launch-bought fresh coin rugged 84% of the time; on 10-22 the same fingerprint was OA (362x), VECTOR
and SHAVCOIN. No single filter survives both days, so the bot learns the mix instead.

    sample   one coin at its decision time (`enrich` = price-led path, `nenrich` = news path)
    x        `vector()`: price, liquidity and flow, launch shape, dev wallet, first candles, organic flow,
             SOL fees and tips paid, story / Jev readings, look-alike ranks, hour of day
    y        did the price-driven exit (`exits.Ladder`, entry = first snapshot within 3 min) end in profit
    model    L2 logistic regression, pure Python (the box has 1 GB), standardised features
    pick     score >= the 80th percentile of the scores the model gave the previous 48 h of coins:
             the threshold moves with the market instead of being fixed

    python -m runners.model train  MODEL.json 'data/runners_*.jsonl*'
    python -m runners.model walk   'data/runners_*.jsonl*'          # walk-forward by UTC day
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import time
from collections import defaultdict

from . import exits, rule, score

PICK_Q = 0.80
L2, EPOCHS, LR = 1.0, 400, 0.1


def _f(x, default=0.0) -> float:
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def _log(x: float) -> float:
    return math.log10(max(x, 0) + 1)


def vector(admit: dict, enr: dict, path: str) -> dict[str, float]:
    f = enr.get("features") or {}
    lc, cd, dv, fl, fe, kd, tx, po = (f.get(k) or {} for k in ("launch", "candles", "dev", "flow", "fees", "kind", "text", "post"))
    h1 = fl.get("1h") or {}
    v: dict[str, float] = {"news_path": float(path == "news")}
    mc = _f(admit.get("mc"))
    v["log_mc"] = _log(mc)
    v["log_liq"] = _log(_f(admit.get("liq")))
    v["liq_unknown"] = float(_f(admit.get("liq")) < 100)
    v["v1h_per_mc"] = min(_f(admit.get("v1h")) / mc, 20) if mc else 0
    v["buy_sell_1h"] = math.log((_f(admit.get("b1h")) + 1) / (_f(admit.get("s1h")) + 1))
    v["chg_5m"] = max(min(_f(admit.get("c5")) / 100, 10), -1)
    v["chg_1h"] = max(min(_f(admit.get("c1h")) / 100, 50), -1)
    created = _f(lc.get("created"))
    v["has_launch"] = float(bool(created))
    v["log_age_h"] = _log((admit["ts"] - created) / 3600) if created else 0
    g = lc.get("grad_lag_s")
    v["grad_at_launch"] = float(g == 0)
    v["log_grad_min"] = _log(_f(g) / 60) if g else 0
    v["not_graduated"] = float(bool(created) and lc.get("graduated") is None)
    pad = (lc.get("launchpad") or "").lower()
    v["pad_pump"], v["pad_dbc"] = float(pad == "pump.fun"), float("dbc" in pad)
    v["log_dev_mints"] = _log(_f(lc.get("dev_mints")))
    v["has_dev"] = float("dev_txs" in dv)
    v["log_dev_sol"] = _log(_f(dv.get("dev_buy_sol")))
    sup = _f(lc.get("supply"), 1e9) or 1e9
    v["dev_pct"] = _f(dv.get("dev_buy_tokens")) / sup
    v["dev_moved"] = float(_f(dv.get("dev_moved_10m")) > 0)
    v["dev_sold"] = float(_f(dv.get("dev_sold_10m")) > 0)
    v["log_dev_wallet_h"] = _log(_f(dv.get("dev_funded_s_before")) / 3600)
    v["has_candles"] = float(bool(cd.get("mc_first_hi")))
    v["log_mc_first"] = _log(_f(cd.get("mc_first_hi")))
    v["first_5m_ratio"] = math.log((_f(cd.get("mc_5m")) + 1e3) / (_f(cd.get("mc_first")) + 1e3))
    v["has_flow"] = float(bool(fl))
    v["organic"] = _f(fl.get("organic_score")) / 100
    tr = _f(h1.get("traders"))
    v["org_buyer_share"] = _f(h1.get("org_buyers")) / tr if tr else 0
    v["org_buy_share"] = _f(h1.get("org_buy_share"))
    v["net_buyer_share"] = _f(h1.get("net_buyers")) / tr if tr else 0
    v["log_traders_1h"] = _log(tr)
    v["log_holders"] = _log(_f(fl.get("holders")))
    v["holder_chg_1h"] = max(min(_f(h1.get("holder_chg")) / 100, 10), -1)
    v["top_holders"] = _f(fl.get("top_holders_pct")) / 100
    v["dev_hold_pct"] = _f(fl.get("dev_pct")) / 100
    v["sus"] = float(bool(fl.get("sus")))
    v["has_fees"] = float(bool(fe.get("n")))
    v["fee_paid_share"] = _f(fe.get("paid_share"))
    v["log_tip_mSOL"] = _log(_f(fe.get("tip_sol")) * 1000)
    v["log_prio_uSOL"] = _log(_f(fe.get("prio_sol")) * 1e6)
    v["log_fees_1h"] = _log(_f(fe.get("est_1h_sol")))
    st = enr.get("story") or {}
    v["x_post"] = float(bool(st.get("post_url")))
    pt = _f(po.get("post_ts"))
    v["coin_after_post"] = float(bool(pt and created and created >= pt - 300))
    for k in ("charity", "prediction_market", "celebrity_post", "real_world_news", "viral_animal_or_person",
              "crypto_meta", "pure_meme"):
        v[f"kind_{k}"] = float(kd.get("kind") == k)
    v["jev_mainstream"] = _f(kd.get("mainstream"))
    v["jev_post_names_coin"] = _f(kd.get("post_names_coin"))
    v["jev_news_event"] = _f((enr.get("judge") or {}).get("news_event"))
    nj = enr.get("news_judge") or {}
    v["jev_named_after_story"] = _f(nj.get("named_after_story"))
    v["jev_fresh_event"] = _f(nj.get("fresh_event"))
    v["polymarket"], v["charity_words"] = float(bool(tx.get("polymarket"))), float(bool(tx.get("charity")))
    v["has_site"], v["has_tg"] = float(bool(tx.get("has_site"))), float(bool(tx.get("has_tg")))
    if path == "news":
        r = enr.get("news_ranks") or {}
        v["rank_mc1"], v["rank_t1"], v["log_rivals"] = float(r.get("mc_rank") == 1), float(r.get("t_rank") == 1), _log(_f(r.get("n_rivals")))
    else:
        r = rule.original_ranks(admit, enr)
        v["rank_mc1"], v["rank_t1"], v["log_rivals"] = float(r.get("mc_rank") == 1), float(r.get("t_rank") == 1), _log(_f(r.get("n_sib")))
    v["top10_gt"] = _f((enr.get("holders") or {}).get("top10"))
    hr = time.gmtime(admit["ts"]).tm_hour + time.gmtime(admit["ts"]).tm_min / 60
    v["hour_sin"], v["hour_cos"] = math.sin(2 * math.pi * hr / 24), math.cos(2 * math.pi * hr / 24)
    return v


def samples(pattern: str) -> list[dict]:
    """Stream the tapes once: each decision (enrich / nenrich) with its features, then its Ladder fed by
    the snapshots that follow. Memory is one Ladder per open coin."""
    admits: dict[tuple[str, str], dict] = {}
    open_: dict[str, list[dict]] = defaultdict(list)
    out: list[dict] = []
    for r in score.rows(pattern):
        t, m = r.get("t"), r.get("mint", "")
        if t in ("admit", "nadmit"):
            admits.setdefault((m, "news" if t == "nadmit" else "price"), r)
        elif t in ("enrich", "nenrich"):
            path = "news" if t == "nenrich" else "price"
            a = admits.get((m, path))
            if not a or any(s["path"] == path for s in open_[m]):
                continue
            try:
                x = vector(a, r, path)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            s = {"mint": m, "path": path, "ts": r["ts"], "x": x, "lad": None, "pin": score.Pin(), "symbol": a.get("symbol", "")}
            open_[m].append(s)
            out.append(s)
        elif t == "snap" and r.get("px"):
            for s in open_.get(m, ()):
                if s["lad"] is None:
                    if s["ts"] <= r["ts"] <= s["ts"] + score.ENTRY_WINDOW_S:
                        s["pin"].feed(r)
                        s["lad"] = exits.Ladder(r["px"], r["ts"], r.get("liq") or 0)
                elif not s["lad"].done:
                    for q in s["pin"].feed(r):          # one pool only (score.Pin)
                        s["lad"].step(q["ts"], q["px"], q.get("liq") or 0)
        elif t == "gone":
            for s in open_.get(m, ()):
                if s["lad"] is not None:
                    s["lad"].gone(r["ts"])
    for s in out:
        lad = s.pop("lad")
        s.pop("pin")
        s["done"] = bool(lad and lad.done)
        s["closed_ts"] = lad.closed_ts if lad else None
        s["net"] = lad.net() if lad else None
        s["max_mult"] = lad.max_mult if lad else None
        s["entered"] = lad is not None
    return [s for s in out if s["entered"]]


class Model:
    def __init__(self, names: list[str], mu: list[float], sd: list[float], w: list[float], b: float, thr: float, meta: dict):
        self.names, self.mu, self.sd, self.w, self.b, self.thr, self.meta = names, mu, sd, w, b, thr, meta

    def score(self, x: dict[str, float]) -> float:
        z = self.b + sum(wi * ((x.get(n, 0.0) - m) / s) for n, m, s, wi in zip(self.names, self.mu, self.sd, self.w))
        return 1 / (1 + math.exp(-max(min(z, 30), -30)))

    def dump(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.__dict__, fh)

    @classmethod
    def load(cls, path: str) -> "Model":
        with open(path) as fh:
            return cls(**json.load(fh))

    def top(self, k: int = 12) -> list[tuple[str, float]]:
        return sorted(zip(self.names, self.w), key=lambda nw: -abs(nw[1]))[:k]


def fit(rows: list[dict], l2: float = L2, epochs: int = EPOCHS, lr: float = LR) -> Model:
    names = sorted({k for r in rows for k in r["x"]})
    X = [[r["x"].get(n, 0.0) for n in names] for r in rows]
    y = [1.0 if r["net"] > 0 else 0.0 for r in rows]
    mu = [statistics.fmean(col) for col in zip(*X)]
    sd = [statistics.pstdev(col) or 1.0 for col in zip(*X)]
    Z = [[(v - m) / s for v, m, s in zip(row, mu, sd)] for row in X]
    w, b, n = [0.0] * len(names), math.log((sum(y) + 1) / (len(y) - sum(y) + 1)), len(Z)
    for _ in range(epochs):
        gw, gb = [0.0] * len(w), 0.0
        for zi, yi in zip(Z, y):
            p = 1 / (1 + math.exp(-max(min(b + sum(a * c for a, c in zip(w, zi)), 30), -30)))
            e = p - yi
            gb += e
            for j, zj in enumerate(zi):
                gw[j] += e * zj
        b -= lr * gb / n
        w = [wj - lr * (gj / n + l2 * wj / n) for wj, gj in zip(w, gw)]
    m = Model(names, mu, sd, w, b, 0.0, {"n": n, "pos": sum(y), "trained": time.time()})
    recent = [r for r in rows if r["ts"] >= max(r2["ts"] for r2 in rows) - 48 * 3600]
    sc = sorted(m.score(r["x"]) for r in recent)
    m.thr = sc[int(PICK_Q * (len(sc) - 1))] if sc else 0.5
    return m


def _line(name: str, xs: list[float]) -> str:
    if not xs:
        return f"  {name:<22} n=0"
    rng = random.Random(5)
    bs = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(2000)) if len(xs) >= 5 else []
    ci = f"CI [{bs[50]:+.1%},{bs[1949]:+.1%}]" if bs else ""
    return (f"  {name:<22} n={len(xs):<4} mean {statistics.fmean(xs):+7.1%} {ci:<22} median {statistics.median(xs):+7.1%} "
            f"win {sum(x > 0 for x in xs) / len(xs):5.1%}")


def walk(pattern: str) -> None:
    rows = [r for r in samples(pattern) if r["done"]]
    days = sorted({time.strftime("%Y-%m-%d", time.gmtime(r["ts"])) for r in rows})
    print(f"{len(rows)} closed samples over {len(days)} days; ladder exit")
    picks, allc = [], []
    for d in days[1:]:
        t0 = time.mktime(time.strptime(d, "%Y-%m-%d")) - time.timezone
        train = [r for r in rows if (r["closed_ts"] or 1e18) < t0]       # outcomes known before the day
        test = [r for r in rows if t0 <= r["ts"] < t0 + 86400]
        if len(train) < 50 or not test:
            continue
        m = fit(train)
        p = [r for r in test if m.score(r["x"]) >= m.thr]
        print(f"{d}: train {len(train)}, test {len(test)}, thr {m.thr:.2f}")
        print(_line("model picks", [r["net"] for r in p]))
        print(_line("all coins (control)", [r["net"] for r in test]))
        picks += [r["net"] for r in p]
        allc += [r["net"] for r in test]
    print("\nall test days")
    print(_line("model picks", picks))
    print(_line("all coins (control)", allc))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("train", "walk"))
    ap.add_argument("args", nargs="+")
    a = ap.parse_args()
    if a.cmd == "walk":
        walk(a.args[0])
        return
    out, pattern = a.args[0], a.args[1]
    rows = [r for r in samples(pattern) if r["done"]]
    m = fit(rows)
    m.dump(out)
    print(f"trained on {len(rows)} closed samples ({m.meta['pos']:.0f} profitable), pick threshold {m.thr:.3f}")
    for n, wt in m.top():
        print(f"  {n:<24} {wt:+.3f}")


if __name__ == "__main__":
    main()
