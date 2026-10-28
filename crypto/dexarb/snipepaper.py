"""Paper broker over a `snipefeed` tape. No keypair, no builder, no signer.

Capital-disabled by construction, in the style of `pumpgraphshadow.py`: there is
no RPC client and no transaction path anywhere in this file, and every ledger row
carries `capital_enabled: false`.

The same code runs live (`--follow`) and over a saved tape, and must produce the
same ledger either way. That is what makes the turnover-matched null in
`snipescore.py` computable at all, because the null does not exist until the run
is over and so cannot be evaluated by a collector that decides as it goes.

Per gate-passing candidate it writes ONE ledger row holding every arm's verdict
and ONE set of fill economics. The arms differ only in whether they accept, so
the comparison is paired on identical rows and the model's marginal contribution
is identified rather than inferred from two separate runs
(`an-oos-test-needs-a-reproduction-arm`).

    python3 snipepaper.py --tape tape.jsonl --ledger ledger.jsonl            # replay
    python3 snipepaper.py --tape tape.jsonl --ledger ledger.jsonl --follow    # live

With `--wallet`, every gate-passing row also gets the wallet-history arm
(`snipewallet.py`): the finder's paragraph goes to the same decider, the verdict
is `arms.gate_wallet_decide`, and the row carries a SECOND set of fill economics
(`entry_reach` / `marks_reach`) priced at the curve state when that verdict would
actually exist. Every gate-passing row gets the reach economics, so the wallet arm
and its roll null are still paired on identical rows. `--wallet-cache` records
each row's research so a replay reproduces the live ledger without RPC.
"""
import argparse
import collections
import json
import os
import sys
import time
import urllib.error
import urllib.request

import snipewallet as W
from snipedecode import buy_quote, curve_price, sell_quote

SCHEMA = "snipepaper-2"

# Frozen with the preregistration: these are the thresholds in the public guide
# the operator asked to reproduce. They are NOT fitted here, and fitting them on
# this tape would make every number downstream in-sample
# (`in-sample-controls-cannot-detect-selection`).
GATE = {
    "min_uniq_wallets": 8,
    "min_buy_ratio": 0.60,
    "min_net_sol": 0.30,
    "max_ssr": 0.20,
    "min_trades": 12,
    "min_mcap_sol": 30.0,
    "max_mcap_sol": 250.0,
    "max_creator_initial_buy_sol": 3.0,
    "reject_creator_sold": True,
}

HORIZONS = (15, 30, 60, 120, 300, 600)
PRIMARY_HORIZON = 60            # declared in the prereg before any row existed
TOTAL_SUPPLY = 1_000_000_000.0  # pump.fun mints 1e9 whole tokens
STANDARD_V_SOL = 30.0           # the canonical opening SOL reserve


def mcap_sol(v_sol, v_tok):
    price = curve_price(v_sol, v_tok)
    return (price * TOTAL_SUPPLY) if price else 0.0


def gate_verdict(m, gate=GATE):
    """(passed, [every failing reason]).

    Every reason, not the first: a single-reason verdict makes the pass rate
    unattributable, and the pass rate is what caps the sample
    (`a-screen-pass-rate-caps-the-sample-before-throughput-does`).
    """
    reasons = []
    if m["uniq_wallets"] < gate["min_uniq_wallets"]:
        reasons.append("uniq_wallets")
    if m["buy_ratio"] < gate["min_buy_ratio"]:
        reasons.append("buy_ratio")
    if m["net_sol"] < gate["min_net_sol"]:
        reasons.append("net_sol")
    # SSR is undefined with no buy volume. None must FAIL, never pass: letting
    # "no buys" read as "no selling" inverts the test
    # (`an-empty-result-must-not-become-the-sentinels-opposite`).
    if m["ssr"] is None or m["ssr"] > gate["max_ssr"]:
        reasons.append("ssr")
    if m["trades"] < gate["min_trades"]:
        reasons.append("trades")
    if not (gate["min_mcap_sol"] <= m["mcap_sol"] <= gate["max_mcap_sol"]):
        reasons.append("mcap_sol")
    if m["creator_initial_buy_sol"] > gate["max_creator_initial_buy_sol"]:
        reasons.append("creator_initial_buy")
    if gate["reject_creator_sold"] and m["creator_sold_in_window"]:
        reasons.append("creator_sold")
    return (not reasons), reasons


