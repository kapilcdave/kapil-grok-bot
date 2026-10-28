"""Launchpads other than pump.fun, behind one small interface.

Why: pump.fun has no pool of its own to earn on until graduation. Launchpads built on Meteora's
Dynamic Bonding Curve (Jupiter Studio, Bags, Believe) put the coin in a real pool from block one, so
the creator can hold LP-fee share, a creator trading-fee slice, and a locked-LP schedule.

Only launchpads with a documented programmatic launch are implemented. As of 2026-10-13:
  Jupiter Studio  documented HTTP API (api.jup.ag/studio/v1/dbc-pool), needs JUPITER_API_KEY   -> here
  Bags            documented HTTP API, needs BAGS_API_KEY; config step may need a Jito bundle  -> here
  Meteora DBC     SDK only (@meteora-ag/dynamic-bonding-curve-sdk); own config = own fee terms  -> here (node helper)
  Raydium LaunchLab (LetsBonk) SDK only (createLaunchpad); any platform can be launched into        -> here (node helper)
  Heaven, Boop, Believe, Moonit/Moonshot, Sugar, TokenMill, PRINTR, tren.ch, PVE.WIN, Stonk,
  hours.fun: no public create-token API found; web-form or X-reply launch only                 -> not scriptable
Like pump.py, the transaction is signed here with our own key; the launchpad never holds it.
"""
from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from . import http, image as image_mod, pump
from .pump import Launched, Rpc

STUDIO = "https://api.jup.ag/studio/v1/dbc-pool"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL = "So11111111111111111111111111111111111111112"


class Launchpad(Protocol):
    name: str
    # Rough non-recoverable SOL per launch, excluding any dev buy. pump.fun's is measured on-chain;
    # a launchpad whose figure is not yet measured says so in `cost_measured`.
    launch_cost_sol: float
    cost_measured: bool

    def launch(self, rpc: Rpc, dev: Keypair, *, name: str, symbol: str, description: str, image: bytes,
               website: str = "", twitter: str = "", dev_buy_sol: float = 0.0) -> Launched: ...

    def coin_url(self, mint: str) -> str: ...

    def unclaimed_sol(self, dev: Keypair) -> float: ...

    def claim(self, rpc: Rpc, dev: Keypair) -> str:
        """Claim everything claimable; returns the last signature. Raises if unsupported."""
        ...


def sign_partial(raw: bytes, *signers: Keypair) -> VersionedTransaction:
    """Sign only our slots of a transaction someone else may have partly signed (a launchpad adds the
    mint signature itself). Rebuilding with VersionedTransaction(msg, signers) would drop theirs."""
    tx = VersionedTransaction.from_bytes(raw)
    msg = tx.message
    required = msg.header.num_required_signatures
    keys = list(msg.account_keys)[:required]
    sigs = list(tx.signatures)
    for signer in signers:
        me = signer.pubkey()
        if me not in keys:
            raise RuntimeError(f"{me} is not a required signer of this transaction")
        sigs[keys.index(me)] = signer.sign_message(to_bytes_versioned(msg))
    return VersionedTransaction.populate(msg, sigs)


