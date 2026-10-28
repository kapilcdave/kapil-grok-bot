"""pump.fun via PumpPortal's local-transaction API: metadata to IPFS, create, claim creator fees.

Local transactions mean PumpPortal only builds the transaction; it is signed here
with our own key and sent through our own RPC, so no third party holds the key.
pump.fun/api/ipfs is retired, so metadata goes to Pinata (free tier) instead.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from . import http
from . import image as image_mod

PORTAL = "https://pumpportal.fun/api/trade-local"
PINATA = "https://uploads.pinata.cloud/v3/files"
LAMPORTS = 1_000_000_000
WSOL_MINT = "So11111111111111111111111111111111111111112"
# Non-recoverable cost per launch measured on-chain (rent-dominated), excluding the dev buy.
LAUNCH_COST_SOL = 0.0098
# Mayhem mode (pump.fun's agent trades the coin for 24 h): PumpPortal's `isMayhemMode` sets create_v2's
# last flag byte and adds InitializeMayhemState. Simulated 2026-10-14: the dev pays 0.00826 SOL of
# rent against 0.00556 for a plain create, so 0.0027 more.
MAYHEM_EXTRA_SOL = 0.0027
MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"  # the agent's trading wallet, seen on-chain


def load_keypair(path: Path) -> Keypair:
    """A solana-keygen JSON array file, or a file holding one base58 secret."""
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError(f"{path} is mode {oct(mode)}; chmod 600 it before the bot will use it")
    text = path.read_text().strip()
    if text.startswith("["):
        return Keypair.from_bytes(bytes(json.loads(text)))
    return Keypair.from_base58_string(text)


def _pin(jwt: str, filename: str, data: bytes, ctype: str) -> str:
    body, content_type = http.multipart({"network": "public"}, {"file": (filename, data, ctype)})
    raw = http.request("POST", PINATA, body=body,
                       headers={"Authorization": f"Bearer {jwt}", "Content-Type": content_type}, timeout=120)
    return "https://ipfs.io/ipfs/" + json.loads(raw)["data"]["cid"]


def metadata(*, name: str, symbol: str, description: str, image_uri: str, website: str = "",
             twitter: str = "", treasury: list[str] | None = None) -> dict:
    """`twitter` is only ever our own launch post, never someone else's account or post.

    `treasury` names the wallets that buy in this coin's launch block (treasury.py). It rides in the
    coin's own metadata so that disclosure is pinned with the coin, not only on our site."""
    meta = {"name": name, "symbol": symbol, "description": description, "image": image_uri,
            "showName": True, "twitter": twitter, "telegram": "", "website": website}
    if treasury:
        meta["treasury"] = list(treasury)
    return meta


def pin_image(jwt: str, symbol: str, image: bytes) -> str:
    """Uploaded when the coin is proposed, so a Launch click only waits for the small metadata file."""
    ext, mime = image_mod.kind(image)
    return _pin(jwt, f"{symbol.lower()}.{ext}", image, mime)


def pin_metadata(jwt: str, *, name: str, symbol: str, description: str, image_uri: str,
                 website: str = "", twitter: str = "", treasury: list[str] | None = None) -> str:
    meta = metadata(name=name, symbol=symbol, description=description, image_uri=image_uri,
                    website=website, twitter=twitter, treasury=treasury)
    return _pin(jwt, "metadata.json", json.dumps(meta).encode(), "application/json")


def cid_of(url: str) -> str:
    return url.rstrip("/").split("/ipfs/")[-1].split("/")[0]


PINATA_API = "https://api.pinata.cloud/v3/files/public"
# For our own reads. ipfs.io now answers direct requests with 429 ("service worker gateway only");
# the on-chain URIs keep the ipfs.io form pump.fun itself uses, since pump.fun resolves the CID.
GATEWAY = "https://gateway.pinata.cloud/ipfs/"


def unpin(jwt: str, cid: str) -> int:
    """Delete every Pinata file with this CID from our account; returns how many were deleted.
    Pinata deletes by file id, so the id is looked up by CID first."""
    auth = {"Authorization": f"Bearer {jwt}"}
    found = json.loads(http.request("GET", f"{PINATA_API}?cid={cid}", headers=auth, timeout=30))
    files = (found.get("data") or {}).get("files") or []
    for f in files:
        http.request("DELETE", f"{PINATA_API}/{f['id']}", headers=auth, timeout=30)
    return len(files)


# A coin is dead when nobody trades it and nobody holds a meaningful position: deleting its
# image then breaks nothing anyone owns. A coin that graduated is never treated as dead.
DEAD_IDLE_S = 3600
DEAD_MAX_CURVE_SOL = 0.05


