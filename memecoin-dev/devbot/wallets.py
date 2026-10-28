"""A fresh creator wallet for every launch (ROTATE_WALLETS=1).

Sniper bots read the creator wallet's history: within creator, a wallet's first launch of the day
drew broad snipers into the launch block +2.4/+2.9pp more often and netted the dev +0.024/+0.033 SOL
more (research/RESULT-bot-levers-20261014.md, both tape days). So each coin gets a new creator key.

The main dev wallet funds a rotated wallet just before its launch and pays the fee of every sweep, so a
rotated wallet can be emptied to exactly 0 SOL. Keys are kept for good, one 0600 file each: pump.fun
accrues creator fees per creator, so claiming a coin's fees (or selling its dev tokens) needs that key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from . import pump

TOPUP_SOL = 0.002   # enough for a sell or a claim (fee + priority) above the 0.00089 rent-exempt minimum


class Wallets:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(mode=0o700, parents=True, exist_ok=True)

    def new(self) -> Keypair:
        kp = Keypair()
        # O_EXCL + 0600 at creation: the secret is never readable by anyone else, not even briefly
        fd = os.open(self.path / f"{kp.pubkey()}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(list(bytes(kp)), f)
        return kp

    def pubkeys(self) -> list[str]:
        return sorted(p.stem for p in self.path.glob("*.json"))

    def get(self, pubkey: str) -> Keypair:
        return pump.load_keypair(self.path / f"{pubkey}.json")

    def age_s(self, pubkey: str, now: float) -> float:
        return now - (self.path / f"{pubkey}.json").stat().st_mtime


def _send(rpc: pump.Rpc, payer: Keypair, ixs: list, signers: list[Keypair]) -> str:
    msg = MessageV0.try_compile(payer.pubkey(), ixs, [], rpc.blockhash())
    sig = rpc.send(VersionedTransaction(msg, signers))
    if not rpc.confirm(sig, timeout_s=60):
        raise RuntimeError(f"transfer {sig} not confirmed")
    return sig


def fund(rpc: pump.Rpc, main: Keypair, to: Pubkey, sol: float) -> str:
    lamports = int(round(sol * pump.LAMPORTS))
    return _send(rpc, main, [transfer(TransferParams(from_pubkey=main.pubkey(), to_pubkey=to, lamports=lamports))],
                 [main])


def top_up(rpc: pump.Rpc, main: Keypair, w: Keypair, sol: float = TOPUP_SOL) -> str | None:
    """Bring w up to `sol` so it can pay its own sell or claim; None if it already has that."""
    have = rpc.balance_sol(str(w.pubkey()))
    return fund(rpc, main, w.pubkey(), sol - have) if have < sol else None


def sweep(rpc: pump.Rpc, main: Keypair, w: Keypair) -> tuple[float, str] | None:
    """Move every lamport of w back to main; main pays the fee, so w ends at exactly 0."""
    lamports = rpc.call("getBalance", [str(w.pubkey()), {"commitment": "confirmed"}])["value"]
    if lamports <= 0:
        return None
    ix = transfer(TransferParams(from_pubkey=w.pubkey(), to_pubkey=main.pubkey(), lamports=lamports))
    return lamports / pump.LAMPORTS, _send(rpc, main, [ix], [main, w])


def lamports_of(rpc: pump.Rpc, addresses: list[str]) -> dict[str, int]:
    """Balances of many accounts, 100 per getMultipleAccounts call; a missing account is 0."""
    out: dict[str, int] = {}
    for i in range(0, len(addresses), 100):
        chunk = addresses[i:i + 100]
        values = rpc.call("getMultipleAccounts", [chunk, {"commitment": "confirmed", "encoding": "base64",
                                                          "dataSlice": {"offset": 0, "length": 0}}])["value"]
        out.update({a: (v or {}).get("lamports", 0) for a, v in zip(chunk, values)})
    return out
