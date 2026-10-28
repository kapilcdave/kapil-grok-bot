"""memephil's own launches: create plus disclosed treasury buys, in one block.

Three steps, in this order, because the disclosure has to exist before the buy does:

    # 1. make the wallets and write the manifest (keys stay in $DEVBOT_STATE/treasury, 0600)
    .venv/bin/python launch_brand_coin.py wallets --new 3 --rule "memephil's own wallets. They buy
        in the launch block and hold. Any sale is posted on @0xmemephil before it is sent."

    # 2. publish it, so the list is public before any coin exists
    cd site && npx wrangler deploy

    # 3. launch (dry run by default; --send pins, funds, bundles)
    .venv/bin/python launch_brand_coin.py launch --name philLAMPthropy --symbol PHILAMP \
        --art site/public/memephil.jpg --description "..." --buy-sol 0.01 --send

Step 3 refuses to run if the live manifest does not already name every wallet that is about to buy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from devbot import http, image as image_mod, jito, pump, treasury as treasury_mod
from devbot import wallets as wallets_mod
from solders.keypair import Keypair

WEBSITE_BASE = "https://coins.memephil.workers.dev"
MANIFEST_URL = f"{WEBSITE_BASE}/{treasury_mod.MANIFEST_NAME}"
MANIFEST_FILE = Path(__file__).resolve().parent / "site" / "public" / treasury_mod.MANIFEST_NAME
TWITTER = "https://x.com/0xmemephil"
DISCLOSURE = "Dev buy: {dev_buy:g} SOL. Dev wallet: {wallet}. Dev sells are posted publicly."

KEYPAIR_PATH = Path(os.environ.get("DEV_KEYPAIR_PATH")
                    or "~/.config/solana/solana-rust-frankfurt-trading.json").expanduser()
STATE_DIR = Path(os.environ.get("DEVBOT_STATE", "~/.devbot")).expanduser()   # same variable as the bot
HELIUS_ENV = Path("~/.config/solana-rpc/helius.env").expanduser()

DEFAULT_TIP_SOL = 0.0002     # above Jito's 1000-lamport floor; a launch slot is not a contested one
SLIPPAGE_PCT = 10
BALANCE_MARGIN_SOL = 0.005   # left in the dev wallet after everything, for a later claim's own fee


def _env(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def _rpc() -> pump.Rpc:
    return pump.Rpc([_env(HELIUS_ENV, "HTTP_URL") or "https://api.mainnet-beta.solana.com"])


def _store() -> treasury_mod.Treasury:
    return treasury_mod.Treasury(STATE_DIR / "treasury")


def _art(source: str) -> bytes:
    raw = (Path(source).read_bytes() if not source.startswith("http")
           else http.request("GET", image_mod.source_url(source), headers={"User-Agent": "Mozilla/5.0"}, timeout=60))
    return image_mod.fit(raw)


def cmd_wallets(args: argparse.Namespace) -> int:
    store = _store()
    made = store.new_keys(args.new) if args.new else []
    pubkeys = store.pubkeys()
    if not pubkeys:
        print("no treasury wallets yet: run with --new N", file=sys.stderr)
        return 1
    dev = pump.load_keypair(KEYPAIR_PATH)
    man = treasury_mod.manifest(pubkeys, dev_wallet=str(dev.pubkey()), rule=args.rule)
    treasury_mod.write_manifest(args.manifest_file, man)
    for p in pubkeys:
        print(f"{'new  ' if p in made else '     '}{p}")
    print(f"\nwrote {args.manifest_file}")
    print(f"publish it before launching:  cd site && npx wrangler deploy   ->  {MANIFEST_URL}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = _store()
    rpc = _rpc()
    pubkeys = store.pubkeys()
    print(f"local keys  {len(pubkeys)} in {store.path}")
    balances = wallets_mod.lamports_of(rpc, pubkeys) if pubkeys else {}
    for p in pubkeys:
        print(f"  {p}  {balances.get(p, 0) / pump.LAMPORTS:.4f} SOL")
    try:
        man = treasury_mod.published(args.manifest_url)
    except Exception as e:
        print(f"published    not readable at {args.manifest_url}: {e}")
        return 1
    print(f"published   {len(man.get('treasury') or [])} at {args.manifest_url} (updated {man.get('updated')})")
    print(f"rule        {man.get('rule')}")
    unpublished = [p for p in pubkeys if p not in set(man.get("treasury") or [])]
    if unpublished:
        print(f"NOT PUBLISHED yet: {', '.join(unpublished)} (deploy the site before they buy anything)")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    jwt = _env(STATE_DIR / ".env", "PINATA_JWT")
    if args.send and not jwt:     # a dry run pins nothing, so it does not need the credential
        print(f"no PINATA_JWT in {STATE_DIR / '.env'}", file=sys.stderr)
        return 1
    store = _store()
    buyers = [w.strip() for w in args.wallets.split(",") if w.strip()] if args.wallets else store.pubkeys()
    buyers = buyers[:treasury_mod.MAX_WALLETS]
    if not buyers:
        print("no treasury wallets: run the wallets subcommand first", file=sys.stderr)
        return 1

    # The guard this whole path exists for: the list is public before the block, or there is no launch.
    try:
        treasury_mod.check_published(args.manifest_url, buyers)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    rpc = _rpc()
    dev = pump.load_keypair(KEYPAIR_PATH)
    mint = Keypair()
    art = _art(args.art)
    website = f"{WEBSITE_BASE}/{mint.pubkey()}"
    description = "\n\n".join([
        args.description,
        treasury_mod.disclosure(buyers, args.manifest_url),
        DISCLOSURE.format(dev_buy=args.dev_buy_sol, wallet=dev.pubkey()),
    ])
    cost = treasury_mod.cost_sol(n_wallets=len(buyers), buy_sol=args.buy_sol,
                                dev_buy_sol=args.dev_buy_sol, tip_sol=args.tip_sol)
    balance = rpc.balance_sol(str(dev.pubkey()))
    held = wallets_mod.lamports_of(rpc, buyers)     # funding only tops up the shortfall, so what they hold is already paid
    need_each = args.buy_sol + treasury_mod.BUY_OVERHEAD_SOL
    cost -= sum(min(held.get(p, 0) / pump.LAMPORTS, need_each) for p in buyers)
    print(f"dev wallet  {dev.pubkey()}  {balance:.4f} SOL")
    print(f"mint        {mint.pubkey()}")
    print(f"bundle      1 create (dev buy {args.dev_buy_sol:g} SOL, tip {args.tip_sol:g}) "
          f"+ {len(buyers)} buys of {args.buy_sol:g} SOL, max {jito.MAX_TXS} transactions")
    for p in buyers:
        print(f"   buyer    {p}")
    print(f"cost        {cost:.4f} SOL total, leaves {balance - cost:.4f}")
    print(f"art         {len(art)} bytes PNG")
    print(json.dumps(pump.metadata(name=args.name, symbol=args.symbol, description=description,
                                   image_uri="<pinned on --send>", website=website, twitter=TWITTER,
                                   treasury=buyers), indent=2))
    if balance - cost < BALANCE_MARGIN_SOL:
        print(f"balance {balance:.4f} - {cost:.4f} would leave less than {BALANCE_MARGIN_SOL} SOL",
              file=sys.stderr)
        return 1
    if not args.send:
        print("\ndry run: nothing pinned, funded or sent. Re-run with --send to launch.")
        return 0

    image_uri = pump.pin_image(jwt, args.symbol, art)
    uri = pump.pin_metadata(jwt, name=args.name, symbol=args.symbol, description=description,
                            image_uri=image_uri, website=website, twitter=TWITTER, treasury=buyers)
    print(f"image       {image_uri}\nmetadata    {uri}")
    keys = store.keypairs(buyers)
    funded = treasury_mod.fund_all(rpc, dev, keys, args.buy_sol)
    print(f"funded      {len(funded)} wallet(s) to {args.buy_sol + treasury_mod.BUY_OVERHEAD_SOL:.4f} SOL each")
    out = treasury_mod.bundle_launch(rpc, dev, mint, keys, name=args.name, symbol=args.symbol, uri=uri,
                                     dev_buy_sol=args.dev_buy_sol, buy_sol=args.buy_sol,
                                     tip_sol=args.tip_sol, slippage_pct=SLIPPAGE_PCT)
    print(f"\nbundle      {out.bundle_id}  {out.status}" + (f"  slot {out.slot}" if out.slot else ""))
    if out.landed:
        print(f"LIVE: {args.name} (${args.symbol})")
        print(f"mint        {out.mint}\npump.fun    https://pump.fun/coin/{out.mint}\nsite        {website}")
        for sig in out.signatures:
            print(f"tx          https://solscan.io/tx/{sig}")
    else:
        print(f"the bundle did not land ({out.status}). Nothing was created: a bundle is all-or-nothing.\n"
              f"Check https://pump.fun/coin/{out.mint} before retrying, and sweep the funded wallets back "
              f"with devbot.wallets.sweep if you are not relaunching now.")
    print(f"balance     {rpc.balance_sol(str(dev.pubkey())):.4f} SOL")
    return 0 if out.landed else 2


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("wallets", help="create treasury keys and write the manifest")
    w.add_argument("--new", type=int, default=0, help="how many new wallets to create")
    w.add_argument("--rule", required=True, help="what these wallets will and will not do, in your words")
    w.add_argument("--manifest-file", type=Path, default=MANIFEST_FILE, dest="manifest_file")
    w.set_defaults(fn=cmd_wallets)

    s = sub.add_parser("show", help="local keys, their balances, and what the site publishes")
    s.add_argument("--manifest-url", default=MANIFEST_URL, dest="manifest_url")
    s.set_defaults(fn=cmd_show)

    l = sub.add_parser("launch", help="create + disclosed treasury buys in one bundle")
    l.add_argument("--name", required=True)
    l.add_argument("--symbol", required=True)
    l.add_argument("--art", required=True, help="image path or URL")
    l.add_argument("--description", required=True)
    l.add_argument("--buy-sol", type=float, required=True, dest="buy_sol", help="SOL each treasury wallet buys")
    l.add_argument("--dev-buy-sol", type=float, default=0.0, dest="dev_buy_sol")
    l.add_argument("--tip-sol", type=float, default=DEFAULT_TIP_SOL, dest="tip_sol")
    l.add_argument("--wallets", default="", help="comma-separated pubkeys; default is every local key")
    l.add_argument("--manifest-url", default=MANIFEST_URL, dest="manifest_url")
    l.add_argument("--send", action="store_true", help="actually pin, fund and send the bundle")
    l.set_defaults(fn=cmd_launch)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
