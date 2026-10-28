"""Launch one pump.fun coin with a bundled dev buy, and get the dev buy back out.

Why this file exists. `research/RESULT-bot-draw-20261015.md` measured the bundled dev buy as the only
large bot-arrival lever (P(broad sniper in our launch block) 3-5% at no dev buy, 29-46% at 0.5-1 SOL;
median early bot buyers 0 -> 4-5). The loss on that dev buy is bounded by the curve itself -- a
pump.fun curve is reversible and cannot fall below its fresh state, so recovery has a hard floor of
`30/(30+2b) * 0.9875/1.0125` (94.4% at b=0.5) -- but ONLY if the sell lands. `devbot/bot.py` is the
only code in this repo that sells, and it needs Telegram and Cloudflare credentials this machine does
not have, so the exit lives here. Everything that signs or spends still goes through `devbot.pump`.

    python launch_one.py run    --name N --symbol S --art PATH --dev-buy-sol 0.5 --fresh --tag T --send
    python launch_one.py plan   --name N --symbol S --art PATH --dev-buy-sol 0.5
    python launch_one.py launch --name N --symbol S --art PATH --dev-buy-sol 0.5 --send
    python launch_one.py watch  --mint MINT --hold-s 60 --send      # then sells 100%
    python launch_one.py sell   --mint MINT --pct 100 --send
    python launch_one.py status --mint MINT
    python launch_one.py sweep  --wallet PUBKEY --send

`run` is the whole experiment in one process: a new creator wallet (`--fresh`), the launch, a fast
watch that sells 100% the moment outside SOL on the curve reaches `--sell-at`, a creator-fee claim and
a sweep back to the main wallet. Every live coin from the main wallet after its 2nd drew 0 launch-block
snipers (33 coins, 10-13..10-15), while both first coins from fresh wallets drew buyers; and the bot
swarms that did come (AMDER 12.7 SOL, BEAD 7.7) bought at ~8 s and dumped ~1-2 s later, so a separate
`watch` started by hand would miss them. One row per run goes to research/data/experiments.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from solders.keypair import Keypair

from devbot import http, image as image_mod, ledger as ledger_mod, pump, wallets as wallets_mod

KEYPAIR_PATH = Path(os.environ.get("DEV_KEYPAIR_PATH")
                    or "~/.config/solana/solana-rust-frankfurt-trading.json").expanduser()
STATE_DIR = Path(os.environ.get("DEVBOT_STATE", "~/.devbot")).expanduser()
ENV_PATH = STATE_DIR / ".env"
RPC_ENVS = ((Path("~/.config/solana-rpc/quicknode.env").expanduser(), "QUICKNODE_SOLANA_HTTP"),
            (Path("~/.config/solana-rpc/helius.env").expanduser(), "HTTP_URL"))
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
LOG_DIR = Path(__file__).resolve().parent / "research" / "data"
WALLETS_DIR = STATE_DIR / "wallets"
# xrun's creator wallets live apart from devbot's rotated store: devbot.bot sweep_idle() empties any idle wallet
# in WALLETS_DIR back to main, and it swept a freshly funded 3.25 SOL xrun wallet on 10-16 09:07Z.
XRUN_WALLETS_DIR = STATE_DIR / "xrun-wallets"
EXPERIMENTS = LOG_DIR / "experiments.jsonl"

PRIORITY_FEE_SOL = 0.00005   # in-block position does not respond to fees (research 2026-10-13)
SLIPPAGE_PCT = 10
MAYHEM = False               # -6.7/-5.4pp launch-block snipers at no fee gain (research 2026-10-14)
MAX_DEV_BUY_SOL = 0.55       # hard ceiling, as devbot/bot.py:50 does for /devbuy
FLOOR_PCT = 20.0             # keep this % of the balance read at start untouchable
HOLD_S = 60.0                # flat hold; trailing/take-initial exits measured WORSE (research 10-11)
POLL_S = 0.25                # swarms dump ~1-2 s after they buy (AMDER, BEAD, REFFS)
SELL_AT_SOL = 0.3            # outside SOL on the curve that triggers the exit; 0 = hold to the deadline
FRESH_BUFFER_SOL = 0.008     # a fresh wallet's token-account rent + sell and claim fees, swept back after
FRESH_FEE_PCT = 3.0          # on top of the dev buy: pump 1.25% + PumpPortal 0.5% + slack (L1 sim failed at 0)
SELL_ATTEMPTS = 3
SETTLE_S = 3.0               # let a send settle before re-reading the token balance
WEBSITE_BASE = "https://coins.memephil.workers.dev"
DISCLOSURE = "Dev buy: {dev_buy:g} SOL. Dev wallet: {wallet}. Dev sells are posted publicly."


def _env(path: Path, key: str) -> str:
    """A KEY=value file, tolerant of a missing file (launch_memephil.py:43 crashes on one)."""
    if not path.exists():
        return ""
    for line in path.read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def rpc_urls() -> list[str]:
    urls = [u for path, key in RPC_ENVS if (u := _env(path, key))]
    return urls + [PUBLIC_RPC]


def pinata_jwt() -> str:
    return os.environ.get("PINATA_JWT", "").strip() or _env(ENV_PATH, "PINATA_JWT")


def token_amount(rpc: pump.Rpc, owner: str, mint: str) -> int:
    """Raw token units of `mint` held by `owner` (0 when there is no account). The sell guard reads
    this, so it must never raise on a missing account -- that is the normal post-sell state."""
    rows = rpc.call("getTokenAccountsByOwner", [owner, {"mint": mint},
                                                {"encoding": "jsonParsed", "commitment": "confirmed"}])
    total = 0
    for row in rows.get("value", []):
        info = row["account"]["data"]["parsed"]["info"]
        total += int(info["tokenAmount"]["amount"])
    return total


def signer(wallet: str) -> Keypair:
    """The main dev wallet, or a creator wallet from `--wallet` (xrun's own store first, then devbot's)."""
    if not wallet:
        return pump.load_keypair(KEYPAIR_PATH)
    if (XRUN_WALLETS_DIR / f"{wallet}.json").exists():
        return wallets_mod.Wallets(XRUN_WALLETS_DIR).get(wallet)
    return wallets_mod.Wallets(WALLETS_DIR).get(wallet)


def check_floor(balance: float, cost: float, floor_pct: float = FLOOR_PCT,
                max_dev_buy: float = MAX_DEV_BUY_SOL,
                launch_cost: float = pump.LAUNCH_COST_SOL) -> ledger_mod.Verdict:
    """The floor is a PERCENT of the balance read right now, never a hardcoded SOL figure: an absolute
    floor goes stale on every deposit and withdrawal (operator rule, and devbot's own `check_launch`
    floor is computed off a `baseline_sol` that does not re-base on deposit)."""
    floor = balance * floor_pct / 100.0
    if cost > max_dev_buy + launch_cost + PRIORITY_FEE_SOL:     # j7 costs 0.0184, PumpPortal 0.0098
        return ledger_mod.Verdict(False, f"cost {cost:.4f} SOL is over the hard ceiling")
    if balance - cost < floor:
        return ledger_mod.Verdict(
            False, f"balance floor: {balance:.4f} - {cost:.4f} < {floor:.4f} ({floor_pct:g}% of balance)")
    return ledger_mod.Verdict(True, "")


def art_bytes(art: str) -> bytes:
    """`--art` is a local file or an http(s) URL, as launch_brand_coin.py:64 does."""
    if art.startswith("http://") or art.startswith("https://"):
        raw = http.request("GET", image_mod.source_url(art), headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    else:
        raw = Path(art).expanduser().read_bytes()
    return image_mod.fit(raw)


def sell_out(rpc: pump.Rpc, dev: Keypair, mint: str, pct: int, *, ledger=None,
             attempts: int = SELL_ATTEMPTS, settle_s: float = SETTLE_S) -> bool:
    """Sell `pct`% and make sure it actually happened.

    `pump.sell` is a PERCENTAGE sell that is sent once and never resent, and `Rpc.confirm` returns
    False on timeout while RAISING on an on-chain error. So a blind retry could sell twice from the
    remainder: every retry re-reads the token balance first and only resends while tokens remain.
    """
    owner = str(dev.pubkey())
    for attempt in range(1, attempts + 1):
        held = token_amount(rpc, owner, mint)
        if held == 0:
            print(f"  nothing left to sell (attempt {attempt}): token balance is 0")
            return True
        before = rpc.balance_sol(owner)
        print(f"  attempt {attempt}: selling {pct}% of {held} units, wallet at {before:.4f} SOL")
        sig = ""
        try:
            out = pump.sell(rpc, dev, mint, pct, slippage_pct=SLIPPAGE_PCT,
                            priority_fee_sol=PRIORITY_FEE_SOL)
            sig, confirmed = out.signature, out.confirmed
        except RuntimeError as e:          # on-chain error, or every endpoint rate-limited
            print(f"  sell raised: {e}")
            confirmed = False
        if confirmed:
            after = rpc.balance_sol(owner)
            print(f"  CONFIRMED {sig}: wallet {before:.4f} -> {after:.4f} SOL ({after - before:+.4f})")
            if ledger is not None:
                ledger.record_sale(mint, pct, sig, after - before)
            return True
        time.sleep(settle_s)
        if token_amount(rpc, owner, mint) == 0:   # landed after the confirm window
            after = rpc.balance_sol(owner)
            print(f"  landed late ({sig or 'no sig'}): wallet {before:.4f} -> {after:.4f} SOL")
            if ledger is not None:
                ledger.record_sale(mint, pct, sig, after - before)
            return True
        print("  not confirmed and tokens still held; retrying")
    print(f"\nSELL FAILED after {attempts} attempts. The position is still held. Run:\n"
          f"  python launch_one.py sell --mint {mint} --pct {pct} --send", file=sys.stderr)
    return False


def launch(a: argparse.Namespace) -> tuple[int, str, Keypair | None, pump.Rpc]:
    """(exit code, mint, creator keypair, rpc). With `--fresh` a new creator wallet is made and funded
    from the main wallet; the floor is always checked against the MAIN wallet's live balance."""
    jwt = pinata_jwt()
    if a.send and not jwt:
        print(f"no PINATA_JWT in the environment or {ENV_PATH}", file=sys.stderr)
        return 1, "", None, None
    rpc = pump.Rpc(rpc_urls())
    main = pump.load_keypair(KEYPAIR_PATH)
    dev = Keypair() if a.fresh and not a.send else main   # a dry run never writes a key file
    mint = Keypair()
    art = art_bytes(a.art)
    cost = pump.LAUNCH_COST_SOL + a.dev_buy_sol + PRIORITY_FEE_SOL
    fund_sol = cost + a.dev_buy_sol * FRESH_FEE_PCT / 100 + FRESH_BUFFER_SOL if a.fresh else 0.0
    balance = rpc.balance_sol(str(main.pubkey()))
    v = check_floor(balance, max(cost, fund_sol), a.floor_pct)
    if a.fresh and a.send and v.ok:
        dev = wallets_mod.Wallets(WALLETS_DIR).new()
    website = f"{WEBSITE_BASE}/{mint.pubkey()}"
    description = a.description + "\n\n" + DISCLOSURE.format(dev_buy=a.dev_buy_sol, wallet=dev.pubkey())

    print(f"main wallet {main.pubkey()}  {balance:.4f} SOL")
    if a.fresh:
        print(f"creator     {dev.pubkey()}  FRESH, funded {fund_sol:.4f} SOL from main"
              + ("" if a.send else " (dry run: throwaway key, not saved)"))
    print(f"mint        {mint.pubkey()}")
    print(f"cost        {cost:.4f} SOL (create {pump.LAUNCH_COST_SOL}, dev buy {a.dev_buy_sol:g}, "
          f"priority {PRIORITY_FEE_SOL})")
    print(f"floor       {balance * a.floor_pct / 100:.4f} SOL ({a.floor_pct:g}% of balance) -> "
          f"{'OK' if v.ok else 'REFUSED: ' + v.reason}")
    print(f"mayhem      {MAYHEM}   slippage {SLIPPAGE_PCT}%   art {len(art)} bytes PNG")
    print(json.dumps(pump.metadata(name=a.name, symbol=a.symbol, description=description,
                                   image_uri="<pinned on --send>", website=website), indent=2))
    if not v.ok:
        return 1, "", None, rpc
    if not a.send:
        print("\ndry run: nothing pinned, nothing sent. Re-run with --send to launch.")
        return 0, "", None, rpc

    image_uri = pump.pin_image(jwt, a.symbol, art)
    uri = pump.pin_metadata(jwt, name=a.name, symbol=a.symbol, description=description,
                            image_uri=image_uri, website=website)
    print(f"image       {image_uri}")
    print(f"metadata    {uri}")
    ledger = ledger_mod.Ledger(STATE_DIR / "devbot.sqlite")
    if a.fresh:
        sig = wallets_mod.fund(rpc, main, dev.pubkey(), fund_sol)
        print(f"funded      {dev.pubkey()} with {fund_sol:.4f} SOL ({sig})")
    ledger.record_spend(cost, f"launch {a.symbol}")   # before the send: a broadcast that errors still spent
    try:
        out = pump.create(rpc, dev, mint, name=a.name, symbol=a.symbol, uri=uri, dev_buy_sol=a.dev_buy_sol,
                          slippage_pct=SLIPPAGE_PCT, priority_fee_sol=PRIORITY_FEE_SOL, mayhem=MAYHEM)
    except RuntimeError:
        if a.fresh and not pump.curve_real_sol(rpc, str(mint.pubkey())):   # no coin: give the SOL back
            print(f"create failed; sweeping {dev.pubkey()} back to main: {wallets_mod.sweep(rpc, main, dev)}")
        raise
    ledger.record_launch(mint=out.mint, name=a.name, symbol=a.symbol, narrative=a.tag or "launch_one",
                         source_mint="", source_name="", source_symbol="", uri=uri,
                         signature=out.signature, confirmed=out.confirmed, dev_buy_sol=a.dev_buy_sol)
    ledger.record_pins(out.mint, [pump.cid_of(image_uri), pump.cid_of(uri)])
    print(f"\n{'LIVE' if out.confirmed else 'SENT (not confirmed yet)'}: {a.name} (${a.symbol})")
    print(f"mint        {out.mint}")
    print(f"pump.fun    https://pump.fun/coin/{out.mint}")
    print(f"tx          https://solscan.io/tx/{out.signature}")
    return (0 if out.confirmed else 2), out.mint, dev, rpc


