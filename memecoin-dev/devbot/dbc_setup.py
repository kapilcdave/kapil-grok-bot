"""One-time: create the Meteora DBC config the `meteora` launchpad launches into.

    python -m devbot.dbc_setup            # prints what it would do (dry run)
    python -m devbot.dbc_setup --send     # creates the config on chain with DEV_KEYPAIR_PATH

A config is reusable: every later launch is a pool in it. Its curve and fee terms are fixed at creation,
so changing them means making a new config. Reads DEV_KEYPAIR_PATH, SOLANA_RPC_URL, DBC_INITIAL_MCAP,
DBC_MIGRATION_MCAP from the environment (the same ~/.devbot/.env the bot uses).
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
    pad = launchpads.MeteoraDbc(config="", pinata_jwt="", rpc_url=rpc_url,
                                initial_market_cap=float(os.environ.get("DBC_INITIAL_MCAP", "30") or 30),
                                migration_market_cap=float(os.environ.get("DBC_MIGRATION_MCAP", "400") or 400))
    print(f"wallet {dev.pubkey()}, curve {pad.curve()}")
    if not send:
        print("dry run: add --send to create the config on chain")
        return 0
    address = pad.make_config(pump.Rpc([rpc_url]), dev)
    print(f"created. Put this in ~/.devbot/.env:\nDBC_CONFIG={address}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