@dataclass(frozen=True)
class JupiterStudio:
    api_key: str
    quote_mint: str = USDC
    initial_market_cap: float = 16_000
    migration_market_cap: float = 69_000
    quote_decimals: int = 6
    fee_bps: int = 100
    lp_locked: bool = True
    anti_sniping: bool = True
    name: str = "jupiter"
    launch_cost_sol: float = 0.02   # UNMEASURED guess (rent for pool+mint+metadata); the docs give no cost
    cost_measured: bool = False

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key}

    def create_payload(self, dev: Keypair, name: str, symbol: str, content_type: str) -> dict:
        return {
            "buildCurveByMarketCapParam": {
                "quoteMint": self.quote_mint,
                "initialMarketCap": self.initial_market_cap,
                "migrationMarketCap": self.migration_market_cap,
                "tokenQuoteDecimal": self.quote_decimals,
                "lockedVestingParam": {
                    "totalLockedVestingAmount": 0, "cliffUnlockAmount": 0, "numberOfVestingPeriod": 0,
                    "totalVestingDuration": 0, "cliffDurationFromMigrationTime": 0},
            },
            "antiSniping": self.anti_sniping,
            "fee": {"feeBps": self.fee_bps},
            "isLpLocked": self.lp_locked,
            "tokenName": name,
            "tokenSymbol": symbol,
            "tokenImageContentType": content_type,
            "creator": str(dev.pubkey()),
        }

    def launch(self, rpc: Rpc, dev: Keypair, *, name: str, symbol: str, description: str, image: bytes,
               website: str = "", twitter: str = "", dev_buy_sol: float = 0.0) -> Launched:
        if dev_buy_sol:  # create-tx has no initial-buy field; failing beats silently launching without it
            raise ValueError("Jupiter Studio's create-tx has no dev buy; set DEV_BUY_SOL=0 or use LAUNCHPAD=bags")
        ext, mime = image_mod.kind(image)
        created = json.loads(http.post_json(f"{STUDIO}/create-tx", self.create_payload(dev, name, symbol, mime),
                                            headers=self._headers()))
        # Presigned PUTs: the image and metadata live on Jupiter's static host, so no Pinata here.
        http.request("PUT", created["imagePresignedUrl"], body=image, headers={"Content-Type": mime}, timeout=120)
        meta = {"name": name, "symbol": symbol, "description": description, "image": created["imageUrl"],
                "website": website, "twitter": twitter, "telegram": ""}
        http.request("PUT", created["metadataPresignedUrl"], body=json.dumps(meta).encode(),
                     headers={"Content-Type": "application/json"}, timeout=60)
        tx = sign_partial(base64.b64decode(created["transaction"]), dev)
        body, ctype = http.multipart(
            {"transaction": base64.b64encode(bytes(tx)).decode(), "owner": str(dev.pubkey()), "content": description},
            {"headerImage": (f"header.{ext}", image, mime)})
        http.request("POST", f"{STUDIO}/submit", body=body, headers={**self._headers(), "Content-Type": ctype},
                     timeout=120)
        # Studio submits for us and its response shape is undocumented, so confirm by our own signature.
        sig = str(tx.signatures[0])
        return Launched(created["mint"], sig, rpc.confirm(sig))

    def coin_url(self, mint: str) -> str:
        return f"https://jup.ag/tokens/{mint}"

    def unclaimed_sol(self, dev: Keypair) -> float:
        return 0.0  # docs now list POST /dbc/fee (poolAddress -> unclaimed), not wired; USDC quote makes it USDC, not SOL

    def claim(self, rpc: Rpc, dev: Keypair) -> str:
        raise NotImplementedError("Jupiter claim (POST /dbc/fee/create-tx) is not wired; claim from studio.jup.ag")


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(text) - len(text.lstrip("1"))) + body


def b58encode(raw: bytes) -> str:
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\x00"))) + out


BAGS = "https://public-api-v2.bags.fm/api/v1"
JITO = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
LAMPORTS = 1_000_000_000


