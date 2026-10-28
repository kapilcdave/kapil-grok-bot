"""Wallet-history finder for a pump.fun candidate, rendered as text for GLiNER.

Capital-disabled by construction: no keypair, no transaction builder, no signer,
no submission path. The only network calls are read-only JSON-RPC reads.

At window close, for each gate-passing candidate, it researches:

* **the creator** -- earlier launches by the same wallet seen on this run's tape
  and how they ended (peak market cap, whether the creator sold); the wallet's
  on-chain history depth and age before the create; its direct SOL funder and
  how many OTHER creators that funder has bankrolled (local preindex + this run).
* **the early buyers** -- the first K distinct non-creator buyers inside the
  window: whether each is a fresh wallet, and whether a fresh one was funded by
  the creator or by the creator's funder (a bundle link).

The result is one plain-English paragraph sent to `snipedecide` as `text`, so
the sidecar and its frozen schema are unchanged.

**Research time is charged, not hidden.** Every lookup runs under a wall-clock
budget and the ledger records how long research + verdict took (`reach_s`).
`snipepaper` prices this arm's entry at the curve state when the verdict would
actually exist, not at window close. A slow finder therefore pays its delay as
slippage, which is the reachability fact `pumpfun-fresh-curve-edge-is-intra-
slot-queue-position` says decides this venue.

**What is already known, so the readout is not over-read.** Deployer quality is
rank-persistent (rho 0.502) but the seat it selects into was negative at every
reachable latency (`pumpfun-deployer-history-persists-but-pays-nothing`), and the
funder graph mostly identifies multi-wallet bundles, whose cohort is the WORST
(`topics-pumpfun-closures`). A text model on rendered numbers has tied a plain
logistic before (`a-text-encoder-on-rendered-numbers-ties-a-logistic`), so the
scorer's D2 check sweeps every feature here for a one-line reduction.
"""
import collections
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request

from snipedecode import curve_price

SCHEMA = "snipewallet-1"
TOTAL_SUPPLY = 1_000_000_000.0
MIN_FUNDING_LAMPORTS = 5_000_000      # same dust floor as `pumpfunding.py`
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


def mcap_sol(v_sol, v_tok):
    price = curve_price(v_sol, v_tok)
    return (price * TOTAL_SUPPLY) if price else 0.0


# ------------------------------------------------------------------ registry
class Registry:
    """Every launch on this run's tape, keyed by creator, with its outcome so
    far. Fed from the tape, so a replay rebuilds exactly what the live run saw.

    Only data up to the decision time is ever read: a prior launch's peak is the
    peak observed BEFORE `before_ts`, never its eventual outcome.
    """

    def __init__(self, keep_s=24 * 3600):
        self.keep_s = keep_s
        self.by_creator = collections.defaultdict(list)
        self.by_mint = {}
        self.funded = collections.defaultdict(set)     # funder -> creators
        self._last_prune = 0.0

    def on_create(self, row):
        creator = row.get("creator")
        if not creator or row.get("mint") in self.by_mint:
            return
        rec = {"mint": row["mint"], "ts": row["ts"], "creator": creator,
               "peaks": [], "creator_sold_ts": None}
        self.by_mint[row["mint"]] = rec
        self.by_creator[creator].append(rec)
        self._prune(row["ts"])

    def on_trade(self, row):
        rec = self.by_mint.get(row.get("mint"))
        if rec is None:
            return
        m = mcap_sol(row.get("v_sol"), row.get("v_tok"))
        # Running peak as (ts, peak) steps, so `prior()` can read the peak as of
        # any earlier time without peeking at later prints.
        if not rec["peaks"] or m > rec["peaks"][-1][1]:
            rec["peaks"].append((row["ts"], m))
        if (row.get("side") == "sell" and row.get("user") == rec["creator"]
                and rec["creator_sold_ts"] is None):
            rec["creator_sold_ts"] = row["ts"]

    def note_funder(self, funder, creator):
        if funder and creator:
            self.funded[funder].add(creator)

    def prior(self, creator, before_ts):
        out = []
        for rec in self.by_creator.get(creator, ()):
            if rec["ts"] >= before_ts:
                continue
            peak = 0.0
            for ts, m in rec["peaks"]:
                if ts > before_ts:
                    break
                peak = m
            sold = (rec["creator_sold_ts"] is not None
                    and rec["creator_sold_ts"] <= before_ts)
            out.append({"mint": rec["mint"], "ts": rec["ts"],
                        "peak_mcap_sol": peak, "creator_sold": sold})
        return out

    def _prune(self, now):
        if now - self._last_prune < 600:
            return
        self._last_prune = now
        cutoff = now - self.keep_s
        for mint, rec in list(self.by_mint.items()):
            if rec["ts"] < cutoff:
                del self.by_mint[mint]
        for creator, recs in list(self.by_creator.items()):
            kept = [r for r in recs if r["ts"] >= cutoff]
            if kept:
                self.by_creator[creator] = kept
            else:
                del self.by_creator[creator]


