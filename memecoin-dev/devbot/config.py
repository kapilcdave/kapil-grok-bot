"""Settings, read once from the environment (a 0600 .env loaded by systemd).

Live spending is opt-in and has no default budget: LIVE=1 without an explicit
MAX_SOL_PER_DAY refuses to start, so a budget is always the operator's call at
the call site, never something a script inherited.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from . import openrouter, social


class ConfigError(RuntimeError):
    pass


def _need(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _hours(name: str, default: tuple[int, int]) -> tuple[int, int]:
    """'6-12' -> (6, 12): UTC hours [start, end) with no scheduled posts; '' keeps the default."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    start, _, end = raw.partition("-")
    hours = (int(start), int(end))
    if not all(0 <= h <= 23 for h in hours):
        raise ConfigError(f"{name} must look like 6-12 (UTC hours)")
    return hours


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_users: frozenset[int]
    home_chat: int
    state_dir: Path
    keypair_path: Path
    rpc_urls: list[str]
    pinata_jwt: str
    cf_account_id: str
    cf_api_token: str
    llm_model: str
    live: bool
    max_sol_per_day: float
    max_launches_per_day: int
    floor_pct: float
    dev_buy_sol: float
    slippage_pct: int
    priority_fee_sol: float
    scan_minutes: int
    disclosure: str
    website_base: str = ""
    claim_min_sol: float = 0.005
    x_keys: social.XKeys | None = None
    x_max_posts_per_day: int = 20
    fresh_minutes: float = 5.0
    hot_min_buyers: int = 8
    openrouter_key: str = ""
    openrouter_models: tuple[str, ...] = tuple(openrouter.TEXT_MODELS)
    groq_key: str = ""
    groq_models: tuple[str, ...] = tuple(openrouter.GROQ_MODELS)
    pollinations_text_models: tuple[str, ...] = tuple(openrouter.POLLINATIONS_MODELS)
    image_fallback: bool = True
    pollinations_token: str = ""
    x_drafts_per_day: int = 0
    launcher: str = "pumpportal"    # how a pump.fun create is sent: pumpportal (we sign) or j7 (j7tracker signs)
    j7_wallet_key: str = ""
    j7_region: str = "nyc"
    feed_min_followers: int = 200_000
    feed_max_per_hour: int = 6
    feed_max_age_s: float = 60.0
    feed_max_copies: int = 4
    feed_auto_sol_per_day: float = 0.0   # SOL a day the feed may launch without a tap (0-copy posts only); 0 = off
    feed_auto_max_per_hour: int = 3
    feed_auto_max_age_s: float = 45.0    # older than this when designed: offered with buttons instead
    x_handle: str = ""     # the coin's X link when memephil's launch post fails: x.com/<handle>
    mayhem: bool = False   # pump.fun Mayhem mode: pump.fun's own agent trades the coin for its first 24 h
    content_quiet_utc: tuple[int, int] = (6, 12)
    x_replies_per_hour: int = 4          # reply drafts off the feed sent to Telegram; 0 = off
    x_reply_min_followers: int = 100_000
    x_reply_max_age_s: float = 300.0
    launchpad: str = "pump"
    jupiter_api_key: str = ""
    bags_api_key: str = ""
    dbc_config: str = ""
    launchlab_platform: str = ""
    dbc_initial_mcap: float = 30
    dbc_migration_mcap: float = 400
    pvp_min_buyers: int = 8      # outside buyers a coin needs before /pvp clones it
    pvp_max_copies: int = 4      # skip a coin that already has this many copies out
    pvp_poll_s: float = 30.0
    rotate_wallets: bool = False   # a fresh creator wallet per launch, funded by the main dev wallet (wallets.py)
    autosell_pct: int = 0          # sell this % of the dev buy as soon as outside SOL lands on the curve; 0 = off
    autosell_min_sol: float = 0.05 # outside SOL on the curve that counts as "the snipers came"
    autosell_watch_s: float = 20.0 # how long after confirmation to watch for them

    @property
    def db_path(self) -> Path:
        return self.state_dir / "devbot.sqlite"