def cmd_launch(a: argparse.Namespace) -> int:
    rc, mint, dev, _ = launch(a)
    if rc == 0 and mint and a.dev_buy_sol > 0:
        w = f" --wallet {dev.pubkey()}" if a.fresh else ""
        print(f"\nNEXT: python launch_one.py watch --mint {mint}{w} --dev-buy-sol {a.dev_buy_sol:g} --send")
    return rc


def watch(rpc: pump.Rpc, mint: str, *, dev_buy_sol: float, hold_s: float, sell_at: float,
          poll_s: float = POLL_S, clock=time.monotonic, sleep=time.sleep) -> dict:
    """Log the curve's real SOL until outside SOL (real SOL minus our own dev buy) reaches `sell_at`
    or `hold_s` passes. Returns {"reason": "trigger"|"deadline", "dt", "outside", "peak_outside"}."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"live-{mint}.jsonl"
    t0 = clock()
    peak, next_line, outside, dt = 0.0, 0.0, 0.0, 0.0
    with log.open("a") as f:
        while (dt := clock() - t0) < hold_s:
            try:
                real = pump.curve_real_sol(rpc, mint)
            except (RuntimeError, OSError) as e:
                real, err = None, str(e)[:120]
            else:
                err = ""
            f.write(json.dumps({"ts": time.time(), "dt": round(dt, 2), "real_sol": real, "err": err}) + "\n")
            f.flush()
            if real is not None:
                outside = real - dev_buy_sol
                peak = max(peak, outside)
                if sell_at > 0 and outside >= sell_at:
                    print(f"  t+{dt:5.2f}s  outside {outside:+.4f} SOL >= {sell_at:g}: TRIGGER", flush=True)
                    return {"reason": "trigger", "dt": round(dt, 2), "outside": outside, "peak_outside": peak}
                if dt >= next_line:                   # one line a second, not four
                    print(f"  t+{dt:5.1f}s  curve {real:7.4f} SOL  outside {outside:+.4f}", flush=True)
                    next_line += 1
            sleep(poll_s)
    print(f"  held {hold_s:g}s; peak outside {peak:+.4f} SOL; samples in {log}")
    return {"reason": "deadline", "dt": round(dt, 2), "outside": outside, "peak_outside": peak}


def cmd_watch(a: argparse.Namespace) -> int:
    rpc = pump.Rpc(rpc_urls())
    dev = signer(a.wallet)
    watch(rpc, a.mint, dev_buy_sol=a.dev_buy_sol, hold_s=a.hold_s, sell_at=a.sell_at)
    if not a.send:
        print(f"dry run: not selling. Re-run with --send, or: "
              f"python launch_one.py sell --mint {a.mint} --pct 100 --send")
        return 0
    ledger = ledger_mod.Ledger(STATE_DIR / "devbot.sqlite")
    print("selling 100% of the dev position:")
    return 0 if sell_out(rpc, dev, a.mint, 100, ledger=ledger) else 2


def settle_fresh(rpc: pump.Rpc, main: Keypair, dev: Keypair, mint: str) -> dict:
    """Claim the fresh wallet's creator fees, then sweep every lamport back to main. Refuses to sweep
    while the wallet still holds the coin: it needs SOL to pay for the sell retry."""
    out = {"claimed": 0.0, "swept": 0.0}
    if token_amount(rpc, str(dev.pubkey()), mint) > 0:
        print(f"  NOT sweeping {dev.pubkey()}: it still holds {mint}", file=sys.stderr)
        return out
    fees = pump.unclaimed_fees_sol(rpc, dev.pubkey())
    if fees >= 0.0005:
        try:
            sig = pump.claim_creator_fees(rpc, dev, priority_fee_sol=PRIORITY_FEE_SOL)
            out["claimed"] = fees
            print(f"  claimed {fees:.6f} SOL creator fees ({sig})")
        except RuntimeError as e:
            print(f"  claim failed ({e}); fees stay in the vault, key kept in {WALLETS_DIR}")
    swept = wallets_mod.sweep(rpc, main, dev)
    if swept:
        out["swept"] = swept[0]
        print(f"  swept {swept[0]:.6f} SOL back to main ({swept[1]})")
    return out


def cmd_run(a: argparse.Namespace) -> int:
    rpc = pump.Rpc(rpc_urls())
    main = pump.load_keypair(KEYPAIR_PATH)
    before = rpc.balance_sol(str(main.pubkey()))
    t_launch = time.time()
    rc, mint, dev, rpc = launch(a)
    if rc != 0 or not mint:
        if rc == 2 and mint:
            print(f"create not confirmed: check https://pump.fun/coin/{mint} before anything else",
                  file=sys.stderr)
        return rc
    w = watch(rpc, mint, dev_buy_sol=a.dev_buy_sol, hold_s=a.hold_s, sell_at=a.sell_at)
    ledger = ledger_mod.Ledger(STATE_DIR / "devbot.sqlite")
    print("selling 100% of the dev position:")
    sold = sell_out(rpc, dev, mint, 100, ledger=ledger) if a.dev_buy_sol > 0 else True
    settled = settle_fresh(rpc, main, dev, mint) if (a.fresh and sold) else {}
    time.sleep(SETTLE_S)
    after = rpc.balance_sol(str(main.pubkey()))
    row = {"ts": t_launch, "tag": a.tag, "mint": mint, "creator": str(dev.pubkey()), "fresh": a.fresh,
           "name": a.name, "symbol": a.symbol, "dev_buy_sol": a.dev_buy_sol, "sell_at": a.sell_at,
           "hold_s": a.hold_s, **{f"watch_{k}": v for k, v in w.items()}, "sold": sold, **settled,
           "main_before": before, "main_after": after, "pnl_sol": after - before}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with EXPERIMENTS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"\nRESULT {a.symbol}: exit on {w['reason']} at t+{w['dt']}s, peak outside {w['peak_outside']:+.4f} SOL; "
          f"main {before:.4f} -> {after:.4f} ({after - before:+.4f} SOL)")
    return 0 if sold else 2


def cmd_sell(a: argparse.Namespace) -> int:
    rpc = pump.Rpc(rpc_urls())
    dev = signer(a.wallet)
    held = token_amount(rpc, str(dev.pubkey()), a.mint)
    print(f"dev wallet  {dev.pubkey()}  {rpc.balance_sol(str(dev.pubkey())):.4f} SOL")
    print(f"holding     {held} raw units of {a.mint}")
    if not a.send:
        print("dry run: nothing sent. Re-run with --send to sell.")
        return 0
    ledger = ledger_mod.Ledger(STATE_DIR / "devbot.sqlite")
    return 0 if sell_out(rpc, dev, a.mint, a.pct, ledger=ledger) else 2


def cmd_status(a: argparse.Namespace) -> int:
    rpc = pump.Rpc(rpc_urls())
    dev = signer(a.wallet)
    owner = str(dev.pubkey())
    print(f"dev wallet  {owner}  {rpc.balance_sol(owner):.4f} SOL")
    print(f"unclaimed   {pump.unclaimed_fees_sol(rpc, dev.pubkey()):.6f} SOL of creator fees")
    if a.mint:
        print(f"holding     {token_amount(rpc, owner, a.mint)} raw units")
        print(f"curve       {pump.curve_real_sol(rpc, a.mint)} real SOL")
    return 0


def cmd_fund(a: argparse.Namespace) -> int:
    """Move SOL from the main wallet to a fresh creator wallet in the store, directly (one visible transfer:
    no hops; the funding link is on-chain by design). Keeps 5% of main's balance."""
    rpc = pump.Rpc(rpc_urls())
    main_kp, dev = pump.load_keypair(KEYPAIR_PATH), signer(a.wallet)
    bal = rpc.balance_sol(str(main_kp.pubkey()))
    print(f"main {main_kp.pubkey()} {bal:.4f} SOL -> {a.sol:g} SOL -> {dev.pubkey()} "
          f"({rpc.balance_sol(str(dev.pubkey())):.4f} SOL now)")
    if a.sol <= 0 or bal - a.sol < bal * 0.05:
        print(f"refused: {a.sol:g} SOL would leave main under 5% of {bal:.4f}", file=sys.stderr)
        return 1
    if not a.send:
        print("dry run: nothing sent. Re-run with --send to fund.")
        return 0
    print(f"sent {wallets_mod.fund(rpc, main_kp, dev.pubkey(), a.sol)}")
    time.sleep(SETTLE_S)
    print(f"fresh wallet now {rpc.balance_sol(str(dev.pubkey())):.4f} SOL")
    return 0