# ----------------------------------------------------------------------- rpc
class RpcFailed(Exception):
    """A lookup that did not answer. Never to be read as an empty answer: a
    failed funder lookup rendered as "no funder found" is a fabricated fact
    (`an-empty-result-must-not-become-the-sentinels-opposite`)."""


class Rpc:
    """Minimal read-only JSON-RPC client. One retry, and only on a rate limit:
    inside a decision budget a long retry loop is just a slower failure.

    Measured 2026-10-10 on the public endpoint: 26 of 105 calls failed on
    rate limits over 12 candidates, so a run on it reports many rows as
    `complete: false`. `$SOLANA_RPC_URL` should point at a keyed endpoint.
    """

    def __init__(self, url, timeout=4.0, retry_sleep=0.4):
        self.url = url
        self.timeout = timeout
        self.retry_sleep = retry_sleep
        self.calls = 0
        self.errors = collections.Counter()

    def call(self, method, params, timeout=None):
        for attempt in (0, 1):
            self.calls += 1
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                               "params": params}).encode()
            req = urllib.request.Request(self.url, body,
                                         {"content-type": "application/json"})
            limited = False
            try:
                with urllib.request.urlopen(
                        req, timeout=timeout or self.timeout) as r:
                    out = json.load(r)
                if not out.get("error"):
                    return out.get("result")
                message = str(out["error"])
                limited = "429" in message or "Too many" in message
                self.errors["rate_limited" if limited else "rpc_error"] += 1
            except urllib.error.HTTPError as exc:
                limited = exc.code == 429
                self.errors[f"http_{exc.code}"] += 1
            except (urllib.error.URLError, TimeoutError, OSError,
                    json.JSONDecodeError) as exc:
                self.errors[type(exc).__name__] += 1
            if not (limited and attempt == 0):
                break
            time.sleep(self.retry_sleep)
        raise RpcFailed(method)

    def signatures(self, address, limit, before=None):
        opts = {"limit": limit, "commitment": "confirmed"}
        if before:
            opts["before"] = before
        return self.call("getSignaturesForAddress", [address, opts])

    def transaction(self, signature):
        # `confirmed`, not `finalized`: a funding transfer can be seconds old,
        # and finalized lags ~13 s, which would read a fresh funder as absent.
        return self.call("getTransaction", [signature, {
            "encoding": "jsonParsed", "commitment": "confirmed",
            "maxSupportedTransactionVersion": 0}])


def incoming_funders(tx, destination, min_lamports=MIN_FUNDING_LAMPORTS):
    """Sources that sent >= `min_lamports` of native SOL into `destination` in a
    successful transaction, largest first. Same rule as `pumpfunding.py`,
    restated here so the deploy bundle stays stdlib-only."""
    if not tx or (tx.get("meta") or {}).get("err"):
        return []
    groups = [(tx.get("transaction") or {}).get("message", {})
              .get("instructions", [])]
    groups.extend(g.get("instructions", [])
                  for g in (tx.get("meta") or {}).get("innerInstructions") or [])
    by_source = collections.Counter()
    for ixs in groups:
        for ix in ixs:
            if ix.get("program") != "system":
                continue
            parsed = ix.get("parsed") or {}
            if parsed.get("type") not in {"transfer", "transferWithSeed"}:
                continue
            info = parsed.get("info") or {}
            src, dst, lam = (info.get("source"), info.get("destination"),
                             info.get("lamports"))
            if dst == destination and src and src != destination \
                    and isinstance(lam, int):
                by_source[src] += lam
    return [(src, lam) for src, lam in by_source.most_common()
            if lam >= min_lamports]