class Watch:
    """One mint's rows. Metrics are a pure function of the windowed rows, so a
    replay over a longer tape gives the same candidate as the live run did."""

    def __init__(self, create):
        self.create = create
        self.mint = create["mint"]
        self.t0 = create["ts"]
        self.recv0 = create.get("recv") or create["ts"]
        self.meta = None
        self.trades = []        # dicts: ts, side, sol, tok, user, v_sol, v_tok
        self.emitted = False
        self.position = None

    def on_trade(self, row):
        self.trades.append({k: row.get(k) for k in
                            ("ts", "side", "sol", "tok", "user",
                             "v_sol", "v_tok")})

    def metrics(self, window):
        rows = [t for t in self.trades if t["ts"] - self.t0 < window]
        wallets = set()
        buys = sells = 0
        buy_sol = sell_sol = 0.0
        creator_buy_sol = 0.0
        creator_sold = False
        creator = self.create.get("creator")
        v_sol, v_tok = self.create["v_sol"], self.create["v_tok"]
        for t in rows:
            if t["user"]:
                wallets.add(t["user"])
            if t["side"] == "buy":
                buys += 1
                buy_sol += t["sol"]
                if t["user"] and t["user"] == creator:
                    creator_buy_sol += t["sol"]
            else:
                sells += 1
                sell_sol += t["sol"]
                if t["user"] and t["user"] == creator:
                    creator_sold = True
            v_sol, v_tok = t["v_sol"], t["v_tok"]
        trades = buys + sells
        return {
            "trades": trades, "uniq_wallets": len(wallets),
            "buys": buys, "sells": sells,
            "buy_ratio": (buys / trades) if trades else 0.0,
            "net_sol": buy_sol - sell_sol,
            "buy_sol": buy_sol, "sell_sol": sell_sol,
            "ssr": (sell_sol / buy_sol) if buy_sol > 0 else None,
            "mcap_sol": mcap_sol(v_sol, v_tok),
            "creator_initial_buy_sol": creator_buy_sol,
            "creator_sold_in_window": creator_sold,
            "price_sol": curve_price(v_sol, v_tok),
            "v_sol": v_sol, "v_tok": v_tok,
        }

    def text_payload(self):
        meta = self.meta or {}
        return {"name": self.create.get("name"),
                "symbol": self.create.get("symbol"),
                "description": meta.get("description") or "",
                "twitter": meta.get("twitter") or "",
                "website": meta.get("website") or "",
                "telegram": meta.get("telegram") or ""}


class Decider:
    """HTTP client for `snipedecide`. Fails CLOSED and says why.

    `--decider-url` absent means the arm is recorded as unavailable rather than
    silently accepted: a missing instrument is a void row, not a buy.
    """

    def __init__(self, url, token, timeout):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.counts = collections.Counter()

    def decide(self, payload):
        if not self.url:
            self.counts["no_url"] += 1
            return {"available": False, "accept": False,
                    "reason": "decider_not_configured"}
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(self.url + "/decide", data=body,
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out = json.loads(resp.read())
            out["available"] = True
            out["rtt_ms"] = round((time.monotonic() - t0) * 1e3, 1)
            self.counts["ok"] += 1
            return out
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:400].decode("utf-8", "replace")
            # 503 is the degeneracy gate refusing to serve. That is a VOID
            # instrument, and it must be visible in the ledger as such.
            reason = ("decider_degenerate" if exc.code == 503
                      else f"decider_http_{exc.code}")
            self.counts[reason] += 1
            return {"available": False, "accept": False, "reason": reason,
                    "detail": detail,
                    "rtt_ms": round((time.monotonic() - t0) * 1e3, 1)}
        except Exception as exc:                            # noqa: BLE001
            reason = "decider_" + type(exc).__name__
            self.counts[reason] += 1
            return {"available": False, "accept": False, "reason": reason,
                    "detail": str(exc)[:200],
                    "rtt_ms": round((time.monotonic() - t0) * 1e3, 1)}


