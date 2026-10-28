"""Follow the swarm wallets: every transaction they make, which coin, which launchpad, how much SOL.

    python -m research.swarmwatch [--wallets FILE] [--hours 24] [--out research/data]

Why. The only multi-SOL flow our 41 coins ever drew was three bot SWARMS (AMDER 12.7 SOL, BEAD 7.7, HSTK 4.4):
5-17 wallets buying >= 0.3 SOL each 7-40 s after the create, then dumping. The three swarms share almost no
wallets (24 wallets, 1 on two coins), and the 10-15 audit found no trigger in OUR coins. So we watch what
these wallets buy across ALL of Solana (pump.fun, PumpSwap, Meteora, LaunchLab/Bonk, ...) and learn the
trigger from the coins they pick, against the launches they skip.

Feed: Helius `logsSubscribe` (mentions = one wallet each), free. Each signature is fetched once with
`getTransaction` on Helius HTTP. One row per transaction: the wallet's SOL change, the non-SOL mints whose
balance it moved, and the named programs it touched. Rows go to research/data/swarmwatch-YYYYMMDD.jsonl.
Nothing here signs or sends.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket

from devbot import http

ENV = Path("~/.config/solana-rpc/helius.env").expanduser()
WSOL = "So11111111111111111111111111111111111111112"
PROGRAMS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "pumpswap",
    "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN": "meteora_dbc",
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": "meteora_damm2",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "meteora_dlmm",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "meteora_damm1",
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj": "raydium_launchlab",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "raydium_amm4",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "raydium_cpmm",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "raydium_clmm",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "orca",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "jupiter",
    "MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG": "moonshot",
    "BSfD6SHZigAfDWSjzD5Q41jw8LmKwtmjskPH9XW1mrRW": "pump_fees",
    "jitodontfront111111111111111111111111111111": "jito_dontfront",
}
TIP_ACCOUNTS = {
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
}


def env(key: str) -> str:
    for line in ENV.read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    raise SystemExit(f"no {key} in {ENV}")


def summarize(tx: dict, wallet: str) -> dict:
    """The wallet's SOL change, the mints whose balances it moved (owner = wallet), and named programs."""
    msg, meta = tx["transaction"]["message"], tx["meta"]
    keys = list(msg["accountKeys"]) + list((meta.get("loadedAddresses") or {}).get("writable", [])) \
        + list((meta.get("loadedAddresses") or {}).get("readonly", []))
    keys = [k if isinstance(k, str) else k["pubkey"] for k in keys]
    progs = {keys[ix["programIdIndex"]] for ix in msg.get("instructions", []) if ix["programIdIndex"] < len(keys)}
    for inner in meta.get("innerInstructions") or []:
        progs |= {keys[ix["programIdIndex"]] for ix in inner["instructions"] if ix["programIdIndex"] < len(keys)}
    dsol = None
    if wallet in keys:
        i = keys.index(wallet)
        dsol = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
    pre = {b["mint"]: float(b["uiTokenAmount"]["uiAmountString"] or 0)
           for b in meta.get("preTokenBalances") or [] if b.get("owner") == wallet}
    post = {b["mint"]: float(b["uiTokenAmount"]["uiAmountString"] or 0)
            for b in meta.get("postTokenBalances") or [] if b.get("owner") == wallet}
    moved = {m: post.get(m, 0) - pre.get(m, 0) for m in set(pre) | set(post) if m != WSOL}
    moved = {m: d for m, d in moved.items() if d}
    tip = sum((meta["postBalances"][keys.index(t)] - meta["preBalances"][keys.index(t)]) / 1e9
              for t in TIP_ACCOUNTS if t in keys)
    return {"dsol": dsol, "fee": meta.get("fee", 0) / 1e9, "err": meta.get("err") is not None,
            "signer": keys[0] if keys else "", "tokens": moved,
            "programs": sorted(PROGRAMS.get(p, p) for p in progs if p in PROGRAMS), "tip": round(tip, 6)}


class Watch:
    def __init__(self, wallets: list[str], out_dir: Path) -> None:
        self.wallets, self.out_dir = wallets, out_dir
        self.http, self.wss = env("HTTP_URL"), env("WSS_URL")
        self.subs: dict[int, str] = {}       # subscription id -> wallet
        self.pending: dict[int, str] = {}    # request id -> wallet
        self.seen: set[str] = set()
        self.todo: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.n = 0

    def write(self, row: dict) -> None:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        with self.lock, (self.out_dir / f"swarmwatch-{day}.jsonl").open("a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
            self.n += 1

    def fetch(self, sig: str, wallet: str, recv: float) -> None:
        tx = None
        for wait in (0.5, 1.5, 4.0, 8.0):        # a confirmed tx is not always served at once
            time.sleep(wait)
            try:
                body = json.loads(http.post_json(self.http, {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                                                             "params": [sig, {"encoding": "json", "commitment": "confirmed",
                                                                              "maxSupportedTransactionVersion": 0}]}))
                tx = body.get("result")
            except Exception:
                tx = None
            if tx:
                break
        row = {"recv": recv, "wallet": wallet, "sig": sig}
        if tx:
            row.update(slot=tx["slot"], block_time=tx.get("blockTime"), **summarize(tx, wallet))
        else:
            row["missing"] = True
        self.write(row)

    def on_open(self, ws) -> None:
        self.subs.clear()
        for i, w in enumerate(self.wallets, start=1):
            self.pending[i] = w
            ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": "logsSubscribe",
                                "params": [{"mentions": [w]}, {"commitment": "confirmed"}]}))

    def on_message(self, _ws, msg: str) -> None:
        d = json.loads(msg)
        if "id" in d and "result" in d:
            self.subs[d["result"]] = self.pending.pop(d["id"], "")
            return
        p = (d.get("params") or {})
        v = ((p.get("result") or {}).get("value") or {})
        sig, wallet = v.get("signature"), self.subs.get(p.get("subscription"), "")
        if not sig or v.get("err") or sig in self.seen:
            return
        self.seen.add(sig)
        self.todo.put((sig, wallet, time.time()))

    def run(self, hours: float) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        pool = cf.ThreadPoolExecutor(4)

        def drain():
            while True:
                pool.submit(self.fetch, *self.todo.get())
        threading.Thread(target=drain, daemon=True).start()

        def socket():
            while True:
                app = websocket.WebSocketApp(self.wss, on_open=self.on_open, on_message=self.on_message)
                app.run_forever(ping_interval=30, ping_timeout=10)
                print(f"[{time.strftime('%H:%M:%S')}] socket dropped; reconnecting", flush=True)
                time.sleep(3)
        threading.Thread(target=socket, daemon=True).start()
        end = time.time() + hours * 3600
        while time.time() < end:
            time.sleep(60)
            print(f"[{time.strftime('%H:%M:%S')}] {len(self.subs)}/{len(self.wallets)} wallets subscribed, "
                  f"{self.n} rows", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallets", required=True, help="one pubkey per line")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "data"))
    a = ap.parse_args()
    wallets = [w.strip() for w in Path(a.wallets).read_text().split() if w.strip()]
    print(f"watching {len(wallets)} wallets for {a.hours:g} h -> {a.out}/swarmwatch-*.jsonl", flush=True)
    Watch(wallets, Path(a.out)).run(a.hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