# -------------------------------------------------------------------- finder
class WalletFinder:
    def __init__(self, rpc, preindex_path=None, budget_s=12.0, buyers=8,
                 creator_history=100, buyer_history=50, fresh_max_sigs=10,
                 funder_tx_max=6):
        self.rpc = rpc
        self.budget_s = budget_s
        self.buyers = buyers
        self.creator_history = creator_history
        self.buyer_history = buyer_history
        self.fresh_max_sigs = fresh_max_sigs
        self.funder_tx_max = funder_tx_max
        self.preindex = None
        if preindex_path and os.path.exists(preindex_path):
            self.preindex = sqlite3.connect(
                f"file:{preindex_path}?mode=ro", uri=True,
                check_same_thread=False)

    def _fanout_preindex(self, funder):
        if self.preindex is None or not funder:
            return 0
        row = self.preindex.execute(
            "SELECT COUNT(DISTINCT destination) FROM funding_edges "
            "WHERE source = ?", (funder,)).fetchone()
        return int(row[0] or 0)

    def _funder_of(self, address, sigs, deadline):
        """First qualifying SOL funder of `address` walking `sigs` in the order
        given. Returns (funder, lamports, None) or (None, 0, why) where `why`
        is None only when every transaction was actually read and none funded
        it -- "budget" or "rpc" mean the answer is UNKNOWN, not absent."""
        for item in sigs[:self.funder_tx_max]:
            if time.monotonic() > deadline:
                return None, 0, "budget"
            try:
                tx = self.rpc.transaction(item["signature"])
            except RpcFailed:
                return None, 0, "rpc"
            found = incoming_funders(tx, address)
            if found:
                return found[0][0], found[0][1], None
        return None, 0, None

    def research(self, create, window_trades, registry, decision_ts):
        """All lookups for one candidate, bounded by `budget_s` wall seconds.

        `window_trades` must already be cut to the decision window by the caller.
        """
        t0 = time.monotonic()
        deadline = t0 + self.budget_s
        calls0 = self.rpc.calls if self.rpc else 0
        creator = create.get("creator")
        f = {"schema": SCHEMA, "creator": creator, "complete": True,
             "cut_short": []}

        prior = registry.prior(creator, create["ts"]) if creator else []
        f["creator_prior_launches"] = len(prior)
        f["creator_prior_best_mcap_sol"] = round(
            max((p["peak_mcap_sol"] for p in prior), default=0.0), 2)
        f["creator_prior_sold"] = sum(1 for p in prior if p["creator_sold"])

        # Creator on-chain history strictly before its create transaction.
        f.update({"creator_history_n": None, "creator_history_saturated": None,
                  "creator_age_s": None, "funder": None,
                  "funder_status": "unknown",
                  "funder_lamports": 0, "funder_fanout": None})
        sigs = None
        if self.rpc and creator:
            try:
                sigs = self.rpc.signatures(creator, self.creator_history,
                                           before=create.get("sig"))
            except RpcFailed:
                sigs = None
        if sigs is None:
            f["complete"] = False
            f["cut_short"].append("creator_history")
        else:
            ok = [s for s in sigs if not s.get("err")]
            f["creator_history_n"] = len(ok)
            f["creator_history_saturated"] = len(sigs) >= self.creator_history
            times = [s["blockTime"] for s in ok if s.get("blockTime")]
            if times:
                f["creator_age_s"] = round(create["ts"] - min(times), 1)
            funder, lam, why = self._funder_of(creator, ok, deadline)
            if why:
                f["complete"] = False
                f["cut_short"].append("creator_funder_" + why)
            f["funder"], f["funder_lamports"] = funder, lam
            f["funder_status"] = ("found" if funder else
                                  "unknown" if why else "none_in_recent")
            f["funder_txs_scanned"] = min(len(ok), self.funder_tx_max)
            if funder:
                registry.note_funder(funder, creator)
                others = (registry.funded[funder] - {creator})
                f["funder_fanout"] = max(len(others),
                                         self._fanout_preindex(funder) - 1, 0)

        # Early buyers: first K distinct non-creator buyers in the window.
        buyers = []
        for t in window_trades:
            u = t.get("user")
            if (t.get("side") == "buy" and u and u != creator
                    and u not in buyers):
                buyers.append(u)
            if len(buyers) >= self.buyers:
                break
        checked = fresh = linked = unknown = 0
        links = {creator, f["funder"]} - {None}
        for u in buyers:
            if not self.rpc or time.monotonic() > deadline:
                f["complete"] = False
                f["cut_short"].append("buyers")
                break
            try:
                bs = self.rpc.signatures(u, self.buyer_history) or []
            except RpcFailed:
                unknown += 1
                f["complete"] = False
                continue
            checked += 1
            ok = [s for s in bs if not s.get("err")]
            if len(ok) > self.fresh_max_sigs:
                continue
            fresh += 1
            # A fresh wallet's funding is near its oldest transaction.
            oldest_first = list(reversed(ok))
            src, _, why = self._funder_of(u, oldest_first, deadline)
            if why == "budget":
                f["complete"] = False
                f["cut_short"].append("buyer_funder_budget")
                break
            if why:
                unknown += 1
                f["complete"] = False
            if src and src in links:
                linked += 1
        f["buyers_seen"] = len(buyers)
        f["buyers_checked"] = checked
        f["buyers_fresh"] = fresh
        f["buyers_linked"] = linked
        f["buyers_unknown"] = unknown
        if unknown and "buyers_rpc" not in f["cut_short"]:
            f["cut_short"].append("buyers_rpc")

        f["research_s"] = round(time.monotonic() - t0, 3)
        f["rpc_calls"] = (self.rpc.calls - calls0) if self.rpc else 0
        f["decision_ts"] = decision_ts
        f["text"] = render(f)
        return f