def price_entry(v_sol, v_tok, clip_sol, fee_bps, gas_sol):
    """Buy `clip_sol` through the constant product, charging our own impact.

    Nothing is netted: the fee comes off the SOL that reaches the curve, the
    curve charges impact at the clip (`an-activity-gate-is-a-depth-gate`), and
    gas is a separate SOL cost that the position never recovers.
    """
    fee = clip_sol * fee_bps / 1e4
    to_curve = clip_sol - fee
    tokens, avg = buy_quote(v_sol, v_tok, to_curve)
    if tokens is None:
        return None
    return {"clip_sol": clip_sol, "fee_sol": fee, "gas_sol": gas_sol,
            "sol_to_curve": to_curve, "tokens": tokens, "avg_price": avg,
            "cost_sol": clip_sol + gas_sol,
            "v_sol_at_entry": v_sol, "v_tok_at_entry": v_tok,
            "mid_price_at_entry": curve_price(v_sol, v_tok)}


def price_exit(entry, v_sol, v_tok, fee_bps, gas_sol):
    """Sell the whole position back through the curve at a later state."""
    sol_out, avg = sell_quote(v_sol, v_tok, entry["tokens"])
    if sol_out is None:
        return None
    fee = sol_out * fee_bps / 1e4
    net = sol_out - fee - gas_sol
    return {"sol_gross": sol_out, "fee_sol": fee, "gas_sol": gas_sol,
            "sol_net": net, "avg_price": avg,
            "pnl_sol": net - entry["cost_sol"],
            "ret": (net - entry["cost_sol"]) / entry["cost_sol"],
            "v_sol": v_sol, "v_tok": v_tok}


def state_at(watch, entry_index, when, entry_state):
    """The curve's STANDING state at `when`: the last print at or before it.

    On a bonding curve the standing state *is* the fill price -- reserves are a
    deterministic function and a sell at time `when` executes against whatever
    the last trade left behind, with no counterparty required. So the last print
    at-or-before the horizon is a reachable exit, and it is causal: nothing after
    `when` is read, and no print is chosen by outcome.

    Taking the FIRST print at-or-after instead (the correct rule on an order
    book, where a quote is not a fill) returns None whenever the token went
    quiet, which silently deletes exactly the rows where a position was stuck --
    the outcome that matters most. With no print at all since entry the state is
    the entry state, i.e. the position costs its two fees and its own impact.
    """
    state = entry_state
    for t in watch.trades[entry_index:]:
        if t["ts"] > when:
            break
        state = t
    return state


class WalletCtx:
    """Finder, tape registry and research cache for the wallet arm."""

    def __init__(self, finder, registry, cache_path=None):
        self.finder = finder
        self.registry = registry
        self.cache = {}
        self.handle = None
        if cache_path:
            if os.path.exists(cache_path):
                for line in open(cache_path):
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self.cache[item["mint"]] = item["out"]
            self.handle = open(cache_path, "a", buffering=1)

    def remember(self, mint, out):
        self.cache[mint] = out
        if self.handle:
            self.handle.write(json.dumps({"mint": mint, "out": out},
                                         separators=(",", ":")) + "\n")


def wallet_arm(watch, decider, window, ctx, follow):
    """Research + verdict for one candidate, and how long until it existed."""
    cached = ctx.cache.get(watch.mint)
    if cached is not None:
        return cached
    if ctx.finder is None:
        return {"wallet_decider_available": False,
                "wallet_decider": {"reason": "wallet_not_in_cache"}}
    t_close = watch.t0 + window
    rows = [t for t in watch.trades if t["ts"] - watch.t0 < window]
    started = time.monotonic()
    feats = ctx.finder.research(watch.create, rows, ctx.registry, t_close)
    verdict = decider.decide({"text": feats["text"]})
    work_s = time.monotonic() - started
    # Live, the verdict exists NOW on the wall clock. Charging the larger of the
    # two also charges time spent queued behind another candidate's research.
    reach_s = max(work_s, time.time() - t_close) if follow else work_s
    out = {"wallet": feats,
           "wallet_decider": {k: v for k, v in verdict.items() if k != "raw"},
           "wallet_decider_available": bool(verdict.get("available")),
           "wallet_accept": bool(verdict.get("available")
                                 and verdict.get("accept")),
           "reach_s": round(reach_s, 3)}
    ctx.remember(watch.mint, out)
    return out