def coin_info(mint: str) -> dict | None:
    try:
        return http.get_json(f"https://frontend-api-v3.pump.fun/coins-v2/{mint}",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    except (http.HttpError, OSError, ValueError):
        return None


def is_dead(info: dict, now: float | None = None) -> bool:
    now = now or time.time()
    if info.get("complete"):
        return False
    last = (info.get("last_trade_timestamp") or info.get("created_timestamp") or now * 1000) / 1000
    curve_sol = int(info.get("real_sol_reserves") or 0) / LAMPORTS
    return now - last >= DEAD_IDLE_S and curve_sol < DEAD_MAX_CURVE_SOL


class Rpc:
    """JSON-RPC over an ordered list of endpoints: free public first, keyed ones as fallback.
    Only quota/rate errors, and a node that can't serve history, fall through to the next
    endpoint; anything else is a real error."""

    QUOTA_STATUSES = {413, 429}
    # -32019: "Failed to query long-term storage" (public node without history; audience.py needs it)
    QUOTA_CODES = {-32003, -32005, -32019, 413, 429}

    def __init__(self, urls: list[str]) -> None:
        if not urls:
            raise ValueError("no RPC endpoints")
        self.urls = urls

    def call(self, method: str, params: list) -> object:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        last = ""
        for i, url in enumerate(self.urls):
            try:
                body = json.loads(http.post_json(url, payload))
            except http.HttpError as e:
                if e.status not in self.QUOTA_STATUSES:
                    raise
                last = f"endpoint {i}: HTTP {e.status}"
                continue
            err = body.get("error")
            if err and err.get("code") in self.QUOTA_CODES:
                last = f"endpoint {i}: {err.get('message', '')[:80]}"
                continue
            if err:
                raise RuntimeError(f"{method}: {err}")
            return body["result"]
        raise RuntimeError(f"{method}: every RPC endpoint is rate-limited ({last})")

    def balance_sol(self, pubkey: str) -> float:
        return self.call("getBalance", [pubkey, {"commitment": "confirmed"}])["value"] / LAMPORTS

    def send(self, tx: VersionedTransaction) -> str:
        return self.call("sendTransaction", [
            base64.b64encode(bytes(tx)).decode(),
            {"encoding": "base64", "preflightCommitment": "confirmed", "maxRetries": 5},
        ])

    def confirm(self, sig: str, timeout_s: float = 75) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = self.call("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}])["value"][0]
            if status:
                if status.get("err"):
                    raise RuntimeError(f"{sig} failed: {status['err']}")
                if status.get("confirmationStatus") in ("confirmed", "finalized"):
                    return True
            time.sleep(2)
        return False

    def blockhash(self) -> Hash:
        # finalized: a load-balanced public RPC can reject a fresher hash its other nodes haven't seen
        value = self.call("getLatestBlockhash", [{"commitment": "finalized"}])["value"]
        return Hash.from_string(value["blockhash"])

    def wsol_accounts(self, owner: str) -> list[tuple[str, str, int]]:
        """(account, token program, lamports) for each wSOL account the owner holds."""
        rows = self.call("getTokenAccountsByOwner", [owner, {"mint": WSOL_MINT}, {"encoding": "jsonParsed"}])
        return [(r["pubkey"], r["account"]["owner"], int(r["account"]["lamports"])) for r in rows["value"]]


def close_account_ix(account: str, owner: Pubkey, token_program: str) -> Instruction:
    """SPL Token CloseAccount (tag 9). On a wSOL account it pays all lamports, i.e. the
    wrapped balance plus the rent, to `owner` as native SOL."""
    return Instruction(
        Pubkey.from_string(token_program),
        bytes([9]),
        [AccountMeta(Pubkey.from_string(account), is_signer=False, is_writable=True),
         AccountMeta(owner, is_signer=False, is_writable=True),
         AccountMeta(owner, is_signer=True, is_writable=False)],
    )


def unwrap_wsol(rpc: Rpc, dev: Keypair) -> tuple[float, str] | None:
    """Close every wSOL account into native SOL in one transaction. None if there are none."""
    accounts = rpc.wsol_accounts(str(dev.pubkey()))
    if not accounts:
        return None
    ixs = [close_account_ix(acct, dev.pubkey(), program) for acct, program, _ in accounts]
    msg = MessageV0.try_compile(dev.pubkey(), ixs, [], rpc.blockhash())
    sig = rpc.send(VersionedTransaction(msg, [dev]))
    rpc.confirm(sig)
    return sum(lamports for *_, lamports in accounts) / LAMPORTS, sig


def _build(payload: dict, signers: list[Keypair]) -> VersionedTransaction:
    raw = http.post_json(PORTAL, payload)
    unsigned = VersionedTransaction.from_bytes(raw)
    return VersionedTransaction(unsigned.message, signers)


B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(text: str) -> bytes:
    """Solana's base58, without a dependency for the one place we read it (PumpPortal's bundle form)."""
    n = 0
    for ch in text:
        i = B58_ALPHABET.find(ch)
        if i < 0:
            raise ValueError(f"not base58: {ch!r}")
        n = n * 58 + i
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(text) - len(text.lstrip("1"))) + body


def _decode_tx(blob: str) -> VersionedTransaction:
    """A transaction from the bundle form. It is documented as base58; base64 is accepted too, so a
    change of encoding there shows up as a different error than silent garbage."""
    for decode in (b58decode, base64.b64decode):
        try:
            return VersionedTransaction.from_bytes(decode(blob))
        except Exception:
            continue
    raise RuntimeError(f"PumpPortal returned a transaction we cannot decode: {blob[:60]}...")


def bundle_txs(payloads: list[dict], signers: list[list[Keypair]]) -> list[VersionedTransaction]:
    """PumpPortal's bundle form: ONE post with a list of actions returns one unsigned transaction per
    action, each signed here by its own wallet and sent to Jito as a bundle (jito.send).

    This is the only way to buy a coin in its own creation block: a standalone buy is built against
    a curve that does not exist yet. The first action is the create, and PumpPortal spends its
    `priorityFee` on the Jito tip for the whole bundle.
    """
    if len(payloads) != len(signers):
        raise ValueError(f"{len(payloads)} actions but {len(signers)} signer lists")
    body = json.loads(http.post_json(PORTAL, payloads))
    if not isinstance(body, list):
        raise RuntimeError(f"PumpPortal did not return a bundle: {str(body)[:200]}")
    if len(body) != len(payloads):
        raise RuntimeError(f"asked for {len(payloads)} transactions, got {len(body)}")
    out = []
    for blob, keys in zip(body, signers):
        tx = _decode_tx(blob)
        out.append(VersionedTransaction(tx.message, keys))
    return out


@dataclass(frozen=True)
class Launched:
    mint: str
    signature: str
    confirmed: bool


def _landed(rpc: Rpc, sigs: list[str]) -> str | None:
    statuses = rpc.call("getSignatureStatuses", [sigs, {"searchTransactionHistory": True}])["value"]
    for sig, st in zip(sigs, statuses):
        if st and not st.get("err") and st.get("confirmationStatus") in ("confirmed", "finalized"):
            return sig
    return None


def create_payload(dev: Keypair, mint: Keypair, *, name: str, symbol: str, uri: str, dev_buy_sol: float,
                   slippage_pct: int, priority_fee_sol: float, mayhem: bool = False) -> dict:
    """PumpPortal's trade-local body for a create (with the dev buy bundled in)."""
    payload = {
        "publicKey": str(dev.pubkey()),
        "action": "create",
        "tokenMetadata": {"name": name, "symbol": symbol, "uri": uri},
        "mint": str(mint.pubkey()),
        "denominatedInSol": "true",
        "amount": dev_buy_sol,
        "slippage": slippage_pct,
        "priorityFee": priority_fee_sol,
        "pool": "pump",
    }
    if mayhem:
        payload["isMayhemMode"] = "true"
    return payload


def simulate_create(rpc: Rpc, payload: dict, signers: list[Keypair]) -> dict:
    """Build the create exactly as `create` would and simulate it: nothing is sent or spent.
    {"ok", "err", "units", "logs", "build_s"}; the recent blockhash is replaced, so a slow build still simulates."""
    t0 = time.time()
    tx = _build(payload, signers)
    build_s = time.time() - t0
    out = rpc.call("simulateTransaction", [base64.b64encode(bytes(tx)).decode(),
                                           {"encoding": "base64", "sigVerify": False,
                                            "replaceRecentBlockhash": True, "commitment": "confirmed"}])["value"]
    return {"ok": out.get("err") is None, "err": out.get("err"), "units": out.get("unitsConsumed"),
            "logs": (out.get("logs") or [])[-3:], "build_s": build_s}


def create(rpc: Rpc, dev: Keypair, mint: Keypair, *, name: str, symbol: str, uri: str, dev_buy_sol: float,
           slippage_pct: int, priority_fee_sol: float, mayhem: bool = False, tries: int = 3,
           wait_s: float = 25) -> Launched:
    """`mint` is a fresh keypair made by the caller, so the address can go into the metadata
    (the coin's website) before upload; its secret only signs this one coin.

    A send can be dropped without a trace (LEAPS, 2026-10-13), so an unconfirmed create is rebuilt on
    a fresh blockhash and resent. Reusing the mint makes that safe: once one send lands, every other
    one fails because the mint exists, so at most one coin is ever created."""
    payload = create_payload(dev, mint, name=name, symbol=symbol, uri=uri, dev_buy_sol=dev_buy_sol,
                             slippage_pct=slippage_pct, priority_fee_sol=priority_fee_sol, mayhem=mayhem)
    sigs: list[str] = []
    for _ in range(tries):
        sigs.append(rpc.send(_build(payload, [mint, dev])))
        try:
            if rpc.confirm(sigs[-1], timeout_s=wait_s):
                return Launched(str(mint.pubkey()), sigs[-1], True)
        except RuntimeError:
            earlier = _landed(rpc, sigs[:-1]) if len(sigs) > 1 else None
            if earlier:  # this resend failed because an earlier send had landed
                return Launched(str(mint.pubkey()), earlier, True)
            raise
        earlier = _landed(rpc, sigs)
        if earlier:
            return Launched(str(mint.pubkey()), earlier, True)
    return Launched(str(mint.pubkey()), sigs[-1], False)


def sell(rpc: Rpc, dev: Keypair, mint: str, pct: int, *, slippage_pct: int, priority_fee_sol: float,
         wait_s: float = 60) -> Launched:
    """Sell `pct`% of the dev wallet's tokens of `mint` (curve or, once migrated, PumpSwap: pool auto).
    Sent once and never resent: a percentage sell that landed late would sell again from what's left."""
    tx = _build({"publicKey": str(dev.pubkey()), "action": "sell", "mint": mint, "denominatedInSol": "false",
                 "amount": f"{pct}%", "slippage": slippage_pct, "priorityFee": priority_fee_sol, "pool": "auto"},
                [dev])
    sig = rpc.send(tx)
    return Launched(mint, sig, rpc.confirm(sig, timeout_s=wait_s))


PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
VAULT_RENT_LAMPORTS = 890_880  # rent-exempt minimum of a 0-byte account; stays in the vault


def creator_vault(creator: Pubkey) -> Pubkey:
    """The PDA where pump.fun accrues a creator's fees across all of their coins."""
    return Pubkey.find_program_address([b"creator-vault", bytes(creator)], PUMP_PROGRAM)[0]


def bonding_curve(mint: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"bonding-curve", bytes(mint)], PUMP_PROGRAM)[0]


