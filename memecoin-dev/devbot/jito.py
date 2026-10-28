"""Jito block-engine bundles: a few transactions, one slot, all or nothing.

A launch's create and its treasury buys have to sit in the same block to be one published event
rather than the dev quietly buying its own coin a few seconds later, and a bundle is the only way to
ask for that. Ordering inside a bundle is the order given, and if any transaction fails the whole
bundle is dropped, so a treasury buy can never land against a create that didn't.

A bundle is only considered if one of its transactions tips a Jito tip account. PumpPortal's bundle
builder puts that tip in the FIRST transaction (it spends that transaction's `priorityFee` on it),
so no tip transfer is compiled here; `MIN_TIP_SOL` is the documented floor, not a competitive bid.
"""
from __future__ import annotations

import base64
import json
import os
import time

from solders.transaction import VersionedTransaction

from . import http

BLOCK_ENGINE = os.environ.get("JITO_BLOCK_ENGINE", "https://mainnet.block-engine.jito.wtf/api/v1/bundles")
MAX_TXS = 5             # the block engine rejects a longer bundle
MIN_TIP_SOL = 0.000001  # 1000 lamports; bundles are an auction, so the floor only clears a quiet slot
TERMINAL = ("Landed", "Failed", "Invalid")


def _rpc(url: str, method: str, params: list) -> object:
    body = json.loads(http.post_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    if body.get("error"):
        raise RuntimeError(f"{method}: {body['error']}")
    return body.get("result")


def tip_accounts(*, url: str = BLOCK_ENGINE) -> list[str]:
    """Jito's own tip accounts. Only needed to check a tip we did not build ourselves."""
    return list(_rpc(url, "getTipAccounts", []) or [])


def send(txs: list[VersionedTransaction], *, url: str = BLOCK_ENGINE) -> str:
    """Submit signed transactions as one bundle; returns its bundle id."""
    if not 1 <= len(txs) <= MAX_TXS:
        raise ValueError(f"a bundle holds 1-{MAX_TXS} transactions, got {len(txs)}")
    encoded = [base64.b64encode(bytes(tx)).decode() for tx in txs]
    return str(_rpc(url, "sendBundle", [encoded, {"encoding": "base64"}]))


def _value(result: object) -> list:
    """Both status calls answer {"context": ..., "value": [...]}."""
    return list((result or {}).get("value") or []) if isinstance(result, dict) else []


def inflight(bundle_id: str, *, url: str = BLOCK_ENGINE) -> str:
    """'Landed', 'Pending', 'Failed' or 'Invalid'. Only the last five minutes are known;
    'Invalid' therefore means "not this bundle, or too long ago", not "rejected"."""
    rows = _value(_rpc(url, "getInflightBundleStatuses", [[bundle_id]]))
    return (rows[0] or {}).get("status", "Invalid") if rows else "Invalid"


def signatures(bundle_id: str, *, url: str = BLOCK_ENGINE) -> tuple[int | None, list[str]]:
    """(slot, transaction signatures) of a landed bundle; (None, []) while it is not known."""
    rows = _value(_rpc(url, "getBundleStatuses", [[bundle_id]]))
    row = (rows[0] or {}) if rows else {}
    return row.get("slot"), list(row.get("transactions") or [])


def landed(bundle_id: str, *, timeout_s: float = 60, url: str = BLOCK_ENGINE,
           sleep_s: float = 2.0) -> tuple[str, int | None, list[str]]:
    """Poll until the bundle reaches a terminal state or the timeout; (status, slot, signatures).

    A timeout is reported as the last status seen, never as a failure: a bundle that is still
    pending may yet land, and the caller must check the chain before deciding it did not.
    """
    start = time.monotonic()
    deadline = start + timeout_s
    status = "Pending"
    while time.monotonic() < deadline:
        # getBundleStatuses is the truth: a landed bundle appears there while getInflightBundleStatuses
        # can keep answering 'Invalid' for it (observed 2026-10-15), so 'Invalid' is never final here.
        try:
            slot, sigs = signatures(bundle_id, url=url)
            if sigs:
                return "Landed", slot, sigs
            time.sleep(1.1)     # the block engine allows one request per second
            status = inflight(bundle_id, url=url)
        except (http.HttpError, RuntimeError):
            status = "Pending"  # rate limited: wait and ask again
        if status in ("Landed", "Failed"):
            break
        time.sleep(sleep_s)
    if status == "Landed":
        return status, *signatures(bundle_id, url=url)
    return status, None, []