def build_row(watch, args, decider, window, now, wallet_ctx=None):
    """One ledger row for one closed window. Returns the row, or None if the
    candidate cannot be priced at all."""
    metrics = watch.metrics(window)
    passed, reasons = gate_verdict(metrics, args.gate)
    create = watch.create
    text = watch.text_payload()
    desc = (text.get("description") or "").strip()
    row = {
        "schema": SCHEMA, "t": "candidate",
        "capital_enabled": False, "transaction_built": False,
        "transaction_signed": False, "transaction_submitted": False,
        "mint": watch.mint, "creator": create.get("creator"),
        "slot": create.get("slot"), "sig": create.get("sig"),
        "created_ts": watch.t0, "window_s": window, "decided_ts": now,
        "name": create.get("name"), "symbol": create.get("symbol"),
        "metrics": metrics,
        "gate_passed": passed, "gate_fail_reasons": reasons,
        "meta_fetched": watch.meta is not None and bool(watch.meta.get("ok")),
        "desc_chars": len(desc),
        "text_thin": not desc,          # name+symbol only; see prereg
        "nonstandard_curve": create.get("v_sol") != STANDARD_V_SOL,
        "v_sol_at_create": create.get("v_sol"),
        "trades_in_tape": len(watch.trades),
    }
    if not passed:
        return row

    # Entry is priced at the curve state of the LAST print inside the window,
    # which is the state a decision taken at window close could actually reach.
    entry_index = len([t for t in watch.trades if t["ts"] - watch.t0 < window])
    v_sol, v_tok = metrics["v_sol"], metrics["v_tok"]
    entry = price_entry(v_sol, v_tok, args.clip_sol, args.fee_bps, args.gas_sol)
    if entry is None:
        row["unpriceable"] = "entry"
        return row
    row["entry"] = entry

    verdict = decider.decide(text)
    row["decider"] = {k: v for k, v in verdict.items() if k != "raw"}
    row["decider_accept"] = bool(verdict.get("accept"))
    row["decider_available"] = bool(verdict.get("available"))

    row["arms"] = {
        "gate": True,
        # A void instrument never accepts. It also never silently rejects: the
        # reason is on the row and `snipescore` refuses a headline when the
        # unavailable share is non-trivial.
        "gate_decide": bool(verdict.get("available") and verdict.get("accept")),
    }
    if wallet_ctx is not None:
        row.update(wallet_arm(watch, decider, window, wallet_ctx, args.follow))
        row["arms"]["gate_wallet_decide"] = bool(row.get("wallet_accept"))
    return row


def finalise(row, watch, args):
    """Attach the marks once the longest horizon has passed. Returns True when
    the row is complete."""
    entry = row.get("entry")
    if entry is None:
        return True
    entry_index = len([t for t in watch.trades
                       if t["ts"] - watch.t0 < row["window_s"]])
    t_entry = watch.t0 + row["window_s"]
    entry_state = {"ts": t_entry, "v_sol": entry["v_sol_at_entry"],
                   "v_tok": entry["v_tok_at_entry"]}
    marks = {}
    for h in HORIZONS:
        state = state_at(watch, entry_index, t_entry + h, entry_state)
        out = price_exit(entry, state["v_sol"], state["v_tok"],
                         args.fee_bps, args.gas_sol)
        if out is not None:
            # Negative: how STALE the standing state is, i.e. how long since the
            # last print. Reported so a mark resting on a long-dead curve is
            # visible rather than indistinguishable from a fresh one.
            out["staleness_s"] = round((t_entry + h) - state["ts"], 3)
        marks[str(h)] = out
    row["marks"] = marks
    row["primary"] = marks.get(str(PRIMARY_HORIZON))

    # The oracle is a BOUND over every exit rule on this path, computed before
    # any rule is proposed (`the-exit-oracle-pays-and-no-causal-rule-does`).
    best = None
    out = price_exit(entry, entry_state["v_sol"], entry_state["v_tok"],
                     args.fee_bps, args.gas_sol)
    if out:
        best = {"ret": out["ret"], "pnl_sol": out["pnl_sol"], "at_s": 0.0}
    for t in watch.trades[entry_index:]:
        if t["ts"] > t_entry + max(HORIZONS):
            break
        out = price_exit(entry, t["v_sol"], t["v_tok"], args.fee_bps,
                         args.gas_sol)
        if out and (best is None or out["ret"] > best["ret"]):
            best = {"ret": out["ret"], "pnl_sol": out["pnl_sol"],
                    "at_s": round(t["ts"] - t_entry, 2)}
    row["oracle"] = best
    # Recorded for stratification, not for dropping: a position with no print
    # after entry is still exitable into the standing curve (see `state_at`), it
    # just pays its two fees and its own impact and nothing else.
    row["no_print_after_entry"] = not any(
        t["ts"] >= t_entry for t in watch.trades[entry_index:])
    row["prints_after_entry"] = sum(1 for t in watch.trades[entry_index:]
                                    if t["ts"] >= t_entry)
    if row.get("reach_s") is not None:
        finalise_reach(row, watch, args)
    row["marked_ts"] = time.time()
    return True