def curve_real_sol(rpc: Rpc, mint: str) -> float | None:
    """SOL actually paid into a coin's bonding curve so far (real_sol_reserves); None before it exists.
    Layout: 8-byte discriminator, then u64 virtual_token, virtual_sol, real_token, real_sol."""
    value = rpc.call("getAccountInfo", [str(bonding_curve(Pubkey.from_string(mint))),
                                        {"commitment": "processed", "encoding": "base64"}])["value"]
    if not value:
        return None
    data = base64.b64decode(value["data"][0])
    return int.from_bytes(data[32:40], "little") / LAMPORTS


def unclaimed_fees_sol(rpc: Rpc, creator: Pubkey) -> float:
    lamports = rpc.call("getBalance", [str(creator_vault(creator)), {"commitment": "confirmed"}])["value"]
    return max(0, lamports - VAULT_RENT_LAMPORTS) / LAMPORTS


def claim_creator_fees(rpc: Rpc, dev: Keypair, priority_fee_sol: float, tries: int = 3) -> str:
    """pump.fun pays out every coin's accrued creator fees in one claim. A sent tx can be dropped
    before it lands; each retry is rebuilt on a fresh blockhash, and a claim that never confirms
    raises instead of looking like a claim of zero (the fees stay in the vault either way)."""
    sigs = []
    for _ in range(tries):
        tx = _build({"publicKey": str(dev.pubkey()), "action": "collectCreatorFee",
                     "priorityFee": priority_fee_sol, "pool": "pump"}, [dev])
        sig = rpc.send(tx)
        sigs.append(sig)
        if rpc.confirm(sig, timeout_s=60):
            return sig
    raise RuntimeError(f"claim not confirmed after {tries} tries ({', '.join(s[:8] for s in sigs)}); "
                       "the fees are still in the creator vault")