def render(f):
    """Features -> the one paragraph GLiNER reads. Plain English, no metrics
    from the gate (those would let the model rebuild the gate and look
    informative -- `the-gliner-gates-benchmark-is-a-one-line-width-gate`)."""
    parts = []
    n = f.get("creator_prior_launches") or 0
    if n:
        parts.append(
            f"The creator wallet launched {n} earlier token"
            f"{'s' if n != 1 else ''} in the last day; the best one reached "
            f"{f.get('creator_prior_best_mcap_sol', 0):.0f} SOL market cap, and "
            f"the creator sold early on {f.get('creator_prior_sold', 0)} of them.")
    else:
        parts.append("The creator wallet has no earlier launches in the last day.")
    h = f.get("creator_history_n")
    if h is None:
        parts.append("The creator wallet's on-chain history is unknown.")
    else:
        more = "at least " if f.get("creator_history_saturated") else ""
        age = f.get("creator_age_s")
        if age is not None:
            parts.append(f"It has {more}{h} earlier transactions, the oldest "
                         f"{_human(age)} before launch.")
        else:
            parts.append(f"It has {more}{h} earlier transactions.")
    status = f.get("funder_status")
    if f.get("funder"):
        fan = f.get("funder_fanout") or 0
        parts.append(
            f"It was funded with {f['funder_lamports'] / 1e9:.2f} SOL by a "
            f"wallet that has funded {fan} other token creator"
            f"{'s' if fan != 1 else ''}.")
    elif status == "none_in_recent" and h:
        parts.append("No SOL funding into it appears in its "
                     f"{f.get('funder_txs_scanned', 0)} most recent transactions.")
    elif h:
        parts.append("Who funded it is unknown.")
    checked = f.get("buyers_checked") or 0
    if checked:
        parts.append(
            f"Of {checked} early buyers checked, {f.get('buyers_fresh', 0)} are "
            f"fresh wallets and {f.get('buyers_linked', 0)} were funded by the "
            "creator or the creator's funder.")
    elif f.get("buyers_seen"):
        parts.append("The early buyers could not be checked.")
    else:
        parts.append("There were no other early buyers.")
    return " ".join(parts)


def _human(seconds):
    s = float(seconds)
    if s < 120:
        return f"{s:.0f} seconds"
    if s < 7200:
        return f"{s / 60:.0f} minutes"
    if s < 172800:
        return f"{s / 3600:.0f} hours"
    return f"{s / 86400:.0f} days"


# Feature names the scorer's D2 sweeps for a one-line reduction of the arm.
D2_FEATURES = ("creator_prior_launches", "creator_prior_best_mcap_sol",
               "creator_prior_sold", "creator_history_n", "creator_age_s",
               "funder_fanout", "buyers_fresh", "buyers_linked")


def default_rpc_url():
    # SOLANA_RPC_HTTP_URL is the name `configure_solana_prod_vm.sh` installs.
    return (os.environ.get("SOLANA_RPC_URL")
            or os.environ.get("SOLANA_RPC_HTTP_URL") or PUBLIC_RPC)