def finalise_reach(row, watch, args):
    """Entry and marks at the moment the wallet verdict existed.

    Window close + research + verdict. Priced at the standing curve state then,
    so whatever the price did while we were looking wallets up is paid, not
    skipped. Every gate-passing row gets these, accept or not, so the arm and
    its roll null stay paired on identical economics.
    """
    t_reach = watch.t0 + row["window_s"] + row["reach_s"]
    c = watch.create
    state = state_at(watch, 0, t_reach,
                     {"ts": watch.t0, "v_sol": c["v_sol"], "v_tok": c["v_tok"]})
    entry = price_entry(state["v_sol"], state["v_tok"], args.clip_sol,
                        args.fee_bps, args.gas_sol)
    if entry is None:
        row["entry_reach"] = None
        return
    row["entry_reach"] = entry
    first = row.get("entry")
    if first and first.get("mid_price_at_entry"):
        row["reach_drift"] = (entry["mid_price_at_entry"]
                              / first["mid_price_at_entry"] - 1.0)
    reach_index = len([t for t in watch.trades if t["ts"] <= t_reach])
    entry_state = {"ts": t_reach, "v_sol": state["v_sol"],
                   "v_tok": state["v_tok"]}
    marks = {}
    for h in HORIZONS:
        st = state_at(watch, reach_index, t_reach + h, entry_state)
        marks[str(h)] = price_exit(entry, st["v_sol"], st["v_tok"],
                                   args.fee_bps, args.gas_sol)
    row["marks_reach"] = marks


def iter_tape(path, follow, idle_exit):
    """Yield parsed rows. With --follow, keep reading and tolerate rotation."""
    handle = open(path)
    last = time.time()
    while True:
        line = handle.readline()
        if line:
            last = time.time()
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue        # a partially flushed line; it reappears
            continue
        if not follow:
            break
        if idle_exit and time.time() - last > idle_exit:
            break
        yield None                  # a tick, so the caller can sweep
        time.sleep(0.25)
    handle.close()