@dataclass(frozen=True)
class Bags:
    """Bags on Meteora DBC. Every launch needs a fee-share config; by default the dev gets all of it.
    The payer of a NEW SOL config is registered as the deployer and gets a separate 25% of the claimers'
    pool (docs: do not reserve it in the bps array), claimed through its own endpoint."""
    api_key: str
    # (wallet, bps) pairs summing to 10_000; empty = the dev wallet takes everything.
    fee_claimers: tuple[tuple[str, int], ...] = ()
    jito_tip_lamports: int = 100_000
    name: str = "bags"
    launch_cost_sol: float = 0.035  # measured 2026-10-13: first live launch, 4 txs, 0.035042 SOL (config + mint + pool rent)
    cost_measured: bool = True

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key}

    def _api(self, method: str, path: str, payload: dict | None = None, *, query: str = "",
             body: bytes | None = None, ctype: str = "") -> object:
        url = f"{BAGS}{path}{query}"
        if payload is not None:
            raw = http.post_json(url, payload, headers=self._headers())
        else:
            headers = {**self._headers(), **({"Content-Type": ctype} if ctype else {})}
            raw = http.request(method, url, body=body, headers=headers, timeout=120)
        data = json.loads(raw)
        if not data.get("success"):
            raise RuntimeError(f"Bags {path}: {data.get('error') or data.get('response')}")
        return data["response"]

    def _send_all(self, rpc: Rpc, dev: Keypair, items: list[dict]) -> str:
        """Sign our slot of each returned transaction (keeping any sponsor signature) and send in order."""
        sig = ""
        for item in items:
            tx = sign_partial(b58decode(item.get("transaction") or item["tx"]), dev)
            sig = rpc.send(tx)
            if not rpc.confirm(sig):
                raise RuntimeError(f"Bags transaction {sig[:8]} did not confirm")
        return sig

    def _send_bundle(self, rpc: Rpc, dev: Keypair, bundle: list[dict]) -> str:
        txs = [sign_partial(b58decode(i.get("transaction") or i["tx"]), dev) for i in bundle]
        http.post_json(JITO, {"jsonrpc": "2.0", "id": 1, "method": "sendBundle",
                              "params": [[b58encode(bytes(t)) for t in txs]]})
        sig = str(txs[-1].signatures[0])
        if not rpc.confirm(sig):
            raise RuntimeError(f"Bags bundle did not land (last tx {sig[:8]})")
        return sig

    def _config(self, rpc: Rpc, dev: Keypair, mint: str) -> str:
        claimers = self.fee_claimers or ((str(dev.pubkey()), 10_000),)
        if sum(b for _, b in claimers) != 10_000:
            raise ValueError("fee_claimers bps must sum to 10000")
        payload = {"payer": str(dev.pubkey()), "baseMint": mint,
                   "claimersArray": [w for w, _ in claimers], "basisPointsArray": [b for _, b in claimers]}
        cfg = self._api("POST", "/fee-share/config", payload)
        if cfg["needsCreation"] and cfg.get("bundles"):
            # Bundles ride Jito and need a tip, which the API appends when asked; re-ask with one.
            tips = json.loads(http.post_json(JITO, {"jsonrpc": "2.0", "id": 1, "method": "getTipAccounts",
                                                    "params": []}))["result"]
            cfg = self._api("POST", "/fee-share/config", {**payload, "tipWallet": tips[0],
                                                          "tipLamports": self.jito_tip_lamports})
        if cfg["needsCreation"]:
            for bundle in cfg.get("bundles") or []:
                self._send_bundle(rpc, dev, bundle)
            self._send_all(rpc, dev, cfg.get("transactions") or [])
        return cfg["meteoraConfigKey"]

    def launch(self, rpc: Rpc, dev: Keypair, *, name: str, symbol: str, description: str, image: bytes,
               website: str = "", twitter: str = "", dev_buy_sol: float = 0.0) -> Launched:
        ext, mime = image_mod.kind(image)
        fields = {"name": name[:32], "symbol": symbol[:10], "description": description[:1000]}
        for key, value in (("website", website), ("twitter", twitter)):
            if value:
                fields[key] = value
        body, ctype = http.multipart(fields, {"image": (f"{symbol.lower()}.{ext}", image, mime)})
        info = self._api("POST", "/token-launch/create-token-info", body=body, ctype=ctype)
        mint = info["tokenMint"]
        config_key = self._config(rpc, dev, mint)
        raw = self._api("POST", "/token-launch/create-launch-transaction", {
            "ipfs": info["tokenMetadata"], "tokenMint": mint, "wallet": str(dev.pubkey()),
            "initialBuyLamports": int(dev_buy_sol * LAMPORTS), "configKey": config_key})
        tx = sign_partial(b58decode(raw), dev)  # already signed with the mint
        sig = rpc.send(tx)
        return Launched(mint, sig, rpc.confirm(sig))

    def coin_url(self, mint: str) -> str:
        return f"https://bags.fm/{mint}"

    def unclaimed_sol(self, dev: Keypair) -> float:
        wallet = str(dev.pubkey())
        positions = self._api("GET", "/token-launch/claimable-positions", query=f"?wallet={wallet}")
        creator = sum(int(p["totalClaimableLamportsUserShare"]) for p in positions)
        deployer = self._api("GET", "/fee-share/deployer/claimable-positions", query=f"?wallet={wallet}")
        return (creator + int(deployer["totalClaimableLamports"])) / LAMPORTS

    def claim(self, rpc: Rpc, dev: Keypair) -> str:
        wallet = str(dev.pubkey())
        sig = ""
        for p in self._api("GET", "/token-launch/claimable-positions", query=f"?wallet={wallet}"):
            if int(p["totalClaimableLamportsUserShare"]) <= 0:
                continue
            txs = self._api("POST", "/token-launch/claim-txs/v3", {"feeClaimer": wallet, "tokenMint": p["baseMint"]})
            sig = self._send_all(rpc, dev, txs) or sig
        dep = self._api("POST", "/fee-share/deployer/claim-txs", {"wallet": wallet, "maxTransactions": 10})
        sig = self._send_all(rpc, dev, dep["transactions"]) or sig
        if not sig:
            raise RuntimeError("nothing was claimable on Bags")
        return sig


