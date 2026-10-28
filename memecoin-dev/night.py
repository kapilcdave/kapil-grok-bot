"""Unattended launches from a batch of concepts, with a hard loss budget for the whole night.

    python night.py BATCH.json [--dev-buy-sol 0.05] [--hold-s 300] [--sell-at 0.3] [--gap-min 8 14] [--send]

BATCH.json is a list of {"name", "symbol", "description", "art_prompt", "tag"}. Each concept gets
Pollinations art (keyless; the watermark strip is cropped off) and one `launch_one.py run --fresh`:
a new creator wallet, the launch, a watch that sells 100% when outside SOL reaches --sell-at, a claim
and a sweep back to the main wallet.

Why small dev buys and a long hold (research 10-15, our 35 live coins + 122 live launches): real
cross-creator snipers bring ~0.05 SOL at a 0.3-0.8 SOL dev buy, so a big dev buy only buys a bigger
fee bill; the money on our coins came from 0-dev-buy SWARMS (AMDER 12.7 SOL, BEAD 7.7). A small
position cannot lose more than the round-trip fee (the curve never falls below its fresh state), so
it can afford to wait the ~2 min humans take to arrive.

Safety, in order: STOP file -> night budget (main wallet may not fall below (100 - --budget-pct)% of
the balance read when the night STARTED, kept in night.json so every batch shares it) -> a failed sell
stops the night (that wallet still holds tokens and needs a look) -> old fresh wallets whose creator
fees grew are topped up, claimed and swept after each launch.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from pathlib import Path

from PIL import Image

import launch_one
from devbot import image as image_mod, pump, wallets as wallets_mod

STATE = launch_one.STATE_DIR / "night.json"
STOP = launch_one.STATE_DIR / "NIGHT_STOP"
ART_DIR = launch_one.LOG_DIR / "night-art"
BUDGET_PCT = 25.0
CLAIM_MIN_SOL = 0.003     # a top-up + claim + sweep costs ~0.00002; below this it is not worth a look


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def art(prompt: str, path: Path, waits: tuple[float, ...] = (30, 90, 240)) -> Path:
    """Keyless Pollinations answers HTTP 402 after a burst (10-15: 1 image, then 402s), so back off."""
    if path.exists():
        return path
    for wait in (*waits, None):
        try:
            raw = image_mod._pollinations(prompt, "")
            break
        except Exception as e:
            if wait is None:
                raise
            log(f"  art: {str(e)[:60]}; retrying in {wait:g}s")
            time.sleep(wait)
    with Image.open(io.BytesIO(raw)) as im:
        w, h = im.size
        cut = int(h * 0.1)                       # the keyless watermark sits in the bottom ~8%
        im = im.convert("RGB").crop((cut // 2, 0, w - cut // 2, h - cut))
        im.save(path, "JPEG", quality=92)
    return path


def night_state(rpc: pump.Rpc, main: str) -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    st = {"start_balance": rpc.balance_sol(main), "started": time.time(), "done": []}
    STATE.write_text(json.dumps(st))
    return st


def harvest(rpc: pump.Rpc, main_kp, send: bool) -> None:
    """Claim creator fees that kept accruing on earlier fresh wallets' coins, then sweep them back."""
    ws = wallets_mod.Wallets(launch_one.WALLETS_DIR)
    for pk in ws.pubkeys():
        kp = ws.get(pk)
        fees = pump.unclaimed_fees_sol(rpc, kp.pubkey())
        if fees < CLAIM_MIN_SOL:
            continue
        log(f"harvest {pk[:6]}: {fees:.6f} SOL unclaimed")
        if not send:
            continue
        try:
            wallets_mod.top_up(rpc, main_kp, kp)
            pump.claim_creator_fees(rpc, kp, priority_fee_sol=launch_one.PRIORITY_FEE_SOL)
            log(f"  claimed; swept {wallets_mod.sweep(rpc, main_kp, kp)}")
        except RuntimeError as e:
            log(f"  harvest failed: {e}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--dev-buy-sol", type=float, default=0.05)
    ap.add_argument("--hold-s", type=float, default=300)
    ap.add_argument("--sell-at", type=float, default=0.3)
    ap.add_argument("--gap-min", type=float, nargs=2, default=(8, 14))
    ap.add_argument("--budget-pct", type=float, default=BUDGET_PCT)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--art-only", action="store_true", help="pre-generate every concept's art, then exit")
    a = ap.parse_args(argv)
    if a.art_only:
        ART_DIR.mkdir(parents=True, exist_ok=True)
        for c in json.loads(Path(a.batch).read_text()):
            log(f"art {c['symbol']}: {art(c['art_prompt'], ART_DIR / (c['symbol'].lower() + '.jpg'))}")
        return 0

    rpc = pump.Rpc(launch_one.rpc_urls())
    main_kp = pump.load_keypair(launch_one.KEYPAIR_PATH)
    main_pk = str(main_kp.pubkey())
    st = night_state(rpc, main_pk)
    floor = st["start_balance"] * (1 - a.budget_pct / 100)
    ART_DIR.mkdir(parents=True, exist_ok=True)
    concepts = json.loads(Path(a.batch).read_text())
    log(f"night started at {st['start_balance']:.4f} SOL; stop below {floor:.4f} ({a.budget_pct:g}% budget); "
        f"{len(concepts)} concepts, dev buy {a.dev_buy_sol:g}, hold {a.hold_s:g}s, sell at +{a.sell_at:g}")

    for i, c in enumerate(concepts):
        key = f"{c['symbol']}|{c['name']}"
        if key in st["done"]:
            continue
        if STOP.exists():
            log(f"STOP file {STOP} present: stopping")
            return 0
        bal = rpc.balance_sol(main_pk)
        need = pump.LAUNCH_COST_SOL + a.dev_buy_sol * 1.03 + launch_one.FRESH_BUFFER_SOL
        if bal - need < floor:
            log(f"BUDGET: main {bal:.4f} - {need:.4f} would cross the night floor {floor:.4f}: stopping")
            return 0
        log(f"[{i + 1}/{len(concepts)}] {c['name']} (${c['symbol']}) tag={c.get('tag', '')}  main {bal:.4f}")
        try:
            path = art(c["art_prompt"], ART_DIR / f"{c['symbol'].lower()}.jpg")
        except Exception as e:                      # art is not worth a launch without it
            log(f"  art failed ({e}); skipping this concept")
            continue
        args = ["run", "--name", c["name"], "--symbol", c["symbol"], "--art", str(path),
                "--description", c["description"], "--dev-buy-sol", str(a.dev_buy_sol), "--fresh",
                "--floor-pct", "5", "--tag", c.get("tag", ""), "--hold-s", str(a.hold_s),
                "--sell-at", str(a.sell_at)] + (["--send"] if a.send else [])
        try:
            rc = launch_one.main(args)
        except Exception as e:
            log(f"  launch raised {type(e).__name__}: {e}; stopping the night for a look")
            return 3
        st["done"].append(key)
        STATE.write_text(json.dumps(st))
        if rc != 0:
            log(f"  launch_one exited {rc} (create unconfirmed or sell failed): stopping the night for a look")
            return rc
        harvest(rpc, main_kp, a.send)
        if not a.send:
            continue
        if i < len(concepts) - 1:
            gap = random.uniform(*a.gap_min) * 60
            log(f"  next launch in {gap / 60:.1f} min")
            time.sleep(gap)
    log(f"batch done; main {rpc.balance_sol(main_pk):.4f} SOL (night start {st['start_balance']:.4f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
