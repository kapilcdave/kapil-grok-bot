"""Audit whether state-inferred "virgin" curves were actually just created.

The archived tape starts at arbitrary window boundaries and contains only
TradeEvents.  `pumporacle.build_mints` has historically called a curve virgin
when its first observed trade has an initial-looking reserve state.  That is
not sufficient if reserves can reset or an old, idle curve returns to the same
state.

This audit asks Solana for transactions immediately preceding each candidate's
first observed trade.  A prior TradeEvent for the same mint proves the
candidate is not a birth.  A CreateEvent identifies the actual creator and
creation transaction.

Read-only.  It never signs or submits a transaction.

    python3 pumpbirthaudit.py --sample 160
    python3 pumpbirthaudit.py --all
"""
import argparse
import concurrent.futures as cf
import json
import os
import random
import struct
import threading
import time
import urllib.error
import urllib.request

import pumpcurve as C
import pumporacle as O
import pumptape as T

PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
CPI_EVENT_DISC = bytes.fromhex("e445a52e51cb9a1d")
TRADE_EVENT_DISC = bytes.fromhex("bddb7fd34ee661ee")
CREATE_EVENT_DISC = bytes.fromhex("1b72a94ddeeb6376")


def read_borsh_string(raw, off):
    if off + 4 > len(raw):
        raise ValueError("short string length")
    n, = struct.unpack_from("<I", raw, off)
    off += 4
    if n > 10_000 or off + n > len(raw):
        raise ValueError("invalid string length")
    return raw[off:off + n].decode("utf-8", errors="replace"), off + n


def parse_create_event(body):
    """Decode the stable prefix of pump.fun CreateEvent from the official IDL."""
    if body[:8] != CREATE_EVENT_DISC:
        return None
    off = 8
    try:
        name, off = read_borsh_string(body, off)
        symbol, off = read_borsh_string(body, off)
        uri, off = read_borsh_string(body, off)
        if off + 32 * 4 + 8 > len(body):
            return None
        mint = T.b58encode(body[off:off + 32])
        off += 32
        bonding_curve = T.b58encode(body[off:off + 32])
        off += 32
        user = T.b58encode(body[off:off + 32])
        off += 32
        creator = T.b58encode(body[off:off + 32])
        off += 32
        timestamp, = struct.unpack_from("<q", body, off)
    except (ValueError, struct.error):
        return None
    return {
        "name": name,
        "symbol": symbol,
        "uri": uri,
        "mint": mint,
        "bonding_curve": bonding_curve,
        "user": user,
        "creator": creator,
        "timestamp": timestamp,
    }


def pump_events(tx):
    """Return decoded TradeEvent/CreateEvent CPI payloads from one transaction."""
    out = []
    meta = (tx or {}).get("meta") or {}
    if meta.get("err"):
        return out
    for group in meta.get("innerInstructions") or []:
        for ix in group.get("instructions") or []:
            data = ix.get("data")
            if not data:
                continue
            try:
                raw = T.b58decode(data)
            except (KeyError, ValueError):
                continue
            if raw[:8] != CPI_EVENT_DISC:
                continue
            body = raw[8:]
            if body[:8] == TRADE_EVENT_DISC:
                event = T.parse_trade(body)
                if event:
                    out.append(("trade", event))
            elif body[:8] == CREATE_EVENT_DISC:
                event = parse_create_event(body)
                if event:
                    out.append(("create", event))
    return out