def cmd_sweep(a: argparse.Namespace) -> int:
    rpc = pump.Rpc(rpc_urls())
    dev = signer(a.wallet)
    print(f"fresh wallet {dev.pubkey()}  {rpc.balance_sol(str(dev.pubkey())):.6f} SOL, "
          f"unclaimed fees {pump.unclaimed_fees_sol(rpc, dev.pubkey()):.6f}")
    if not a.send:
        print("dry run: nothing sent. Re-run with --send to claim and sweep.")
        return 0
    settle_fresh(rpc, pump.load_keypair(KEYPAIR_PATH), dev, a.mint) if a.mint else \
        print(wallets_mod.sweep(rpc, pump.load_keypair(KEYPAIR_PATH), dev))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def coin_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--name", required=True)
        p.add_argument("--symbol", required=True)
        p.add_argument("--art", required=True, help="local file or http(s) URL")
        p.add_argument("--description", default="")
        p.add_argument("--dev-buy-sol", type=float, required=True)
        p.add_argument("--floor-pct", type=float, default=FLOOR_PCT)
        p.add_argument("--fresh", action="store_true", help="launch from a new creator wallet")
        p.add_argument("--tag", default="", help="experiment label, kept in the ledger and experiments.jsonl")

    p = sub.add_parser("plan", help="print cost, floor and metadata; send nothing")
    coin_args(p)
    p = sub.add_parser("launch", help="create the coin with the dev buy bundled in the create tx")
    coin_args(p)
    p.add_argument("--send", action="store_true")
    p = sub.add_parser("run", help="launch, watch, sell on arrival, claim and sweep: one experiment")
    coin_args(p)
    p.add_argument("--hold-s", type=float, default=HOLD_S)
    p.add_argument("--sell-at", type=float, default=SELL_AT_SOL)
    p.add_argument("--send", action="store_true")
    p = sub.add_parser("watch", help="log the curve, then sell 100% on the trigger or the deadline")
    p.add_argument("--mint", required=True)
    p.add_argument("--wallet", default="", help="fresh creator wallet pubkey (default: main)")
    p.add_argument("--hold-s", type=float, default=HOLD_S)
    p.add_argument("--sell-at", type=float, default=SELL_AT_SOL)
    p.add_argument("--dev-buy-sol", type=float, default=0.0, help="our own SOL on the curve")
    p.add_argument("--send", action="store_true")
    p = sub.add_parser("sell", help="sell pct% of the dev position, verified")
    p.add_argument("--mint", required=True)
    p.add_argument("--wallet", default="")
    p.add_argument("--pct", type=int, default=100)
    p.add_argument("--send", action="store_true")
    p = sub.add_parser("status")
    p.add_argument("--mint", default="")
    p.add_argument("--wallet", default="")
    p = sub.add_parser("fund", help="move SOL from main to a fresh wallet in the store")
    p.add_argument("--wallet", required=True)
    p.add_argument("--sol", type=float, required=True)
    p.add_argument("--send", action="store_true")
    p = sub.add_parser("sweep", help="claim a fresh wallet's fees and sweep it back to main")
    p.add_argument("--wallet", required=True)
    p.add_argument("--mint", default="", help="refuse to sweep while this coin is still held")
    p.add_argument("--send", action="store_true")

    a = ap.parse_args(argv)
    if a.cmd in ("plan", "launch", "run"):
        if a.dev_buy_sol > MAX_DEV_BUY_SOL:
            print(f"--dev-buy-sol {a.dev_buy_sol} is over the {MAX_DEV_BUY_SOL} SOL ceiling", file=sys.stderr)
            return 1
        a.send = getattr(a, "send", False)
        if a.cmd == "run" and not a.send:
            return cmd_launch(a)              # a dry run of `run` is a plan
        return cmd_run(a) if a.cmd == "run" else cmd_launch(a)
    return {"watch": cmd_watch, "sell": cmd_sell, "status": cmd_status, "sweep": cmd_sweep,
            "fund": cmd_fund}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