def run(args):
    decider = Decider(args.decider_url, args.decider_token, args.decider_timeout)
    wallet_ctx = None
    registry = W.Registry()
    if args.wallet or args.wallet_cache:
        finder = None
        if args.wallet:
            finder = W.WalletFinder(
                W.Rpc(args.wallet_rpc), preindex_path=args.wallet_preindex,
                budget_s=args.wallet_budget_s, buyers=args.wallet_buyers)
        wallet_ctx = WalletCtx(finder, registry, args.wallet_cache)
    ledger = open(args.ledger, "a", buffering=1)
    watches = {}
    pending = []                    # (row, watch, due_ts)
    counts = collections.Counter()
    window = args.window
    hold = max(HORIZONS) + args.mark_slack
    deadline = (time.time() + args.duration) if args.duration else None

    def sweep(now):
        for mint, watch in list(watches.items()):
            if watch.emitted or now - watch.t0 < window:
                continue
            watch.emitted = True
            counts["window_closed"] += 1
            row = build_row(watch, args, decider, window, now, wallet_ctx)
            if row is None:
                continue
            if not row["gate_passed"]:
                counts["gate_rejected"] += 1
                for reason in row["gate_fail_reasons"]:
                    counts["why_" + reason] += 1
                ledger.write(json.dumps(row, separators=(",", ":")) + "\n")
                watches.pop(mint, None)
                continue
            counts["gate_passed"] += 1
            counts["arm_gate_decide" if row["arms"]["gate_decide"]
                   else "arm_decide_reject"] += 1
            pending.append((row, watch, watch.t0 + window + hold
                            + (row.get("reach_s") or 0.0)))
        for item in list(pending):
            row, watch, due = item
            if now < due:
                continue
            pending.remove(item)
            finalise(row, watch, args)
            ledger.write(json.dumps(row, separators=(",", ":")) + "\n")
            counts["finalised"] += 1
            watches.pop(row["mint"], None)
        # Drop anything the tape has stopped feeding, so a 24 h run does not
        # grow a watch set the box cannot hold.
        for mint, watch in list(watches.items()):
            if watch.emitted and now - watch.t0 > window + hold + 60:
                watches.pop(mint, None)
                counts["watch_dropped"] += 1

    for row in iter_tape(args.tape, args.follow, args.idle_exit):
        if row is None:
            sweep(time.time())
            if deadline and time.time() > deadline:
                break
            continue
        kind = row.get("t")
        counts["tape_" + str(kind)] += 1
        if kind == "create":
            registry.on_create(row)
            if len(watches) < args.max_watch:
                watches[row["mint"]] = Watch(row)
            else:
                counts["watch_set_at_cap"] += 1
        elif kind == "trade":
            registry.on_trade(row)
            watch = watches.get(row["mint"])
            if watch is not None:
                watch.on_trade(row)
        elif kind == "meta":
            watch = watches.get(row["mint"])
            if watch is not None:
                watch.meta = row
        # In replay the clock is the TAPE's clock, not the wall clock, or a
        # saved tape would close every window instantly on the first row. `ts`
        # (the venue's own event time) and not `recv` (our drain time), because a
        # receipt timestamp measures drain, not arrival
        # (`a-receipt-timestamp-measures-drain-time-not-arrival`), and mixing the
        # two puts the window boundary off by the propagation delay.
        clock = row.get("ts") or row.get("recv") or time.time()
        sweep(clock if not args.follow else time.time())
        if deadline and time.time() > deadline:
            break

    # Flush whatever is still open, using the last clock we saw.
    final_clock = time.time() if args.follow else float("inf")
    for row, watch, due in list(pending):
        pending.remove((row, watch, due))
        finalise(row, watch, args)
        ledger.write(json.dumps(row, separators=(",", ":")) + "\n")
        counts["finalised_at_exit"] += 1
    if not args.follow:
        sweep(final_clock)
    ledger.close()
    return counts, decider.counts


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tape", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="WALL-clock seconds with --follow; 0 = until the tape ends")
    ap.add_argument("--idle-exit", type=float, default=0.0,
                    help="with --follow, stop after this many idle seconds")
    ap.add_argument("--window", type=float, default=30.0)
    ap.add_argument("--clip-sol", type=float, default=0.25,
                    help="the paper clip; priced through the curve, so it is a "
                         "real cost and not a scale factor")
    ap.add_argument("--fee-bps", type=float, default=100.0,
                    help="pump.fun protocol+creator fee per leg")
    ap.add_argument("--gas-sol", type=float, default=0.0001,
                    help="base+priority fee per leg, charged both legs")
    ap.add_argument("--mark-slack", type=float, default=30.0)
    ap.add_argument("--max-watch", type=int, default=3000)
    ap.add_argument("--decider-url",
                    default=os.environ.get("SNIPE_DECIDE_URL"))
    ap.add_argument("--decider-token",
                    default=os.environ.get("SNIPE_DECIDE_TOKEN"))
    # 30 s, not 8: measured 2026-10-05 on the e2-small, one inference takes
    # ~6 s against swap-resident weights (the model card's 38 ms is a V100 and
    # its 167 ms a 48-vCPU host). Harmless for a paper mark -- the entry price is
    # already fixed at window close, so a slow verdict delays the ledger row
    # without moving the fill -- but it is a hard REACHABILITY fact: a live arm
    # cannot decide in 6 s inside a 30 s window and would need a different host.
    ap.add_argument("--decider-timeout", type=float, default=30.0)
    ap.add_argument("--wallet", action="store_true",
                    help="run the wallet-history arm (read-only RPC)")
    ap.add_argument("--wallet-rpc", default=W.default_rpc_url(),
                    help="JSON-RPC URL; default $SOLANA_RPC_URL, else public")
    ap.add_argument("--wallet-cache",
                    help="jsonl of per-mint research; a replay reads it back")
    ap.add_argument("--wallet-preindex",
                    help="pumpfunderpreindex sqlite, for funder fan-out")
    ap.add_argument("--wallet-budget-s", type=float, default=12.0)
    ap.add_argument("--wallet-buyers", type=int, default=8)
    args = ap.parse_args(argv)
    args.gate = dict(GATE)
    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)), exist_ok=True)
    counts, dcounts = run(args)
    print(json.dumps({"counts": dict(counts), "decider": dict(dcounts),
                      "gate": args.gate, "primary_horizon_s": PRIMARY_HORIZON},
                     sort_keys=True))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