METEORA_DIR = Path(__file__).resolve().parent.parent / "meteora"
RAYDIUM_DIR = Path(__file__).resolve().parent.parent / "raydium"


@dataclass(frozen=True)
class MeteoraDbc:
    """Our own Meteora Dynamic Bonding Curve config, so the fee terms are ours.

    We are the config's partner AND each pool's creator: trading fees (net of Meteora's cut) accrue to us
    as partner, an anti-sniper fee decays from startingFeeBps to endingFeeBps over the first minute, and
    the graduated DAMM v2 LP is permanently locked (so it can't be pulled, and keeps earning). One config
    serves every launch; make it once with `python -m devbot.dbc_setup` and put its address in DBC_CONFIG.
    Transactions are built by meteora/dbc.mjs (the SDK is TypeScript) and signed here; the helper never
    sees a key. Metadata goes to Pinata like pump.fun's."""
    config: str
    pinata_jwt: str
    rpc_url: str
    mints: Callable[[], list[str]] = field(default=lambda: [], compare=False)
    initial_market_cap: float = 30          # SOL
    migration_market_cap: float = 400       # SOL
    starting_fee_bps: int = 5000
    ending_fee_bps: int = 100
    decay_seconds: int = 60
    locked_lp_percent: int = 100
    name: str = "meteora"
    launch_cost_sol: float = 0.03   # UNMEASURED guess (pool + mint + metadata rent, no pool-creation fee)
    cost_measured: bool = False

    def curve(self) -> dict:
        return {"totalSupply": 1_000_000_000, "startingFeeBps": self.starting_fee_bps,
                "endingFeeBps": self.ending_fee_bps, "decayPeriods": self.decay_seconds,
                "decaySeconds": self.decay_seconds, "lockedLpPercent": self.locked_lp_percent,
                "initialMarketCap": self.initial_market_cap, "migrationMarketCap": self.migration_market_cap}

    def _node(self, cmd: str, req: dict) -> dict:
        done = subprocess.run(["node", str(METEORA_DIR / "dbc.mjs"), cmd], input=json.dumps(
            {"rpc": self.rpc_url, **req}), capture_output=True, text=True, timeout=90, cwd=METEORA_DIR)
        if done.returncode != 0:
            raise RuntimeError(f"meteora {cmd}: {done.stderr.strip().splitlines()[-1] if done.stderr.strip() else 'failed'}")
        return json.loads(done.stdout)

    def _send(self, rpc: Rpc, built: dict, *signers: Keypair) -> str:
        tx = sign_partial(base64.b64decode(built["tx"]), *signers)
        sig = rpc.send(tx)
        if not rpc.confirm(sig):
            raise RuntimeError(f"meteora transaction {sig[:8]} did not confirm")
        return sig

    def make_config(self, rpc: Rpc, dev: Keypair) -> str:
        """One-time: create the DBC config on chain. Returns its address (put it in DBC_CONFIG)."""
        cfg = Keypair()
        built = self._node("config", {"blockhash": str(rpc.blockhash()), "payer": str(dev.pubkey()),
                                      "config": str(cfg.pubkey()), "curve": self.curve()})
        self._send(rpc, built, dev, cfg)
        return str(cfg.pubkey())

    def launch(self, rpc: Rpc, dev: Keypair, *, name: str, symbol: str, description: str, image: bytes,
               website: str = "", twitter: str = "", dev_buy_sol: float = 0.0) -> Launched:
        mint = Keypair()
        image_uri = pump.pin_image(self.pinata_jwt, symbol, image)
        uri = pump.pin_metadata(self.pinata_jwt, name=name, symbol=symbol, description=description,
                                image_uri=image_uri, website=website, twitter=twitter)
        built = self._node("pool", {"blockhash": str(rpc.blockhash()), "payer": str(dev.pubkey()),
                                    "config": self.config, "baseMint": str(mint.pubkey()), "name": name,
                                    "symbol": symbol, "uri": uri, "buyLamports": int(dev_buy_sol * LAMPORTS)})
        tx = sign_partial(base64.b64decode(built["tx"]), dev, mint)
        sig = rpc.send(tx)
        return Launched(str(mint.pubkey()), sig, rpc.confirm(sig))

    def coin_url(self, mint: str) -> str:
        return f"https://solscan.io/token/{mint}"

    def _fees(self) -> list[dict]:
        mints = self.mints()
        if not mints:
            return []
        return self._node("fees", {"config": self.config, "mints": mints})["pools"]

    def unclaimed_sol(self, dev: Keypair) -> float:
        # Quote is SOL, so lamports. creatorQuote is 0 by config (creatorTradingFeePercentage=0) but is
        # counted anyway so a changed config can't hide fees.
        return sum(int(p["partnerQuote"]) + int(p["creatorQuote"]) for p in self._fees() if "error" not in p) / LAMPORTS

    def claim(self, rpc: Rpc, dev: Keypair) -> str:
        sig = ""
        for p in self._fees():
            if "error" in p or int(p["partnerQuote"]) <= 0:
                continue
            built = self._node("claim", {"blockhash": str(rpc.blockhash()), "feeClaimer": str(dev.pubkey()),
                                         "config": self.config, "baseMint": p["mint"]})
            sig = self._send(rpc, built, dev)
        if not sig:
            raise RuntimeError("nothing was claimable on Meteora")
        return sig


