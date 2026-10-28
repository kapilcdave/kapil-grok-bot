"""memephil's launch-block treasury: the same-block buy with the wallet list published, not hidden.

Bundling is normally used to make insider supply look like a crowd: 8-20 wallets buy in the creation
block, and the split exists so that no single holder looks big to anyone reading the holders tab. The
split is the deception, not the block. This module keeps the block and drops the disguise:

- every wallet is listed in `treasury.json`, published at `<WEBSITE_BASE>/treasury.json`, BEFORE a launch;
- the same list is pinned in the coin's own metadata (`treasury`) and named in its description;
- `check_published` refuses the launch when a wallet that is about to buy is not in the published file,
  so the disclosure cannot lag the buy by even one launch;
- nothing here sells. Selling stays the dev wallet's own /sell, which the disclosure says is public.

What this does NOT do, deliberately: no wash volume, no holder padding, no bump cycles. Our own tape
says they would not help anyway (research/RESULT-bot-levers-20261014.md: bots are already 87-88% of
non-creator volume) and they exist only to misstate demand.

Keys live one 0600 file each in $DEVBOT_STATE/treasury, like wallets.py, and are kept: pump.fun
tracks holders per wallet, so selling or moving a treasury position later needs that key.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from solders.keypair import Keypair

from . import http, jito, pump
from . import wallets as wallets_mod

MANIFEST_NAME = "treasury.json"
# One slot of a bundle is the create, so the rest is what can buy in the launch block.
MAX_WALLETS = jito.MAX_TXS - 1
# A buy leaves an associated token account (rent) and pays pump.fun's 1% fee; this covers both
# plus the signature, so a funded wallet can actually spend its whole intended buy.
BUY_OVERHEAD_SOL = 0.0035   # token account plus the fee-share program's trailing transfer; 0.0025 failed a 0.008 buy


class Treasury(wallets_mod.Wallets):
    """The treasury keys. Same 0600-file-per-key store as the rotated creator wallets."""

    def keypairs(self, pubkeys: list[str]) -> list[Keypair]:
        return [self.get(p) for p in pubkeys]

    def new_keys(self, n: int) -> list[str]:
        return [str(self.new().pubkey()) for _ in range(n)]


def manifest(wallets: list[str], *, dev_wallet: str, rule: str, updated: float | None = None) -> dict:
    """The published file. `rule` is the operator's own commitment about these wallets, in their
    words: it is what readers are entitled to hold us to, so it is never defaulted here."""
    if not rule.strip():
        raise ValueError("a treasury manifest needs a rule: what these wallets will and will not do")
    return {
        "dev_wallet": dev_wallet,
        "treasury": sorted(wallets),
        "rule": rule.strip(),
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(updated if updated is not None else time.time())),
        "note": "These are memephil's own wallets. They buy in the launch block of memephil's coins. "
                "Treat every one of them as the dev, not as another buyer.",
    }


def write_manifest(path: Path, man: dict) -> Path:
    """Write the manifest where the site serves it from (site/public/treasury.json)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(man, indent=2) + "\n")
    return path


def published(url: str) -> dict:
    return json.loads(http.request("GET", url, timeout=30))


def check_published(url: str, wallets: list[str]) -> dict:
    """Raise unless the live manifest already lists every wallet about to buy."""
    try:
        man = published(url)
    except Exception as e:
        raise RuntimeError(f"cannot read the published treasury at {url}: {str(e)[:120]}. Deploy the site "
                           f"first; a launch-block buy from an unpublished wallet is the thing this avoids") from e
    listed = set(man.get("treasury") or [])
    missing = [w for w in wallets if w not in listed]
    if missing:
        raise RuntimeError(f"{url} does not list {', '.join(missing)}: publish the manifest "
                           f"(write_manifest + wrangler deploy) before launching with these wallets")
    return man


def disclosure(wallets: list[str], manifest_url: str) -> str:
    """The line that goes in the coin's description, in front of the dev-buy disclosure."""
    if not wallets:
        return ""
    return (f"Launch-block buys: {len(wallets)} wallet{'s' if len(wallets) > 1 else ''} owned by memephil, "
            f"published at {manifest_url} before this coin existed: {', '.join(sorted(wallets))}. "
            f"They are the dev, not other buyers.")


def cost_sol(*, n_wallets: int, buy_sol: float, dev_buy_sol: float, tip_sol: float) -> float:
    """Everything the dev wallet pays for one treasury launch, including funding the wallets.
    The buys themselves come back as tokens, not SOL, so none of this is recoverable on the day."""
    return pump.LAUNCH_COST_SOL + dev_buy_sol + tip_sol + n_wallets * (buy_sol + BUY_OVERHEAD_SOL)


def fund_all(rpc: pump.Rpc, dev: Keypair, wallets: list[Keypair], buy_sol: float) -> list[str]:
    """Top every treasury wallet up to its buy plus overhead. Confirmed one at a time, before the
    bundle: a wallet that is short would fail its buy and take the whole bundle down with it."""
    need = buy_sol + BUY_OVERHEAD_SOL
    sigs = []
    for w in wallets:
        sig = wallets_mod.top_up(rpc, dev, w, need)
        if sig:
            sigs.append(sig)
    return sigs


@dataclass(frozen=True)
class Bundled:
    mint: str
    bundle_id: str
    status: str                 # jito's terminal status: Landed, Failed, Invalid, or Pending on timeout
    slot: int | None
    signatures: list[str]

    @property
    def landed(self) -> bool:
        return self.status == "Landed"


def bundle_launch(rpc: pump.Rpc, dev: Keypair, mint: Keypair, buyers: list[Keypair], *, name: str,
                  symbol: str, uri: str, dev_buy_sol: float, buy_sol: float, tip_sol: float,
                  slippage_pct: int, timeout_s: float = 60) -> Bundled:
    """Create the coin and buy from every treasury wallet in one block, or do neither.

    Sent once and never resent: a bundle is atomic, so a resend of a landed bundle could only
    create a second set of buys, and the create would fail anyway once the mint exists.
    """
    if not 1 <= len(buyers) <= MAX_WALLETS:
        raise ValueError(f"1-{MAX_WALLETS} treasury wallets fit in a bundle with the create, got {len(buyers)}")
    if tip_sol < jito.MIN_TIP_SOL:
        raise ValueError(f"tip {tip_sol} is below Jito's minimum {jito.MIN_TIP_SOL}")
    create = {
        "publicKey": str(dev.pubkey()),
        "action": "create",
        "tokenMetadata": {"name": name, "symbol": symbol, "uri": uri},
        "mint": str(mint.pubkey()),
        "denominatedInSol": "true",
        "amount": dev_buy_sol,
        "slippage": slippage_pct,
        "priorityFee": tip_sol,     # the whole bundle's Jito tip
        "pool": "pump",
    }
    buys = [{
        "publicKey": str(w.pubkey()),
        "action": "buy",
        "mint": str(mint.pubkey()),
        "denominatedInSol": "true",
        "amount": buy_sol,
        "slippage": slippage_pct,
        "priorityFee": 0.0,         # only the first transaction's fee becomes the tip
        "pool": "pump",
    } for w in buyers]
    txs = pump.bundle_txs([create, *buys], [[mint, dev], *([w] for w in buyers)])
    bundle_id = jito.send(txs)
    status, slot, sigs = jito.landed(bundle_id, timeout_s=timeout_s)
    return Bundled(str(mint.pubkey()), bundle_id, status, slot, sigs)
