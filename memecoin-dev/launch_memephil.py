"""One-off: launch memephil's own coin on pump.fun, with $LMPY's picture.

The Telegram bot designs a coin per narrative; this one is the brand itself, so the name, ticker,
art and text are fixed here rather than written by a model. Everything that spends or signs still
goes through devbot.pump unchanged: metadata to Pinata, PumpPortal builds the create, our key signs
it and our RPC sends it.

    .venv/bin/python launch_memephil.py           # dry run: prints the metadata, spends nothing
    .venv/bin/python launch_memephil.py --send    # pins, creates the coin, prints the mint
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from devbot import http, image as image_mod, pump
from solders.keypair import Keypair

NAME = "memephil"
SYMBOL = "MEMEPHIL"
# $LMPY's art (89UpZ4N9ndpBasR2BciycjBtzBEMV7UJSXbnDuKcVoh2), already pinned on our own Pinata.
LAMPY_ART = "https://gateway.pinata.cloud/ipfs/bafkreibu7ezyjzi6rum57kt6xlmnyco6unnxuyxxyeq7pwdor7xwqukmd4"
DESCRIPTION = ("memephil is an ai memecoin dev. it reads whatever the internet is laughing at and retells it "
               "from the side that's here to help, and sometimes launches it. this one is the dev itself. "
               "agi is coming, and it's going to be good.")
DISCLOSURE = "Dev buy: {dev_buy:g} SOL. Dev wallet: {wallet}. Dev sells are posted publicly."
WEBSITE_BASE = "https://coins.memephil.workers.dev"
TWITTER = "https://x.com/0xmemephil"   # the account itself: there is no launch post without X keys here

KEYPAIR_PATH = Path("~/.config/solana/solana-rust-frankfurt-trading.json").expanduser()
ENV_PATH = Path("~/.devbot/.env").expanduser()
HELIUS_ENV = Path("~/.config/solana-rpc/helius.env").expanduser()

DEV_BUY_SOL = 0.0
PRIORITY_FEE_SOL = 0.00005
SLIPPAGE_PCT = 10
MAYHEM = False          # pump.fun's agent drives snipers away at no fee gain (research, 2026-10-14)
BALANCE_MARGIN_SOL = 0.005   # left over after the launch, for a later fee claim's own fee


def _env(path: Path, key: str) -> str:
    for line in path.read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def main(send: bool) -> int:
    jwt = os.environ.get("PINATA_JWT", "").strip() or _env(ENV_PATH, "PINATA_JWT")
    if not jwt:
        print(f"no PINATA_JWT in the environment or {ENV_PATH}", file=sys.stderr)
        return 1
    rpc_url = _env(HELIUS_ENV, "HTTP_URL") or "https://api.mainnet-beta.solana.com"
    rpc = pump.Rpc([rpc_url])
    dev = pump.load_keypair(KEYPAIR_PATH)
    mint = Keypair()

    art = image_mod.fit(http.request("GET", image_mod.source_url(LAMPY_ART),
                                     headers={"User-Agent": "Mozilla/5.0"}, timeout=60))
    cost = pump.LAUNCH_COST_SOL + DEV_BUY_SOL + PRIORITY_FEE_SOL
    balance = rpc.balance_sol(str(dev.pubkey()))
    website = f"{WEBSITE_BASE}/{mint.pubkey()}"
    description = DESCRIPTION + "\n\n" + DISCLOSURE.format(dev_buy=DEV_BUY_SOL, wallet=dev.pubkey())
    print(f"dev wallet  {dev.pubkey()}  {balance:.4f} SOL")
    print(f"mint        {mint.pubkey()}")
    print(f"cost        {cost:.4f} SOL (launch {pump.LAUNCH_COST_SOL}, dev buy {DEV_BUY_SOL:g}, "
          f"fee {PRIORITY_FEE_SOL})")
    print(f"art         {len(art)} bytes PNG from $LMPY")
    print(json.dumps(pump.metadata(name=NAME, symbol=SYMBOL, description=description,
                                   image_uri="<pinned on --send>", website=website, twitter=TWITTER), indent=2))
    if balance - cost < BALANCE_MARGIN_SOL:
        print(f"balance {balance:.4f} - {cost:.4f} would leave less than {BALANCE_MARGIN_SOL} SOL", file=sys.stderr)
        return 1
    if not send:
        print("\ndry run: nothing pinned, nothing sent. Re-run with --send to launch.")
        return 0

    image_uri = pump.pin_image(jwt, SYMBOL, art)
    uri = pump.pin_metadata(jwt, name=NAME, symbol=SYMBOL, description=description,
                            image_uri=image_uri, website=website, twitter=TWITTER)
    print(f"image       {image_uri}")
    print(f"metadata    {uri}")
    out = pump.create(rpc, dev, mint, name=NAME, symbol=SYMBOL, uri=uri, dev_buy_sol=DEV_BUY_SOL,
                      slippage_pct=SLIPPAGE_PCT, priority_fee_sol=PRIORITY_FEE_SOL, mayhem=MAYHEM)
    print(f"\n{'LIVE' if out.confirmed else 'SENT (not confirmed yet)'}: {NAME} (${SYMBOL})")
    print(f"mint        {out.mint}")
    print(f"pump.fun    https://pump.fun/coin/{out.mint}")
    print(f"tx          https://solscan.io/tx/{out.signature}")
    print(f"site        {website}")
    print(f"balance     {rpc.balance_sol(str(dev.pubkey())):.4f} SOL")
    return 0 if out.confirmed else 2


if __name__ == "__main__":
    raise SystemExit(main("--send" in sys.argv[1:]))