LETSBONK_PLATFORM = "FfYek5vEz23cMkWsdJwG2oa6EphsvXSHrGpdALN4g6W1"   # creatorFeeRate 0: creators earn nothing there


@dataclass(frozen=True)
class LaunchLab:
    """Raydium LaunchLab, on the platform of your choice (`platform_id`; blank = Raydium's own).

    A LaunchLab platform sets the fee split, and platforms are permissionless: ~3,800 exist. What the dev
    earns is the platform's creatorFeeRate (Raydium's own 0.05%; LetsBonk 0%; several third parties 0.5%).
    On OUR OWN platform (`python -m devbot.launchlab_setup`, id derived from the dev wallet) we also take
    the platform fee and the graduated LP position. Transactions are built by raydium/launchlab.mjs and
    signed here; the helper never sees a key. Metadata goes to Pinata like pump.fun's."""
    pinata_jwt: str
    rpc_url: str
    platform_id: str = ""
    priority_fee_sol: float = 0.00005
    name: str = "launchlab"
    launch_cost_sol: float = 0.0207  # measured 2026-10-13: first live launch, 1 tx, 0.020667 SOL (mint + pool + vault rent)
    cost_measured: bool = True

    def _node(self, cmd: str, req: dict) -> dict:
        micro = int(self.priority_fee_sol * LAMPORTS * 1_000_000 / 400_000)  # per-CU price for the 400k-unit budget
        done = subprocess.run(["node", str(RAYDIUM_DIR / "launchlab.mjs"), cmd], input=json.dumps(
            {"rpc": self.rpc_url, "microLamports": micro, **req}), capture_output=True, text=True, timeout=120,
            cwd=RAYDIUM_DIR)
        if done.returncode != 0:
            lines = [ln for ln in done.stderr.strip().splitlines() if not ln.startswith("bigint:")]
            raise RuntimeError(f"launchlab {cmd}: {lines[-1] if lines else 'failed'}")
        return json.loads(done.stdout)

    def _send_all(self, rpc: Rpc, built: dict, *signers: Keypair) -> str:
        sig = ""
        for raw in built["txs"]:
            sig = rpc.send(sign_partial(base64.b64decode(raw), *signers))
            if not rpc.confirm(sig):
                raise RuntimeError(f"launchlab transaction {sig[:8]} did not confirm")
        return sig

    def make_platform(self, rpc: Rpc, dev: Keypair, *, name: str, web: str, img: str, fee_rate: int,
                      creator_fee_rate: int) -> str:
        """One-time: create our own platform. The program rejects a creator share of the graduated LP
        for new platforms (any creatorScale > 0 fails), so the lock position goes to the platform wallet,
        which is the dev wallet. Returns the platform id (put it in LAUNCHLAB_PLATFORM)."""
        built = self._node("platform-create", {"payer": str(dev.pubkey()), "name": name, "web": web, "img": img,
                                               "feeRate": fee_rate, "creatorFeeRate": creator_fee_rate,
                                               "lockScales": [1_000_000, 0, 0]})
        self._send_all(rpc, built, dev)
        return built["platformId"]

    def launch(self, rpc: Rpc, dev: Keypair, *, name: str, symbol: str, description: str, image: bytes,
               website: str = "", twitter: str = "", dev_buy_sol: float = 0.0) -> Launched:
        mint = Keypair()
        image_uri = pump.pin_image(self.pinata_jwt, symbol, image)
        uri = pump.pin_metadata(self.pinata_jwt, name=name, symbol=symbol, description=description,
                                image_uri=image_uri, website=website, twitter=twitter)
        built = self._node("launch", {"payer": str(dev.pubkey()), "mint": str(mint.pubkey()), "name": name,
                                      "symbol": symbol, "uri": uri, "platformId": self.platform_id,
                                      "buyLamports": int(dev_buy_sol * LAMPORTS)})
        tx = sign_partial(base64.b64decode(built["txs"][0]), dev, mint)
        sig = rpc.send(tx)
        return Launched(str(mint.pubkey()), sig, rpc.confirm(sig))

    def coin_url(self, mint: str) -> str:
        return f"https://raydium.io/launchpad/token/?mint={mint}"

    def _unclaimed(self, dev: Keypair) -> dict:
        return self._node("unclaimed", {"creator": str(dev.pubkey()), "platformId": self.platform_id})

    def unclaimed_sol(self, dev: Keypair) -> float:
        u = self._unclaimed(dev)
        return (int(u["lamports"]) + int(u["platformLamports"])) / LAMPORTS

    def claim(self, rpc: Rpc, dev: Keypair) -> str:
        u, sig = self._unclaimed(dev), ""
        payer = str(dev.pubkey())
        if int(u["lamports"]) > 0:
            sig = self._send_all(rpc, self._node("claim-creator", {"payer": payer}), dev) or sig
        if int(u["platformLamports"]) > 0:
            built = self._node("claim-platform", {"payer": payer, "platformId": self.platform_id})
            sig = self._send_all(rpc, built, dev) or sig
        if not sig:
            raise RuntimeError("nothing was claimable on LaunchLab")
        return sig