def load() -> Config:
    users = frozenset(
        int(u) for u in os.environ.get("TELEGRAM_ALLOWED_USERS", "").replace(" ", "").split(",") if u
    )
    if not users:
        # The bot can spend SOL; an empty allowlist would hand that to anyone who finds it.
        raise ConfigError("TELEGRAM_ALLOWED_USERS must list at least one numeric user id")
    live = os.environ.get("LIVE", "0").strip() == "1"
    # "none" turns the daily SOL cap off, but only when written out: an empty or missing value
    # still refuses LIVE=1, so no budget is ever implied. The balance floor still applies.
    raw_max = os.environ.get("MAX_SOL_PER_DAY", "").strip().lower()
    max_sol = math.inf if raw_max == "none" else float(raw_max) if raw_max else 0.0
    if live and max_sol <= 0:
        raise ConfigError("LIVE=1 requires MAX_SOL_PER_DAY: a number, or 'none' for no daily cap")
    pinata_jwt = os.environ.get("PINATA_JWT", "").strip()
    if live and not pinata_jwt:
        raise ConfigError("LIVE=1 requires PINATA_JWT (metadata upload); previews do not")
    website_base = os.environ.get("WEBSITE_BASE", "").strip().rstrip("/")
    if website_base and not website_base.startswith("https://"):
        raise ConfigError("WEBSITE_BASE must be an https:// URL (the site/ worker), or empty")
    x_vals = [os.environ.get(k, "").strip() for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    if any(x_vals) and not all(x_vals):
        raise ConfigError("X posting needs all four of X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET")
    x_keys = social.XKeys(*x_vals) if all(x_vals) else None
    launcher = os.environ.get("LAUNCHER", "pumpportal").strip().lower() or "pumpportal"
    j7_key = os.environ.get("J7_WALLET_KEY", "").strip()
    j7_region = os.environ.get("J7_REGION", "nyc").strip().lower() or "nyc"
    if launcher not in ("pumpportal", "j7"):
        raise ConfigError("LAUNCHER must be pumpportal or j7")
    if launcher == "j7" and not j7_key:
        raise ConfigError("LAUNCHER=j7 needs J7_WALLET_KEY (the encrypted wallet key from j7tracker's app)")
    if j7_region not in ("nyc", "lax", "fra", "sgp"):
        raise ConfigError("J7_REGION must be nyc, lax, fra or sgp")
    mayhem = os.environ.get("MAYHEM", "0").strip() == "1"
    if mayhem and (launcher != "pumpportal" or (os.environ.get("LAUNCHPAD", "pump").strip().lower() or "pump") != "pump"):
        raise ConfigError("MAYHEM=1 is a pump.fun create flag sent through PumpPortal: it needs LAUNCHER=pumpportal "
                          "and LAUNCHPAD=pump")
    launchpad = os.environ.get("LAUNCHPAD", "pump").strip().lower() or "pump"
    rotate = os.environ.get("ROTATE_WALLETS", "0").strip() == "1"
    if rotate and (launcher != "pumpportal" or launchpad != "pump"):
        raise ConfigError("ROTATE_WALLETS=1 signs each create with a new local key: it needs LAUNCHER=pumpportal "
                          "(j7 signs with its own copy of one key) and LAUNCHPAD=pump (pads claim fees per dev key)")
    autosell_pct = _int("AUTOSELL_PCT", 0)
    if not 0 <= autosell_pct <= 100:
        raise ConfigError("AUTOSELL_PCT must be 0-100 (0 = off)")
    if autosell_pct and launchpad != "pump":
        raise ConfigError("AUTOSELL_PCT reads the pump.fun bonding curve and sells through PumpPortal: it needs LAUNCHPAD=pump")
    state_dir = Path(os.environ.get("DEVBOT_STATE", "~/.devbot")).expanduser()
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return Config(
        telegram_token=_need("DEVBOT_TELEGRAM_TOKEN"),
        allowed_users=users,
        home_chat=int(_need("TELEGRAM_HOME_CHAT")),
        state_dir=state_dir,
        keypair_path=Path(_need("DEV_KEYPAIR_PATH")).expanduser(),
        rpc_urls=[u.strip() for u in os.environ.get(
            "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com").split(",") if u.strip()],
        pinata_jwt=pinata_jwt,
        cf_account_id=_need("CLOUDFLARE_ACCOUNT_ID"),
        cf_api_token=_need("CLOUDFLARE_API_TOKEN"),
        llm_model=os.environ.get("DEVBOT_MODEL", "").strip() or "@cf/meta/llama-3.1-8b-instruct-fp8-fast",
        live=live,
        max_sol_per_day=max_sol,
        max_launches_per_day=_int("MAX_LAUNCHES_PER_DAY", 0),  # 0 = no count cap
        claim_min_sol=_float("CLAIM_MIN_SOL", 0.005),
        floor_pct=_float("FLOOR_PCT", 50.0),
        dev_buy_sol=_float("DEV_BUY_SOL", 0.0),
        slippage_pct=_int("SLIPPAGE_PCT", 10),
        priority_fee_sol=_float("PRIORITY_FEE_SOL", 0.00005),
        scan_minutes=_int("SCAN_MINUTES", 15),
        disclosure=os.environ.get(
            "DISCLOSURE", "Dev buy: {dev_buy} SOL. Dev wallet: {wallet}. Dev sells are posted publicly."
        ),
        website_base=website_base,
        x_keys=x_keys,
        x_max_posts_per_day=_int("X_MAX_POSTS_PER_DAY", 20),
        fresh_minutes=_float("FRESH_MINUTES", 5.0),
        hot_min_buyers=_int("HOT_MIN_BUYERS", 8),
        openrouter_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
        openrouter_models=tuple(m.strip() for m in os.environ.get("OPENROUTER_MODELS", "").split(",") if m.strip())
        or tuple(openrouter.TEXT_MODELS),
        groq_key=os.environ.get("GROQ_API_KEY", "").strip(),
        groq_models=tuple(m.strip() for m in os.environ.get("GROQ_MODELS", "").split(",") if m.strip())
        or tuple(openrouter.GROQ_MODELS),
        pollinations_text_models=tuple(m.strip() for m in os.environ.get("POLLINATIONS_TEXT_MODELS", "").split(",")
                                       if m.strip()) or tuple(openrouter.POLLINATIONS_MODELS),
        image_fallback=os.environ.get("IMAGE_FALLBACK", "1").strip() != "0",
        pollinations_token=os.environ.get("POLLINATIONS_TOKEN", "").strip(),
        x_drafts_per_day=_int("X_DRAFTS_PER_DAY", 0),
        launcher=launcher,
        j7_wallet_key=j7_key,
        j7_region=j7_region,
        feed_min_followers=_int("FEED_MIN_FOLLOWERS", 200_000),
        feed_max_per_hour=_int("FEED_MAX_PER_HOUR", 6),
        feed_max_age_s=_float("FEED_MAX_AGE_S", 60.0),
        feed_max_copies=_int("FEED_MAX_COPIES", 4),
        feed_auto_sol_per_day=_float("FEED_AUTO_SOL_PER_DAY", 0.0),
        feed_auto_max_per_hour=_int("FEED_AUTO_MAX_PER_HOUR", 3),
        feed_auto_max_age_s=_float("FEED_AUTO_MAX_AGE_S", 45.0),
        x_handle=os.environ.get("X_HANDLE", "").strip().lstrip("@"),
        mayhem=mayhem,
        content_quiet_utc=_hours("CONTENT_QUIET_UTC", (6, 12)),
        x_replies_per_hour=_int("X_REPLIES_PER_HOUR", 4),
        x_reply_min_followers=_int("X_REPLY_MIN_FOLLOWERS", 100_000),
        x_reply_max_age_s=_float("X_REPLY_MAX_AGE_S", 300.0),
        launchpad=launchpad,
        jupiter_api_key=os.environ.get("JUPITER_API_KEY", "").strip(),
        bags_api_key=os.environ.get("BAGS_API_KEY", "").strip(),
        dbc_config=os.environ.get("DBC_CONFIG", "").strip(),
        launchlab_platform=os.environ.get("LAUNCHLAB_PLATFORM", "").strip(),
        dbc_initial_mcap=_float("DBC_INITIAL_MCAP", 30),
        dbc_migration_mcap=_float("DBC_MIGRATION_MCAP", 400),
        pvp_min_buyers=_int("PVP_MIN_BUYERS", 8),
        pvp_max_copies=_int("PVP_MAX_COPIES", 4),
        pvp_poll_s=_float("PVP_POLL_S", 30.0),
        rotate_wallets=rotate,
        autosell_pct=autosell_pct,
        autosell_min_sol=_float("AUTOSELL_MIN_SOL", 0.05),
        autosell_watch_s=_float("AUTOSELL_WATCH_S", 20.0),
    )
