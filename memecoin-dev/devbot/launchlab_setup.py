"""One-time: create OUR OWN Raydium LaunchLab platform, so launches into it pay the platform fee and keep the
graduated LP position, on top of the creator fee.

    python -m devbot.launchlab_setup          # dry run: shows the terms
    python -m devbot.launchlab_setup --send   # creates the platform on chain with DEV_KEYPAIR_PATH

One platform per admin wallet (its id is derived from the wallet), so this can be run once. Rates are parts per
million: LAUNCHLAB_FEE_RATE (platform fee, default 5000 = 0.5%) and LAUNCHLAB_CREATOR_FEE_RATE (default 5000 =
0.5%); traders also pay Raydium's 0.25%. Reads DEV_KEYPAIR_PATH and SOLANA_RPC_URL from the environment.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import launchpads, pump


def main(argv: list[str]) -> int:
    send = "--send" in argv
    rpc_url = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com").split(",")[0].strip()
    dev = pump.load_keypair(Path(os.environ.get("DEV_KEYPAIR_PATH", "~/.devbot/dev-wallet.json")).expanduser())
    terms = {"name": os.environ.get("LAUNCHLAB_NAME", "devbot"),
             "web": os.environ.get("LAUNCHLAB_WEB", "https://example.com/"),
             "img": os.environ.get("LAUNCHLAB_IMG", "https://example.com/logo.png"),
             "fee_rate": int(os.environ.get("LAUNCHLAB_FEE_RATE", "5000") or 5000),
             "creator_fee_rate": int(os.environ.get("LAUNCHLAB_CREATOR_FEE_RATE", "5000") or 5000)}
    print(f"wallet {dev.pubkey()}, terms {terms}")
    if not send:
        print("dry run: add --send to create the platform on chain")
        return 0
    pad = launchpads.LaunchLab(pinata_jwt="", rpc_url=rpc_url)
    platform = pad.make_platform(pump.Rpc([rpc_url]), dev, **terms)
    print(f"created. Put this in ~/.devbot/.env:\nLAUNCHLAB_PLATFORM={platform}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