def build(name: str, *, jupiter_api_key: str = "", bags_api_key: str = "", dbc_config: str = "",
          pinata_jwt: str = "", rpc_url: str = "", mints: Callable[[], list[str]] = lambda: [],
          dbc_initial_mcap: float = 30, dbc_migration_mcap: float = 400, launchlab_platform: str = "",
          priority_fee_sol: float = 0.00005) -> Launchpad | None:
    """None means pump.fun, which keeps its own richer code path in pump.py."""
    if name in ("", "pump"):
        return None
    if name == "jupiter":
        if not jupiter_api_key:
            raise ValueError("LAUNCHPAD=jupiter requires JUPITER_API_KEY")
        return JupiterStudio(api_key=jupiter_api_key)
    if name == "bags":
        if not bags_api_key:
            raise ValueError("LAUNCHPAD=bags requires BAGS_API_KEY")
        return Bags(api_key=bags_api_key)
    if name == "meteora":
        if not (dbc_config and pinata_jwt):
            raise ValueError("LAUNCHPAD=meteora requires DBC_CONFIG (python -m devbot.dbc_setup) and PINATA_JWT")
        return MeteoraDbc(config=dbc_config, pinata_jwt=pinata_jwt, rpc_url=rpc_url, mints=mints,
                          initial_market_cap=dbc_initial_mcap, migration_market_cap=dbc_migration_mcap)
    if name in ("launchlab", "letsbonk"):
        if not pinata_jwt:
            raise ValueError("LAUNCHPAD=launchlab requires PINATA_JWT")
        platform = LETSBONK_PLATFORM if name == "letsbonk" else launchlab_platform
        return LaunchLab(pinata_jwt=pinata_jwt, rpc_url=rpc_url, platform_id=platform,
                         priority_fee_sol=priority_fee_sol, name=name)
    raise ValueError(f"LAUNCHPAD={name!r} has no programmatic launch implemented (see devbot/launchpads.py)")


__all__ = ["Launchpad", "JupiterStudio", "Bags", "MeteoraDbc", "LaunchLab", "build", "sign_partial"]