class Rpc:
    def __init__(self, url, min_interval=0.10):
        self.url = url
        self.min_interval = min_interval
        self.lock = threading.Lock()
        self.last = 0.0
        self.calls = 0
        self.retries = 0

    def call(self, method, params, tries=8, timeout=40):
        for attempt in range(tries):
            with self.lock:
                wait = self.min_interval - (time.monotonic() - self.last)
                if wait > 0:
                    time.sleep(wait)
                self.last = time.monotonic()
                self.calls += 1
            payload = json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            }).encode()
            req = urllib.request.Request(
                self.url, payload, {"content-type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    result = json.load(response)
                if result.get("error"):
                    message = str(result["error"])
                    if "429" not in message and "Too many" not in message:
                        return None, message
                else:
                    return result.get("result"), None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                message = type(exc).__name__
            self.retries += 1
            if attempt + 1 < tries:
                time.sleep(
                    min(8.0, 0.35 * 2 ** attempt)
                    + random.random() * 0.2
                )
        return None, message

    def signatures_before(self, address, signature, limit):
        return self.call("getSignaturesForAddress", [
            address, {"before": signature, "limit": limit},
        ])

    def transaction(self, signature, encoding="json"):
        return self.call("getTransaction", [
            signature, {
                "encoding": encoding,
                "maxSupportedTransactionVersion": 1,
                "commitment": "finalized",
            },
        ])


def candidates(pattern, sample, seed, boundary_slots=20):
    raw, metas = O.load(pattern)
    virgin, _, _ = O.build_mints(raw, metas, C.discover_initial_states(raw))
    by_window = {m["window"]: m for m in metas}
    rows = []
    for (window, mint), v in virgin.items():
        first = v["tape"][0]
        rows.append({
            "window": window,
            "mint": mint,
            "first_sig": first["sig"],
            "first_slot": first["slot"],
            "state_user": first["user"],
            "offset_slots": first["slot"] - by_window[window]["lo_slot"],
        })
    if sample is None or sample >= len(rows):
        return sorted(rows, key=lambda r: (r["window"], r["first_slot"], r["mint"]))

    # Include the dangerous window-boundary cases first, then allocate the
    # remainder equally by window so high-activity windows do not dominate.
    rnd = random.Random(seed)
    boundary = [r for r in rows if r["offset_slots"] < boundary_slots]
    rnd.shuffle(boundary)
    if len(boundary) >= sample:
        return boundary[:sample]
    picked = list(boundary)
    picked_keys = {(r["mint"], r["first_sig"]) for r in picked}
    grouped = {}
    for row in rows:
        if (row["mint"], row["first_sig"]) in picked_keys:
            continue
        grouped.setdefault(row["window"], []).append(row)
    per = max(1, (sample - len(picked)) // len(grouped))
    for window in sorted(grouped):
        pool = grouped[window]
        rnd.shuffle(pool)
        picked.extend(pool[:per])
    if len(picked) < sample:
        picked_keys = {(r["mint"], r["first_sig"]) for r in picked}
        remaining = [
            r for r in rows
            if (r["mint"], r["first_sig"]) not in picked_keys
        ]
        rnd.shuffle(remaining)
        picked.extend(remaining[:sample - len(picked)])
    return picked[:sample]


def inspect_candidate(rpc, candidate, older_limit):
    out = dict(candidate)
    out.update({
        "status": "unresolved",
        "create_sig": None,
        "create_slot": None,
        "creator": None,
        "create_user": None,
        "prior_trade_sig": None,
        "prior_trade_slot": None,
        "older_signatures_checked": 0,
        "error": None,
    })

    first_tx, error = rpc.transaction(candidate["first_sig"])
    if error or first_tx is None:
        out["error"] = "first_tx: " + str(error)
        return out
    for kind, event in pump_events(first_tx):
        if kind == "create" and event["mint"] == candidate["mint"]:
            out.update({
                "status": "create_in_first_trade_tx",
                "create_sig": candidate["first_sig"],
                "create_slot": candidate["first_slot"],
                "creator": event["creator"],
                "create_user": event["user"],
            })
            return out

    sigs, error = rpc.signatures_before(
        candidate["mint"], candidate["first_sig"], older_limit
    )
    if error or sigs is None:
        out["error"] = "older_sigs: " + str(error)
        return out

    for item in sigs:
        if item.get("err"):
            continue
        out["older_signatures_checked"] += 1
        tx, error = rpc.transaction(item["signature"])
        if error or tx is None:
            out["error"] = "older_tx: " + str(error)
            return out
        events = [
            (kind, event) for kind, event in pump_events(tx)
            if event["mint"] == candidate["mint"]
        ]
        if any(kind == "trade" for kind, _event in events):
            out.update({
                "status": "prior_trade_proves_not_birth",
                "prior_trade_sig": item["signature"],
                "prior_trade_slot": item["slot"],
            })
            return out
        creates = [event for kind, event in events if kind == "create"]
        if creates:
            out.update({
                "status": "create_precedes_first_trade",
                "create_sig": item["signature"],
                "create_slot": item["slot"],
                "creator": creates[0]["creator"],
                "create_user": creates[0]["user"],
            })
            return out

    out["status"] = "no_create_or_prior_trade_in_scan"
    return out


def summarize(rows):
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    resolved = [
        r for r in rows
        if r["status"] in {
            "create_in_first_trade_tx",
            "create_precedes_first_trade",
            "prior_trade_proves_not_birth",
        }
    ]
    false = [r for r in resolved if r["status"] == "prior_trade_proves_not_birth"]
    creators = [r for r in rows if r.get("creator")]
    state_user_matches = sum(r["creator"] == r["state_user"] for r in creators)
    boundary = [r for r in rows if r["offset_slots"] < 20]
    interior = [r for r in rows if r["offset_slots"] >= 20]

    def false_rate(group):
        known = [
            r for r in group
            if r["status"] in {
                "create_in_first_trade_tx",
                "create_precedes_first_trade",
                "prior_trade_proves_not_birth",
            }
        ]
        return (
            sum(r["status"] == "prior_trade_proves_not_birth" for r in known)
            / len(known) if known else None
        )

    return {
        "n": len(rows),
        "status_counts": counts,
        "resolved": len(resolved),
        "false_births": len(false),
        "false_birth_rate_resolved": len(false) / len(resolved) if resolved else None,
        "boundary_lt_20_slots": {
            "n": len(boundary), "false_rate_resolved": false_rate(boundary),
        },
        "interior_ge_20_slots": {
            "n": len(interior), "false_rate_resolved": false_rate(interior),
        },
        "creators_decoded": len(creators),
        "creator_equals_first_trade_user_share": (
            state_user_matches / len(creators) if creators else None
        ),
        "errors": sum(bool(r.get("error")) for r in rows),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", default="data/pumptape/*.jsonl.gz")
    parser.add_argument("--jsonl", default="data/pumpbirthaudit.jsonl")
    parser.add_argument("--summary", default="data/pumpbirthaudit_summary.json")
    parser.add_argument("--sample", type=int, default=160)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--older-limit", type=int, default=20)
    parser.add_argument("--boundary-slots", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--qps", type=float, default=8.0)
    parser.add_argument("--seed", type=int, default=20261002)
    args = parser.parse_args()

    url = os.environ.get(
        "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
    ).strip()
    chosen = candidates(
        args.tape, None if args.all else args.sample, args.seed,
        args.boundary_slots,
    )
    done = {}
    if os.path.exists(args.jsonl):
        with open(args.jsonl) as handle:
            for line in handle:
                row = json.loads(line)
                done[(row["mint"], row["first_sig"])] = row
    pending = [
        row for row in chosen
        if (
            (row["mint"], row["first_sig"]) not in done
            or done[(row["mint"], row["first_sig"])].get("error")
        )
    ]
    rpc = Rpc(url, min_interval=1.0 / max(args.qps, 0.1))

    with (
        open(args.jsonl, "a") as sink,
        cf.ThreadPoolExecutor(max_workers=args.workers) as pool,
    ):
        futures = {
            pool.submit(inspect_candidate, rpc, row, args.older_limit): row
            for row in pending
        }
        for i, future in enumerate(cf.as_completed(futures), 1):
            row = future.result()
            done[(row["mint"], row["first_sig"])] = row
            sink.write(json.dumps(row, sort_keys=True) + "\n")
            sink.flush()
            if i % 10 == 0 or i == len(pending):
                print(
                    f"{i}/{len(pending)} new; {rpc.calls} RPC calls, "
                    f"{rpc.retries} retries",
                    flush=True,
                )

    selected = [done[(r["mint"], r["first_sig"])] for r in chosen]
    output = {
        "rpc_url": (
            "public-mainnet"
            if url == "https://api.mainnet-beta.solana.com" else "configured"
        ),
        "sample_requested": None if args.all else args.sample,
        "older_limit": args.older_limit,
        "boundary_slots": args.boundary_slots,
        "seed": args.seed,
        "summary": summarize(selected),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    with open(args.summary, "w") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
