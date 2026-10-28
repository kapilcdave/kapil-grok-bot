"""Telegram front end and the headless loop.

    python -m devbot.bot

One process: long-polls Telegram for commands, records pump.fun launches and
trades every POLL_S for trends.py, and, when /auto is on, runs a
narrative -> concept -> image -> launch cycle every SCAN_MINUTES. With LIVE=0
(the default) every cycle stops at a preview; nothing is uploaded or sent on-chain.
"""
from __future__ import annotations

import json
import random
import logging
import queue
import secrets
import sys
import time
import traceback
from dataclasses import dataclass, field

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from . import concept as concept_mod
from . import config as config_mod
from . import audience, http, image, j7, launchpads, openrouter, pump, scanner, social
from . import wallets as wallets_mod
from . import content as content_mod
from . import feed as feed_mod
from . import trends as trends_mod
from .ledger import CLONE_SOURCE, Ledger, check_launch

log = logging.getLogger("devbot")
PROPOSAL_TTL_S = 30 * 60
DRAFT_TTL_S = 12 * 3600    # an unanswered X draft can still be posted for this long
TOPIC_COOLDOWN_S = 12 * 3600
HOUSEKEEP_S = 600       # auto-claim fees and free dead coins' Pinata files this often
POLL_S = 120            # pump.fun shows ~3 min of launches per page; poll inside that
# Creator share of pump.fun volume, measured on-chain (30 of 125 bp); used only for estimates.
CREATOR_FEE_RATE = 0.0030
FEED_SOURCE = "X post"  # source_symbol of a coin launched off someone's post (the j7tracker feed)
# A dropped create used to wait 25 s before its resend (pigeondim, 2026-10-14: landed 30 s after the
# click); the first seconds are when buyers come, so resend sooner. Same mint, so at most one lands.
CREATE_RESEND_S = 6
CREATE_TRIES = 5
AUTO_SPEND = "auto "        # spend label prefix of feed auto-launches (their own daily budget)
SELL_PCTS = (25, 50, 100)   # the launch message's Sell buttons (dev wallet's own tokens)
SELL_DEBOUNCE_S = 15
MAX_DEV_BUY_SOL = 1.0       # /devbuy refuses more; the balance floor still applies below it
# ROTATE_WALLETS: a new creator wallet gets cost() plus this (and 2% of the dev buy) so the create never
# runs short; the rest is swept back once the wallet has been idle SWEEP_AFTER_S.
FUND_MARGIN_SOL = 0.005
SWEEP_AFTER_S = 900
ROTATED_CLAIM_MIN_SOL = 0.001   # below this a rotated wallet's fees don't cover top-up + claim + sweep fees
AUTOSELL_POLL_S = 0.5

# One list feeds both /help and Telegram's "/" menu (setMyCommands).
COMMANDS = [
    ("propose", "pick a live narrative, design a coin for it, show it with Launch/Skip"),
    ("coin", "<your idea> - design a coin from your own idea, show it with Launch/Skip"),
    ("trends", "coins from the last few minutes that people are buying now"),
    ("auto", "on|off - launch every cycle without asking (LIVE=1 only)"),
    ("status", "mode, caps, balance, today's spend"),
    ("stats", "our coins: 24h volume and estimated creator fees"),
    ("humans", "[n] who bought our last n coins (default 5), and feed coins vs the rest"),
    ("post", "draft an AI-ified meme post for X now (on|off: the X_DRAFTS_PER_DAY schedule)"),
    ("clone", "[mint] - an exact copy (name, ticker, picture) of that pump.fun coin, or of the hottest one now"),
    ("pvp", "off|ask|auto - clone any new coin that gets PVP_MIN_BUYERS buyers (ask: Launch/Skip, auto: launch)"),
    ("feed", "on|off - coins off big accounts' X posts from j7tracker's live feed (needs a /j7 session); "
             "auto <SOL>|off - launch posts nobody has coined yet without a tap, up to that much a day"),
    ("replies", "on|off - memephil reply drafts under big accounts' fresh X posts (feed), each a Reply on X link"),
    ("devbuy", "<SOL> - how much the dev wallet buys of each new coin (0 = none); launches then get Sell buttons"),
    ("sell", "<ticker or mint> <percent> - sell that share of the dev wallet's tokens of one of our coins"),
    ("claim", "claim all accrued creator fees"),
    ("unwrap", "turn the wallet's wSOL into native SOL (launches spend native SOL)"),
    ("j7", "<__Secure-j7_at cookie> - give the bot a j7tracker session for the feed; the message is deleted"),
    ("stop", "hard switch: no launch is sent until /resume"),
    ("resume", "undo /stop"),
    ("rebase", "reset the balance-floor baseline to the current balance"),
    ("help", "list the commands"),
]
HELP = "\n".join(f"/{c} - {d}" for c, d in COMMANDS)


def quiet(t: float, window: tuple[int, int]) -> bool:
    """Whether epoch time t falls in the UTC [start, end) hours of CONTENT_QUIET_UTC (may wrap midnight)."""
    start, end = window
    hour = time.gmtime(t).tm_hour
    return start != end and ((start <= hour < end) if start < end else (hour >= start or hour < end))


@dataclass
class Proposal:
    concept: concept_mod.Concept
    image: bytes
    created: float = field(default_factory=time.time)
    # Made before the click so Launch only has to post, pin the metadata and send.
    mint: Keypair = field(default_factory=Keypair)
    image_uri: str = ""
    art_note: str = ""
    quote_id: str = ""   # a feed coin's source post: memephil's launch post quotes it
    source: str = ""     # a feed coin's poster (@handle) and post link, for the ledger and /humans
    source_url: str = ""
    clone: bool = False  # an exact copy of another pump.fun coin (source_mint is that coin)


@dataclass
class Drafted:
    draft: content_mod.Draft
    image: bytes | None
    created: float = field(default_factory=time.time)


class Bot:
    def __init__(self, cfg: config_mod.Config) -> None:
        self.cfg = cfg
        openrouter.configure(cfg.groq_key, cfg.groq_models, cfg.pollinations_token, cfg.pollinations_text_models)
        self.ledger = Ledger(cfg.db_path)
        self.dev = pump.load_keypair(cfg.keypair_path)
        self.wallet = str(self.dev.pubkey())
        self.rpc = pump.Rpc(cfg.rpc_urls)
        # kept whenever rotated wallets exist, even with ROTATE_WALLETS=0: their coins' fees still need claiming
        wallet_dir = cfg.state_dir / "wallets"
        self.wallets = wallets_mod.Wallets(wallet_dir) if cfg.rotate_wallets or wallet_dir.exists() else None
        self.pad = launchpads.build(cfg.launchpad, jupiter_api_key=cfg.jupiter_api_key,
                                 bags_api_key=cfg.bags_api_key, dbc_config=cfg.dbc_config,
                                 pinata_jwt=cfg.pinata_jwt, rpc_url=cfg.rpc_urls[0],
                                 mints=lambda: self.ledger.pad_mints(cfg.launchpad),
                                 dbc_initial_mcap=cfg.dbc_initial_mcap, dbc_migration_mcap=cfg.dbc_migration_mcap,
                                 launchlab_platform=cfg.launchlab_platform, priority_fee_sol=cfg.priority_fee_sol)  # None = pump.fun
        self.api = f"https://api.telegram.org/bot{cfg.telegram_token}"
        self.proposals: dict[str, Proposal] = {}
        self.recent_sells: dict[str, float] = {}  # mint -> when its last sell was sent
        # Feed auto-launch: the feed thread queues finished 0-copy designs; the main thread (which owns
        # the ledger) re-checks and launches them. feed_auto_cap is the main thread's copy for the feed thread.
        self.auto_queue: queue.Queue = queue.Queue()
        self.feed_auto_cap = 0.0
        self.auto_sent: list[float] = []
        self.offset = 0
        self.next_cycle = time.time() + 60
        self.trends = trends_mod.Trends(cfg.state_dir / "trends.sqlite")
        self.next_poll = 0.0
        self.auto_skip = ""  # last reason auto waited, so a cap is reported once, not every cycle
        self.next_housekeep = time.time() + 90
        self.housekeep_error = ""
        self.cycle_error = ""
        self.x_error = ""
        self.wallet_kinds: dict[str, str] = {}  # audience.classify cache: one RPC call per wallet
        self.drafts: dict[str, Drafted] = {}
        self.feed: feed_mod.Feed | None = None
        self.feed_avoid: list[str] = []
        self.feed_error = ""
        self.feed_coins = True            # /feed on|off: coin designs; the connection also serves replies
        self.recent_replies: list[str] = []   # the reply thread's own memory, so it doesn't repeat itself
        self.next_feed_check = 0.0
        self.content_error = ""
        self.next_pvp = 0.0
        self.pvp_seen: set[str] = set()   # sources already offered or launched against this run
        self.pvp_error = ""

    def balance(self) -> float:
        """Current balance; the first successful read ever also records the floor's baseline."""
        bal = self.rpc.balance_sol(self.wallet)
        if self.ledger.baseline is None:
            self.ledger.put("baseline_sol", str(bal))
        return bal

    # ---- telegram -------------------------------------------------------------
    def tg(self, method: str, payload: dict, timeout: float = 40) -> dict:
        return json.loads(http.post_json(f"{self.api}/{method}", payload, timeout=timeout))

    def say(self, text: str, markup: dict | None = None) -> None:
        payload = {"chat_id": self.cfg.home_chat, "text": text[:4000], "disable_web_page_preview": True}
        if markup:
            payload["reply_markup"] = markup
        try:
            self.tg("sendMessage", payload)
        except Exception:
            log.exception("sendMessage failed")

    def photo(self, data: bytes, caption: str, markup: dict | None = None) -> None:
        fields = {"chat_id": str(self.cfg.home_chat), "caption": caption[:1000]}
        if markup:
            fields["reply_markup"] = json.dumps(markup)
        ext, mime = image.kind(data)
        body, ctype = http.multipart(fields, {"photo": (f"coin.{ext}", data, mime)})
        try:
            http.request("POST", f"{self.api}/sendPhoto", body=body, headers={"Content-Type": ctype}, timeout=60)
        except Exception:
            log.exception("sendPhoto failed")
            self.say(caption)

    # ---- pipeline -------------------------------------------------------------
    def next_angle(self, n_narratives: int) -> str:
        angles = [a for a in concept_mod.ANGLES if a != "mashup" or n_narratives >= 2]
        i = int(self.ledger.get("angle_i", "0"))
        self.ledger.put("angle_i", str(i + 1))
        return angles[i % len(angles)]

    def live_narratives(self) -> list[trends_mod.Narrative]:
        """Coins launched in the last FRESH_MINUTES that people are buying right now, read live."""
        narratives, checked = trends_mod.hot_now(self.cfg.fresh_minutes, self.cfg.hot_min_buyers)
        if not narratives:
            raise RuntimeError(f"nothing launched in the last {self.cfg.fresh_minutes:g} min has "
                               f"{self.cfg.hot_min_buyers}+ buyers yet. Checked: "
                               + ("; ".join(checked) or "no fresh coins"))
        return narratives

    def propose(self) -> Proposal:
        narratives = self.live_narratives()
        angle = self.next_angle(len(narratives))
        avoid = self.ledger.used_names() + [x for n in narratives for c in n.examples for x in (c.name, c.symbol)]
        posts = {n.terms[0]: self.trends.post_texts(n) for n in narratives}  # the stories behind the waves
        last_error: Exception | None = None
        for _ in range(3):
            try:
                c = concept_mod.generate(self.cfg.cf_account_id, self.cfg.cf_api_token,
                                         self.cfg.llm_model, narratives, angle, avoid, posts=posts,
                                         openrouter_key=self.cfg.openrouter_key,
                                         openrouter_models=list(self.cfg.openrouter_models))
            except concept_mod.ConceptError as e:
                last_error = e
                continue
            if scanner.symbol_taken(c.symbol, c.name):
                avoid += [c.name, c.symbol]
                last_error = RuntimeError(f"{c.symbol} / {c.name} already trades on Solana")
                continue
            art, note = self.art(c)
            log.info("proposal %s / %s: %s, %d bytes", c.name, c.symbol, note, len(art))
            p = Proposal(c, art, art_note=note)
            self.pin_art(p)
            return p
        raise RuntimeError(f"no usable concept after 3 tries: {last_error}")

    def idea(self, text: str) -> Proposal:
        """A proposal from the operator's own idea (/coin <idea>)."""
        avoid = self.ledger.used_names()
        last: Exception | None = None
        for _ in range(3):
            try:
                c = concept_mod.from_idea(self.cfg.cf_account_id, self.cfg.cf_api_token, self.cfg.llm_model,
                                          text, avoid, openrouter_key=self.cfg.openrouter_key,
                                          openrouter_models=list(self.cfg.openrouter_models))
            except concept_mod.ConceptError as e:
                last = e
                continue
            if scanner.symbol_taken(c.symbol, c.name):
                avoid += [c.name, c.symbol]
                last = RuntimeError(f"{c.symbol} / {c.name} already trades on Solana")
                continue
            art, note = self.art(c)
            p = Proposal(c, art, art_note=note)
            self.pin_art(p)
            return p
        raise RuntimeError(f"no usable coin from that idea after 3 tries: {last}")

    # ---- PVP: an exact copy of a coin that is being bought -------------------------
    def clone(self, src: trends_mod.Coin, copies: int = 0, buyers: int = 0) -> Proposal:
        """Same name, ticker, description and picture as `src`. Only the links are ours: the coin points at
        memephil's launch post, never at the original's X account or site."""
        url = image.source_url(src.image)
        if not url.startswith("https://"):
            raise RuntimeError(f"${src.symbol} has no usable image URL: {src.image!r}")
        try:
            art = image.fit(http.request("GET", url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30))
        except Exception as e:  # PIL errors on a non-image too
            raise RuntimeError(f"${src.symbol}'s picture is unreadable: {str(e)[:120]}") from None
        live = (f"PVP: {src.name} (${src.symbol}), {(time.time() - src.created) / 60:.1f} min old, "
                f"{buyers} buyers, peak ${src.peak_usd / 1000:.1f}k, {copies} copies already out")
        n = trends_mod.Narrative([src.symbol.lower()], buyers, copies + 1, 0.0, 0.0, copies + 1, 1, 0,
                                 examples=[src], live=live)
        c = concept_mod.Concept(n, "clone", src.name, src.symbol, concept_mod.clean_description(src.description) or src.name,
                                "", f"{buyers} outside wallets are buying ${src.symbol}", story="exact copy")
        p = Proposal(c, art, art_note=f"${src.symbol}'s own picture, unchanged", clone=True)
        self.pin_art(p)
        return p

    def pvp_tick(self) -> None:
        mode = self.ledger.get("pvp", "off")
        if mode == "off" or self.ledger.stopped:
            return
        if mode == "auto" and self.cfg.live and not self.verdict().ok:
            return  # a cap or the floor: auto waits quietly, it does not spend
        narratives, _ = trends_mod.hot_now(self.cfg.fresh_minutes, self.cfg.pvp_min_buyers)
        for n in narratives:  # hottest first
            src = n.examples[0]
            copies = n.launches_1h - 1
            if (src.mint in self.pvp_seen or src.creator == self.wallet or self.ledger.cloned(src.mint)
                    or (self.wallets and src.creator in self.wallets.pubkeys())
                    or copies > self.cfg.pvp_max_copies):
                continue
            self.pvp_seen.add(src.mint)
            p = self.clone(src, copies, int(n.score))
            if mode == "auto" and self.cfg.live:
                self.photo(p.image, self.caption(p, "PVP auto launch"))
                self.launch(p)
            else:
                self.offer(p, "PVP")
            return

    def pvp_cycle(self) -> None:
        """pvp_tick every PVP_POLL_S; a lasting failure is reported once, not every poll."""
        try:
            self.pvp_tick()
        except Exception as e:
            log.error("pvp failed: %s\n%s", e, traceback.format_exc())
            if str(e)[:120] != self.pvp_error:
                self.say(f"pvp failed (reported once until it changes): {e}")
            self.pvp_error = str(e)[:120]
            return
        self.pvp_error = ""

    def pin_art(self, p: Proposal) -> None:
        """Pin the art at proposal time when PumpPortal will need it on IPFS (j7tracker takes it inline)."""
        if self.cfg.live and self.cfg.pinata_jwt and self.cfg.launcher != "j7":
            p.image_uri = pump.pin_image(self.cfg.pinata_jwt, p.concept.symbol, p.image)

    # ---- j7tracker feed: coins off other people's posts ----------------------------
    def design(self, post: feed_mod.Post) -> bool:
        """Runs on the feed's worker thread: network only, no ledger (sqlite is the main thread's).
        True once a proposal was sent; only those count toward FEED_MAX_PER_HOUR."""
        if not self.feed_coins:  # the feed is connected for replies only
            return False
        t0 = time.time()
        avoid = list(self.feed_avoid)
        for _ in range(2):
            c = concept_mod.from_post(self.cfg.cf_account_id, self.cfg.cf_api_token, self.cfg.llm_model, post.handle,
                                      post.followers, post.text, post.suggestion, avoid,
                                      openrouter_key=self.cfg.openrouter_key,
                                      openrouter_models=list(self.cfg.openrouter_models))
            if not scanner.symbol_taken(c.symbol, c.name):
                break
            log.info("feed: %s / %s already trades", c.symbol, c.name)
            avoid += [c.name, c.symbol]
        else:
            return False
        art = image.generate(self.cfg.cf_account_id, self.cfg.cf_api_token, c.image_prompt,
                             fallback=self.cfg.image_fallback, pollinations_token=self.cfg.pollinations_token)
        if post.copies > self.cfg.feed_max_copies:  # swarmed while we designed: ours would be one of many
            log.info("feed: skipped %s / %s, %d copies already off %s", c.name, c.symbol, post.copies, post.url)
            return False
        p = Proposal(c, art, art_note="fresh art", quote_id=post.id, source=f"@{post.handle}", source_url=post.url)
        self.pin_art(p)
        copies = f"{post.copies} copies already" if post.copies else "no copies seen yet"
        obvious = f'obvious name "{post.suggestion}" ({copies})' if post.suggestion else copies
        header = (f"FEED: @{post.handle} ({post.followers:,} followers) posted {time.time() - post.created:.0f}s "
                  f"ago, designed in {time.time() - t0:.0f}s\n\"{post.text[:220]}\"\n{post.url}\n{obvious}\n"
                  f"Launch posts memephil's own launch post.")
        if self.feed_auto_cap > 0 and post.copies == 0:  # first on this post: the main thread may launch it
            log.info("feed auto candidate %s / %s off %s, %.0fs after the post", c.name, c.symbol, post.url,
                     time.time() - post.created)
            self.auto_queue.put((p, post, header))
            return True
        log.info("feed proposal %s / %s off %s (%d copies), %.0fs after the post", c.name, c.symbol, post.url,
                 post.copies, time.time() - post.created)
        self.offer(p, header)
        return True

    def feed_auto(self) -> float:
        """SOL a day the feed may launch without a tap (/feed auto <SOL>, else FEED_AUTO_SOL_PER_DAY)."""
        v = self.ledger.get("feed_auto_sol")
        return float(v) if v is not None else self.cfg.feed_auto_sol_per_day

    def auto_tick(self) -> None:
        """Launch queued 0-copy feed designs if they are still first, fresh and within the caps;
        anything that no longer qualifies is offered with Launch/Skip instead."""
        while True:
            try:
                p, post, header = self.auto_queue.get_nowait()
            except queue.Empty:
                return
            now = time.time()
            self.auto_sent = [t for t in self.auto_sent if now - t < 3600]
            cap = self.feed_auto()
            why = ("auto is off" if cap <= 0 else
                   f"{post.copies} copies appeared" if post.copies else
                   f"the post is {now - post.created:.0f}s old" if now - post.created > self.cfg.feed_auto_max_age_s else
                   f"{len(self.auto_sent)} auto launches this hour" if len(self.auto_sent) >= self.cfg.feed_auto_max_per_hour else
                   f"today's auto budget: {self.ledger.spent_today(AUTO_SPEND):.4f} + {self.cost():.4f} > {cap:g} SOL"
                   if self.ledger.spent_today(AUTO_SPEND) + self.cost() > cap + 1e-12 else "")
            if why:
                log.info("feed auto: not launching %s (%s)", p.concept.symbol, why)
                self.offer(p, f"{header}\nNot auto-launched: {why}.")
                continue
            self.say(f"AUTO: launching ${p.concept.symbol} off @{post.handle}, {now - post.created:.0f}s after the "
                     f"post, no other coins on it yet")
            try:
                if self.launch(p, True):  # a refusal (floor, stop switch) doesn't use up the hour
                    self.auto_sent.append(now)
            except Exception as e:  # sent or not, SOL may be gone: count it
                self.auto_sent.append(now)
                log.error("auto launch failed: %s\n%s", e, traceback.format_exc())
                self.say(f"auto launch failed: {e}")

    def replies_on(self) -> bool:
        return self.cfg.x_replies_per_hour > 0 and self.ledger.get("replies", "1") == "1"

    def reply_draft(self, post: feed_mod.Post) -> bool:
        """Runs on the feed's reply thread: network only, no ledger. Sends memephil's replies to a fresh
        big-account post as "Reply on X" links (the API may not reply to posts that don't mention us).
        True once a draft was sent; a post the model passes on doesn't count toward X_REPLIES_PER_HOUR."""
        t0 = time.time()
        rs = content_mod.replies(self.cfg.cf_account_id, self.cfg.cf_api_token, self.cfg.llm_model, post.handle,
                                 post.followers, post.text, list(self.recent_replies),
                                 openrouter_key=self.cfg.openrouter_key,
                                 openrouter_models=list(self.cfg.openrouter_models))
        if not rs:
            log.info("reply: passed on %s", post.url)
            return False
        self.recent_replies = (self.recent_replies + rs)[-20:]
        rows = [[{"text": f"Reply {i + 1} on X", "url": social.reply_url(post.id, r)} for i, r in enumerate(rs)]]
        self.say(f"REPLY: @{post.handle} ({post.followers:,} followers) posted {time.time() - post.created:.0f}s ago\n"
                 f"\"{post.text[:280]}\"\n{post.url}\n\n" + "\n\n".join(f"{i + 1}. {r}" for i, r in enumerate(rs))
                 + "\n\nTap one: X opens with the reply written; post it from memephil.", {"inline_keyboard": rows})
        log.info("reply draft for %s, %.0fs after the post (%.1fs to write)", post.url, time.time() - post.created,
                 time.time() - t0)
        return True

    def feed_tick(self) -> None:
        """Keep the feed connected with the current j7tracker session while feed coins or replies are on;
        report a stop once."""
        self.feed_coins = self.ledger.get("feed", "1") == "1"
        replying = self.replies_on() and not quiet(time.time(), self.cfg.content_quiet_utc)
        if self.feed:
            self.feed.replying = replying
        if not (self.feed_coins or self.replies_on()):
            if self.feed:
                self.feed.stop()
                self.feed = None
            return
        self.feed_avoid = self.ledger.used_names()
        self.feed_auto_cap = self.feed_auto()
        try:
            token = self.j7_session()
        except RuntimeError as e:
            if self.feed:
                self.feed.stop()
                self.feed = None
            if str(e) != self.feed_error:
                self.say(f"feed paused: {e}")
            self.feed_error = str(e)
            return
        if self.feed and self.feed.alive and self.feed.token == token:
            return
        if self.feed and not self.feed.alive and self.feed.error and self.feed.error != self.feed_error:
            self.say(f"feed stopped: {self.feed.error}")
            self.feed_error = self.feed.error
        if self.feed:
            self.feed.stop()
        self.feed = feed_mod.Feed(token, self.design, min_followers=self.cfg.feed_min_followers,
                                  max_age_s=self.cfg.feed_max_age_s, max_per_hour=self.cfg.feed_max_per_hour,
                                  reply=self.reply_draft if self.cfg.x_replies_per_hour > 0 else None,
                                  reply_min_followers=self.cfg.x_reply_min_followers,
                                  reply_max_age_s=self.cfg.x_reply_max_age_s,
                                  reply_max_per_hour=self.cfg.x_replies_per_hour)
        self.feed.replying = replying
        self.feed.start()
        if self.feed_error:
            self.say("feed connected.")
            self.feed_error = ""

    def art(self, c: concept_mod.Concept) -> tuple[bytes, str]:
        """A live variant edits the source coin's own picture (c.twist); anything else, or a failed
        edit, gets fresh art. Returns (image, what the art is) for the proposal caption."""
        src = c.narrative.examples[0] if c.narrative.live and c.narrative.examples else None
        note = "fresh art"
        if src and c.twist:
            try:
                return (image.variant(self.cfg.cf_account_id, self.cfg.cf_api_token, src.image, c.twist,
                                      pollinations_token=self.cfg.pollinations_token),
                        f"${src.symbol}'s picture, edited: {c.twist}")
            except Exception as e:
                log.warning("variant art for %s failed: %s", c.symbol, e)
                note = f"fresh art (editing ${src.symbol}'s picture failed: {str(e)[:120]})"
        return image.generate(self.cfg.cf_account_id, self.cfg.cf_api_token, c.image_prompt,
                              fallback=self.cfg.image_fallback, pollinations_token=self.cfg.pollinations_token), note

    def cost(self) -> float:
        base = (self.pad.launch_cost_sol if self.pad else j7.LAUNCH_COST_SOL if self.cfg.launcher == "j7"
                else pump.LAUNCH_COST_SOL + (pump.MAYHEM_EXTRA_SOL if self.cfg.mayhem else 0))
        return base + self.dev_buy() + self.cfg.priority_fee_sol

    def verdict(self):
        return check_launch(
            stopped=self.ledger.stopped, live=self.cfg.live,
            launches_today=self.ledger.launches_today(), max_launches=self.cfg.max_launches_per_day,
            spent_today=self.ledger.spent_today(), max_sol_per_day=self.cfg.max_sol_per_day,
            cost=self.cost(), balance=self.balance(),  # before baseline=: may record it
            baseline=self.ledger.baseline, floor_pct=self.cfg.floor_pct)

    def caption(self, p: Proposal, header: str) -> str:
        c = p.concept
        return (f"{header}\n\n{c.name} (${c.symbol})\n{c.description}\n\n"
                f"Story: {c.story or '-'}\nAngle: {c.angle}\nWhy now: {c.why}\n"
                + (f"Art: {p.art_note}\n" if p.art_note else "")
                + f"\nEvidence: {c.narrative.evidence()}")

    def discard(self, p: Proposal) -> None:
        """Delete the art of a proposal that will never launch (skipped or expired)."""
        if p.image_uri:
            try:
                pump.unpin(self.cfg.pinata_jwt, pump.cid_of(p.image_uri))
            except Exception as e:
                log.warning("unpin %s: %s", p.image_uri, e)

    def launch(self, p: Proposal, auto: bool = False) -> bool:
        """False when refused before anything was posted or spent."""
        clicked = time.time()
        v = self.verdict()
        if not v.ok:
            log.info("not launching %s: %s", p.concept.symbol, v.reason)
            self.say(f"Not launching ${p.concept.symbol}: {v.reason}")
            return False
        if self.cfg.launcher == "j7" and not self.pad:
            try:
                self.j7_session()
            except RuntimeError as e:  # before anything is posted or spent
                self.say(f"Not launching ${p.concept.symbol}: {e}")
                return False
        c = p.concept
        creator = self.wallets.new() if self.cfg.rotate_wallets and not self.pad else self.dev
        description = c.description
        if self.cfg.disclosure:
            description += "\n\n" + self.cfg.disclosure.format(dev_buy=self.dev_buy(), wallet=creator.pubkey())
        # Counted before sending: a send that errors after broadcasting still spent the SOL.
        self.ledger.record_spend(self.cost(), f"{AUTO_SPEND if auto else ''}launch {c.symbol}")
        mint = p.mint
        if creator is not self.dev:  # before the X post: a failed transfer leaves nothing to clean up
            wallets_mod.fund(self.rpc, self.dev, creator.pubkey(),
                             self.cost() + self.dev_buy() * 0.02 + FUND_MARGIN_SOL)
            self.ledger.put(f"creator:{mint.pubkey()}", str(creator.pubkey()))
        website = f"{self.cfg.website_base}/{mint.pubkey()}" if self.cfg.website_base and not self.pad else ""  # a pad picks its own mint
        post_id = self.announce(c, str(mint.pubkey()), p.image, p.quote_id)
        posted = time.time()
        try:
            sending = time.time()
            if self.pad:  # the launchpad hosts its own metadata and returns the mint it chose
                uri = ""
                out = self.pad.launch(self.rpc, self.dev, name=c.name, symbol=c.symbol, description=description,
                                      image=p.image, website=website,
                                      twitter=self.twitter_link(post_id),
                                      dev_buy_sol=self.dev_buy())
            elif self.cfg.launcher == "j7":  # j7tracker hosts the metadata and signs with its copy of the key
                uri = ""
                if p.image_uri:  # pinned at proposal time; recorded so housekeeping frees it once the coin dies
                    self.ledger.record_pins(str(mint.pubkey()), [pump.cid_of(p.image_uri)])
                sending = time.time()
                out = j7.deploy(self.rpc, j7.BASES[self.cfg.j7_region], self.j7_session(), self.cfg.j7_wallet_key,
                                mint, name=c.name, symbol=c.symbol, description=description, image=p.image,
                                website=website, twitter=self.twitter_link(post_id),
                                dev_buy_sol=self.dev_buy())
            else:
                image_uri = p.image_uri or pump.pin_image(self.cfg.pinata_jwt, c.symbol, p.image)
                art_pinned = time.time()
                uri = pump.pin_metadata(self.cfg.pinata_jwt, name=c.name, symbol=c.symbol, description=description,
                                        image_uri=image_uri, website=website,
                                        twitter=self.twitter_link(post_id))
                self.ledger.record_pins(str(mint.pubkey()), [pump.cid_of(image_uri), pump.cid_of(uri)])
                sending = time.time()
                log.info("%s: X post %.1fs, art %s %.1fs, metadata %.1fs", c.symbol, posted - clicked,
                         "already pinned" if p.image_uri else "pinned", art_pinned - posted, sending - art_pinned)
                out = pump.create(self.rpc, creator, mint, name=c.name, symbol=c.symbol, uri=uri,
                                  dev_buy_sol=self.dev_buy(), slippage_pct=self.cfg.slippage_pct,
                                  priority_fee_sol=self.cfg.priority_fee_sol, mayhem=self.cfg.mayhem,
                                  tries=CREATE_TRIES, wait_s=CREATE_RESEND_S)
        except Exception:
            if post_id:  # never leave a post up for a coin that doesn't exist
                try:
                    social.delete(self.cfg.x_keys, post_id)
                except Exception as e:
                    self.say(f"could not delete X post {social.post_url(post_id)} for the failed launch: {e}")
            raise
        top = c.narrative.examples[0] if c.narrative.examples else None
        self.ledger.record_launch(mint=out.mint, name=c.name, symbol=c.symbol,
                                  narrative=f"{'/'.join(c.narrative.terms)} ({c.angle})",
                                  source_mint=p.source_url or (top.mint if top else ""),
                                  source_name=p.source or (top.name if top else ""),
                                  source_symbol=CLONE_SOURCE if p.clone else FEED_SOURCE if p.quote_id
                                  else top.symbol if top else "",
                                  uri=uri, signature=out.signature,
                                  confirmed=out.confirmed, dev_buy_sol=self.dev_buy())
        if self.pad:
            self.ledger.record_pad_mint(self.pad.name, out.mint)
        sell_rows = ({"inline_keyboard": [[{"text": f"Sell {x}%", "callback_data": f"sl:{out.mint}.{x}"}
                                          for x in SELL_PCTS]]} if self.dev_buy() > 0 and not self.pad else None)
        state = ("AUTO " if auto else "") + ("LIVE" if out.confirmed else "SENT (not yet confirmed)")
        log.info("%s %s: sent at +%.1fs, confirmed at +%.1fs after the %s", state, c.symbol, sending - clicked,
                 time.time() - clicked, "decision" if auto else "click")
        source = f"; ${top.symbol} was {(clicked - top.created) / 60:.1f} min old at the click" if top else ""
        self.say(f"{state}: {c.name} (${c.symbol})\n{self.pad.coin_url(out.mint) if self.pad else 'https://pump.fun/coin/' + out.mint}\n"
                 f"https://solscan.io/tx/{out.signature}\n"
                 + (f"X: {social.post_url(post_id)}\n" if post_id else "")
                 + f"timing: sent {sending - clicked:.1f}s after your click, confirmed at {time.time() - clicked:.1f}s"
                 + source
                 + (f"\ncreator wallet {creator.pubkey()} (fresh)" if creator is not self.dev else "")
                 + (f"\ndev buy {self.dev_buy():g} SOL: sell the dev wallet's tokens with the buttons" if sell_rows else ""),
                 sell_rows)
        if self.cfg.autosell_pct and sell_rows and out.confirmed:
            self.watch_snipers(out.mint, c.symbol)
        return True

    def watch_snipers(self, mint: str, symbol: str) -> None:
        """AUTOSELL_PCT: sell part of the dev buy into the first outside buying. Snipers land in or right
        after the launch block and are gone by ~30 s ($gctrl), so this polls the curve's real SOL fast;
        anything above our own dev buy came from someone else."""
        dev_buy, pct = self.dev_buy(), self.cfg.autosell_pct
        deadline = time.monotonic() + self.cfg.autosell_watch_s
        while time.monotonic() < deadline:
            try:
                real = pump.curve_real_sol(self.rpc, mint)
            except (http.HttpError, RuntimeError, OSError) as e:
                log.warning("curve read for %s: %s", symbol, e)
                real = None
            if real is not None and real - dev_buy >= self.cfg.autosell_min_sol:
                self.say(f"${symbol}: {real - dev_buy:.3f} SOL of outside buying on the curve, auto-selling {pct}% "
                         "of the dev tokens")
                self.sell(mint, pct)
                return
            time.sleep(AUTOSELL_POLL_S)
        self.say(f"${symbol}: under {self.cfg.autosell_min_sol:g} SOL of outside buying in "
                 f"{self.cfg.autosell_watch_s:g}s; no auto-sell, the dev tokens are kept")

    def creator_of(self, mint: str) -> str:
        return self.ledger.get(f"creator:{mint}") or self.wallet

    def creator_key(self, mint: str) -> Keypair:
        """The key that created `mint`: its rotated wallet, else the main dev wallet."""
        w = self.ledger.get(f"creator:{mint}")
        return self.wallets.get(w) if w and self.wallets else self.dev

    def dev_buy(self) -> float:
        """SOL the dev wallet buys of each new coin: /devbuy's value, else DEV_BUY_SOL."""
        v = self.ledger.get("dev_buy_sol")
        return float(v) if v is not None else self.cfg.dev_buy_sol

    def sell(self, ref: str, pct: int) -> None:
        """Sell pct% of the dev wallet's tokens of one of our coins (a Sell button, or /sell)."""
        found = self.ledger.launched(ref)
        if found is None:
            self.say(f"{ref} isn't one of our launches.")
            return
        mint, symbol = found
        if self.pad:
            self.say(f"Selling is only wired for pump.fun coins (LAUNCHPAD is {self.pad.name}).")
            return
        last = self.recent_sells.get(mint, 0.0)
        if time.time() - last < SELL_DEBOUNCE_S:  # a double tap would sell twice from what's left
            self.say(f"Already selling ${symbol}; wait a few seconds and check the result first.")
            return
        self.recent_sells[mint] = time.time()
        self.say(f"Selling {pct}% of the dev wallet's ${symbol}...")
        key, wallet = self.creator_key(mint), self.creator_of(mint)
        try:
            if key is not self.dev:  # a rotated wallet is swept to 0 when idle: give it the fee first
                wallets_mod.top_up(self.rpc, self.dev, key)
            before = self.rpc.balance_sol(wallet)
            out = pump.sell(self.rpc, key, mint, pct, slippage_pct=self.cfg.slippage_pct,
                            priority_fee_sol=self.cfg.priority_fee_sol)
        except (http.HttpError, RuntimeError, OSError) as e:
            self.say(f"Sell of ${symbol} failed: {str(e)[:300]}")
            return
        after = self.rpc.balance_sol(wallet)
        if out.confirmed:
            self.ledger.record_sale(mint, pct, out.signature, after - before)
        self.say(f"{'Sold' if out.confirmed else 'Sell SENT, not confirmed yet'}: {pct}% of ${symbol}, wallet "
                 f"{before:.4f} -> {after:.4f} SOL ({after - before:+.4f})\nhttps://solscan.io/tx/{out.signature}")

    def twitter_link(self, post_id: str) -> str:
        """The coin's X link: memephil's launch post, or the account itself (X_HANDLE) when the post failed."""
        if post_id:
            return social.post_url(post_id)
        return f"https://x.com/{self.cfg.x_handle}" if self.cfg.x_handle else ""

    def j7_session(self) -> str:
        """The stored j7tracker access token, if it has at least two minutes left."""
        cached = json.loads(self.ledger.get("j7_access", "{}") or "{}")
        if not cached.get("token"):
            raise RuntimeError("no j7tracker session yet: send the bot /j7 <__Secure-j7_at cookie value>")
        if cached.get("expires") and cached["expires"] - time.time() < 120:
            raise RuntimeError("the j7tracker session expired: send the bot /j7 <new __Secure-j7_at cookie value>")
        return cached["token"]

    def set_j7_session(self, msg: dict, token: str) -> None:
        """Take a j7tracker access token (the __Secure-j7_at cookie), check it with j7tracker, keep it,
        and delete the message that carried it."""
        try:
            self.tg("deleteMessage", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"]})
        except Exception as e:
            log.warning("could not delete the /j7 message: %s", e)
        try:
            expires = j7.expiry(token)
            info = j7.session_check(token)
        except j7.J7Error as e:
            self.say(f"Not saved: {e}")
            return
        self.ledger.put("j7_access", json.dumps({"token": token, "expires": expires}))
        until = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(expires)) if expires else "unknown"
        self.say(f"j7tracker session saved for {info.get('username') or 'your account'}, good until {until}. "
                 f"Launches go through j7tracker while LAUNCHER=j7 (now: {self.cfg.launcher}).")

    def announce(self, c: concept_mod.Concept, mint: str, art: bytes, quote_id: str = "") -> str:
        """Post the launch, with the coin's art, from the dev's X account (before the coin exists, so
        the post can be the coin's `twitter` link). Returns the post id, or "" when X is off, capped,
        or failing: a failed post never blocks a launch, and a failed image upload posts text only."""
        if not self.cfg.x_keys:
            return ""
        if self.ledger.count_today("x_post") >= self.cfg.x_max_posts_per_day:
            return ""
        disclosure = ""  # operator's call (2026-10-13): the post carries the coin only
        try:
            media_id = social.upload_image(self.cfg.x_keys, art, *image.kind(art))
        except Exception as e:
            log.warning("X image upload failed, posting text only: %s", e)
            media_id = ""
        ca = mint
        try:
            for _ in range(3):
                try:
                    post_id = social.post(self.cfg.x_keys, social.launch_text(c.name, c.symbol, c.description, ca,
                                                                              disclosure), media_id, quote_id=quote_id)
                    break
                except Exception as e:
                    if quote_id and social.quote_blocked(e):
                        quote_id = ""  # the API may only quote posts that mention us: post it plain
                    elif ca and social.address_blocked(e):
                        ca = ""        # new-account rule: no CA; the coin still links to this post
                    else:
                        raise
            else:
                raise RuntimeError("X refused the launch post three times")
        except Exception as e:
            log.warning("X post failed: %s", e)
            if str(e)[:80] != self.x_error:
                self.say(f"X post failed (launching without it; reported once): {e}")
            self.x_error = str(e)[:80]
            return ""
        self.x_error = ""
        self.ledger.count_event("x_post")
        return post_id

    # ---- X content between launches -------------------------------------------
    def x_image(self, art: bytes) -> str:
        try:
            return social.upload_image(self.cfg.x_keys, art, *image.kind(art))
        except Exception as e:
            log.warning("X image upload failed, posting text only: %s", e)
            return ""

    def topics(self) -> list[content_mod.Topic]:
        """What people are on right now, minus topics drafted in the last TOPIC_COOLDOWN_S."""
        now = time.time()
        used = {concept_mod._norm(r[1]) for r in json.loads(self.ledger.get("content_topics", "[]"))
                if now - r[0] < TOPIC_COOLDOWN_S}
        found: list[content_mod.Topic] = []
        try:
            found += content_mod.trending()[:10]
        except Exception as e:  # one source down still leaves the others
            log.warning("google trends: %s", e)
        try:
            ns = self.trends.narratives(limit=6)
            found += content_mod.from_narratives(ns, {n.terms[0]: self.trends.post_texts(n) for n in ns})
        except Exception as e:
            log.warning("pump.fun topics: %s", e)
        return [t for t in found if concept_mod._norm(t.title) not in used] + [content_mod.EVERGREEN]

    def draft_art(self, d: content_mod.Draft) -> bytes | None:
        """AI-ify the topic coin's own picture, or draw fresh art; None for a text post or when
        every image service fails (the draft is still worth sending)."""
        try:
            if d.image_mode == "edit":
                info = pump.coin_info(d.topic.mint) or {}
                try:
                    return image.variant(self.cfg.cf_account_id, self.cfg.cf_api_token, info.get("image_uri") or "",
                                         d.image_instruction, pollinations_token=self.cfg.pollinations_token)
                except Exception as e:
                    log.warning("draft art edit failed, drawing fresh: %s", e)
            if d.image_mode in ("edit", "new"):
                return image.generate(self.cfg.cf_account_id, self.cfg.cf_api_token, d.image_instruction,
                                      fallback=self.cfg.image_fallback, pollinations_token=self.cfg.pollinations_token)
        except Exception as e:
            log.warning("draft art failed: %s", e)
        return None

    def draft_post(self) -> None:
        """Draft three AI-ified takes on a live meme and send them to Telegram to pick from."""
        if not self.cfg.x_keys:
            raise RuntimeError("X is off: set the four X_* keys")
        recent = json.loads(self.ledger.get("content_topics", "[]"))
        last: Exception | None = None
        topics = self.topics()
        for _ in range(3):
            try:
                d = content_mod.draft(self.cfg.cf_account_id, self.cfg.cf_api_token, self.cfg.llm_model,
                                      topics, json.loads(self.ledger.get("posted_texts", "[]")),
                                      openrouter_key=self.cfg.openrouter_key,
                                      openrouter_models=list(self.cfg.openrouter_models))
                break
            except concept_mod.ConceptError as e:
                last = e
        else:
            raise RuntimeError(f"no usable draft after 3 tries: {last}")
        now = time.time()
        self.ledger.put("content_topics", json.dumps([r for r in recent if now - r[0] < TOPIC_COOLDOWN_S]
                                                     + [[now, d.topic.title]]))
        art = self.draft_art(d)
        did = secrets.token_hex(4)
        self.drafts[did] = Drafted(d, art)
        rows = [[{"text": f"Post {i + 1}", "callback_data": f"dp:{did}.{i}"} for i in range(len(d.jokes))]]
        if art:
            rows.append([{"text": f"{i + 1} without image", "callback_data": f"dt:{did}.{i}"}
                         for i in range(len(d.jokes))])
        rows.append([{"text": "Skip", "callback_data": f"ds:{did}"}])
        ctx = f"\n{d.topic.context[0][:160]}" if d.topic.context else ""
        head = f"X draft ({d.topic.source}: {d.topic.title[:80]}){ctx}"
        text = (f"{head}\n\n"
                + "\n\n".join(f"{i + 1}. {j}" for i, j in enumerate(d.jokes))
                + (f"\n\nimage: {d.image_mode}, {d.image_instruction[:150]}" if art else ""))
        if art:
            self.photo(art, text, {"inline_keyboard": rows})
        else:
            self.say(text, {"inline_keyboard": rows})

    def post_draft(self, did: str, i: int, with_image: bool) -> None:
        dd = self.drafts.pop(did, None)
        if dd is None or time.time() - dd.created > DRAFT_TTL_S or not 0 <= i < len(dd.draft.jokes):
            self.say("That draft expired or is from before a restart; /post for a fresh one.")
            return
        if self.ledger.count_today("x_post") >= self.cfg.x_max_posts_per_day:
            self.drafts[did] = dd  # keep it for tomorrow
            self.say(f"X post cap reached today ({self.cfg.x_max_posts_per_day}); try again after 00:00 UTC.")
            return
        text = dd.draft.jokes[i]
        post_id = social.post(self.cfg.x_keys, text, self.x_image(dd.image) if with_image and dd.image else "")
        self.ledger.count_event("x_post")
        self.ledger.count_event("x_content")
        self.ledger.put("posted_texts", json.dumps((json.loads(self.ledger.get("posted_texts", "[]")) + [text])[-30:]))
        self.say(f"Posted: {social.post_url(post_id)}",
                 {"inline_keyboard": [[{"text": "Delete it", "callback_data": f"dx:{post_id}"}]]})

    def schedule_content(self, now: float) -> float:
        """Next scheduled post: the day's posts spread over the hours outside the quiet window, with
        jitter so they don't land on the minute. Stored, so a restart doesn't post right away."""
        start, end = self.cfg.content_quiet_utc
        active_h = (24 - (end - start) % 24) or 24
        t = now + active_h * 3600 / self.cfg.x_drafts_per_day * random.uniform(0.75, 1.25)
        if quiet(t, (start, end)):
            day = t - (t % 86400) + end * 3600
            t = (day if day > t else day + 86400) + random.uniform(0, 1800)
        self.ledger.put("next_content_ts", str(t))
        return t

    def content_tick(self, now: float) -> None:
        if not (self.cfg.x_drafts_per_day and self.cfg.x_keys) or self.ledger.get("content", "1") != "1":
            return
        due = float(self.ledger.get("next_content_ts", "0") or 0)
        if not due:
            self.schedule_content(now)
            return
        if now < due:
            return
        self.schedule_content(now)
        try:
            self.draft_post()
        except Exception as e:
            log.error("X draft failed: %s\n%s", e, traceback.format_exc())
            if str(e)[:120] != self.content_error:
                self.say(f"scheduled X draft failed (reported once until it changes): {e}")
            self.content_error = str(e)[:120]
            return
        self.content_error = ""

    def auto_cycle(self) -> None:
        """One auto cycle. At SCAN_MINUTES=1 a lasting failure (e.g. the Workers AI daily allocation
        running out) would otherwise post the same error every minute: report each distinct error
        once, and say when cycles work again."""
        try:
            self.cycle()
        except Exception as e:
            log.error("cycle failed: %s\n%s", e, traceback.format_exc())
            if str(e)[:120] != self.cycle_error:
                self.say(f"auto cycle failed (reported once until it changes): {e}")
            self.cycle_error = str(e)[:120]
            return
        if self.cycle_error:
            self.say("auto cycles are working again.")
            self.cycle_error = ""

    def cycle(self) -> None:
        if self.cfg.live and not self.ledger.stopped:
            v = self.verdict()  # check caps first: don't design (and post) a coin that can't launch
            if not v.ok:
                if v.reason.split(":")[0] != self.auto_skip:
                    self.say(f"auto: waiting ({v.reason}).")
                self.auto_skip = v.reason.split(":")[0]
                return
            self.auto_skip = ""
        p = self.propose()
        if self.cfg.live and not self.ledger.stopped:
            self.photo(p.image, self.caption(p, "AUTO launch"))
            self.launch(p)
        else:
            self.photo(p.image, self.caption(p, "DRY RUN (LIVE=0): would launch"))

    # ---- commands -------------------------------------------------------------
    def offer(self, p: Proposal | None = None, header: str = "Proposal") -> None:
        p = p or self.propose()
        pid = secrets.token_hex(4)
        self.proposals[pid] = p
        buttons = {"inline_keyboard": [[{"text": "Launch", "callback_data": f"go:{pid}"},
                                        {"text": "Skip", "callback_data": f"no:{pid}"}]]}
        header = header if self.cfg.live else f"{header} (DRY RUN: Launch will be refused)"
        self.photo(p.image, self.caption(p, header), buttons)

    def status(self) -> str:
        bal = self.balance()
        base = self.ledger.baseline or 0
        return (f"mode: {'LIVE' if self.cfg.live else 'DRY RUN'} | auto: {'on' if self.ledger.auto else 'off'}"
                f" | {'STOPPED' if self.ledger.stopped else 'armed' if self.cfg.live else 'not armed'}\n"
                f"wallet: {self.wallet}\nbalance: {bal:.4f} SOL (floor {base * self.cfg.floor_pct / 100:.4f}"
                f" = {self.cfg.floor_pct:g}% of {base:.4f})\n"
                f"unclaimed creator fees: {self.unclaimed():.6f} SOL (/claim)\n"
                + (f"rotated creator wallets: {len(self.wallets.pubkeys())}"
                   f"{' (new one per launch)' if self.cfg.rotate_wallets else ' (rotation off)'}\n" if self.wallets else "")
                + f"today: {self.ledger.launches_today()}"
                f"{'/' + str(self.cfg.max_launches_per_day) if self.cfg.max_launches_per_day else ' (no count cap)'}"
                f" launches, "
                f"{self.ledger.spent_today():.4f}"
                f"{'/' + format(self.cfg.max_sol_per_day, '.4f') if self.cfg.max_sol_per_day != float('inf') else ' (no daily cap)'}"
                f" SOL\n"
                f"per launch: ~{self.cost():.4f} SOL (dev buy {self.dev_buy():g}"
                f"{', Mayhem mode' if self.cfg.mayhem and not self.pad else ''})\n"
                f"replies: {'on' if self.replies_on() else 'off'}"
                f"{' (' + str(len(self.feed.replied)) + ' this hour)' if self.replies_on() and self.feed and self.feed.alive else ''}\n"
                f"feed auto-launch: {format(self.feed_auto(), 'g') + ' SOL/day, spent ' + format(self.ledger.spent_today(AUTO_SPEND), '.4f') if self.feed_auto() > 0 else 'off'}\n"
                f"next cycle in {max(0, int(self.next_cycle - time.time()))}s")

    def replies_status(self) -> str:
        if self.cfg.x_replies_per_hour <= 0:
            return "replies off: X_REPLIES_PER_HOUR is 0"
        f, start_end = self.feed, self.cfg.content_quiet_utc
        if not self.replies_on():
            return "replies off (/replies on)"
        return (f"replies on: up to {self.cfg.x_replies_per_hour} drafts an hour under fresh posts (under "
                f"{self.cfg.x_reply_max_age_s / 60:g} min old) from accounts with {self.cfg.x_reply_min_followers:,}+ "
                f"followers, none during {start_end[0]}-{start_end[1]} UTC"
                + (f"; {len(f.replied)} sent and {len(f.asked)} posts read this hour" if f and f.alive else
                   f"; feed not connected{' (' + self.feed_error + ')' if self.feed_error else ''}")
                + ("; quiet hours now" if quiet(time.time(), start_end) else ""))

    def humans(self, n: int = 5) -> str:
        """Each of the last n coins' buyers, then a scorecard: feed coins (off someone's X post) against
        every other launch, on humans and on buying after the bots left."""
        lines = []
        groups: dict[str, list[audience.Audience]] = {"feed": [], "other": []}
        for mint, ts, _name, symbol, src, confirmed in self.ledger.launches(n):
            if not confirmed:
                continue
            kind = "feed" if src == FEED_SOURCE else "other"
            try:
                a = audience.audience(self.rpc, mint, self.creator_of(mint), ts, self.wallet_kinds)
            except Exception as e:
                lines.append(f"${symbol}: unavailable ({str(e)[:60]})")
                continue
            groups[kind].append(a)
            lines.append(f"${symbol}{' [feed]' if kind == 'feed' else ''} ({(time.time() - ts) / 3600:.1f}h): {a.line()}")
        if not lines:
            return "No confirmed launches yet."
        card = []
        for kind, label in (("feed", "feed coins"), ("other", "other coins")):
            g = groups[kind]
            if g:
                buyers = sum(sum(a.counts.values()) for a in g) / len(g)
                humans = sum(a.counts["human"] for a in g) / len(g)
                late = sum(1 for a in g if a.late_buys)
                card.append(f"{label}: {len(g)} coins, {buyers:.1f} buyers and {humans:.1f} humans per coin, "
                            f"{late} with buying after {audience.LATE_S}s")
        return "\n".join(lines) + "\n\nscorecard (last " + str(n) + "):\n" + "\n".join(card)

    def stats(self) -> str:
        rows = self.ledger.launches()
        if not rows:
            return "No launches yet."
        live = scanner.token_stats([r[0] for r in rows])
        t = self.ledger.totals()
        lines, total_vol = [], 0.0
        for mint, ts, _name, symbol, src, confirmed in rows:
            s = live.get(mint)
            vol = s["vol_h24"] if s else 0.0
            total_vol += vol
            age_h = (time.time() - ts) / 3600
            lines.append(f"${symbol} ({age_h:.0f}h, from ${src}): 24h vol ${vol:,.0f}"
                         f" -> fees ~${vol * CREATOR_FEE_RATE:,.2f}{'' if confirmed else ' [unconfirmed]'}")
        unclaimed = self.unclaimed()
        earned = t["claimed_sol"] + unclaimed + t["sold_sol"]
        per = f", {(earned - t['spent_sol']) / t['launches']:+.4f} per launch" if t["launches"] else ""
        sold = f"dev sells returned {t['sold_sol']:.4f} SOL\n" if t["sold_sol"] else ""
        return ("\n".join(lines) + f"\n\n{t['launches']} launches, spent {t['spent_sol']:.4f} SOL (incl. dev buys)\n"
                f"fees: claimed {t['claimed_sol']:.4f} + unclaimed {unclaimed:.4f} SOL\n{sold}"
                f"net {earned - t['spent_sol']:+.4f} SOL{per}\n"
                f"est. 24h creator fees ~${total_vol * CREATOR_FEE_RATE:,.2f}")

    # ---- housekeeping ---------------------------------------------------------
    def housekeep(self) -> None:
        """Auto-claim, then free Pinata files of dead coins. A failure is reported once per
        distinct error rather than every 10 minutes."""
        try:
            if self.cfg.live:
                self.auto_claim()
                self.sweep_idle()
            if self.cfg.pinata_jwt:
                self.clean_pins()
            self.housekeep_error = ""
        except Exception as e:
            log.error("housekeeping: %s\n%s", e, traceback.format_exc())
            if str(e) != self.housekeep_error:
                self.say(f"housekeeping failed: {e}")
            self.housekeep_error = str(e)

    def unclaimed(self) -> float:
        if self.pad:
            return self.pad.unclaimed_sol(self.dev)
        return pump.unclaimed_fees_sol(self.rpc, self.dev.pubkey()) + sum(self.rotated_unclaimed().values())

    def rotated_unclaimed(self) -> dict[str, float]:
        """Unclaimed creator fees per rotated wallet: one vault each, read 100 per RPC call."""
        if not self.wallets:
            return {}
        vaults = {str(pump.creator_vault(Pubkey.from_string(w))): w for w in self.wallets.pubkeys()}
        return {vaults[v]: max(0, lam - pump.VAULT_RENT_LAMPORTS) / pump.LAMPORTS
                for v, lam in wallets_mod.lamports_of(self.rpc, list(vaults)).items()}

    def sweep_idle(self) -> None:
        """Move SOL left in rotated wallets (create margin, sell proceeds) back to the dev wallet."""
        if not self.wallets:
            return
        now = time.time()
        idle = [w for w in self.wallets.pubkeys() if self.wallets.age_s(w, now) > SWEEP_AFTER_S]
        moved = 0.0
        for w, lam in wallets_mod.lamports_of(self.rpc, idle).items():
            if lam > 0:
                out = wallets_mod.sweep(self.rpc, self.dev, self.wallets.get(w))
                moved += out[0] if out else 0.0
        if moved:
            log.info("swept %.6f SOL from rotated wallets", moved)

    def auto_claim(self) -> None:
        pending = self.unclaimed()
        if pending < self.cfg.claim_min_sol:
            return
        msg = self.claim()
        bal = self.rpc.balance_sol(self.wallet)
        if self.ledger.raise_baseline(bal):  # ratchet up only: the floor never follows a loss down
            msg += f"\nfloor raised: baseline {bal:.4f}, floor {bal * self.cfg.floor_pct / 100:.4f} SOL"
        self.say("auto-claim: " + msg)

    def clean_pins(self, batch: int = 60) -> None:
        freed, coins = 0, []
        candidates = self.ledger.uncleaned(older_than_s=pump.DEAD_IDLE_S)
        random.shuffle(candidates)  # coins people still hold stay uncleaned; never let them block the batch
        for mint, symbol, uri in candidates[:batch]:
            info = pump.coin_info(mint)
            if info is None or not pump.is_dead(info):
                continue
            cids = self.ledger.pins(mint)
            if not cids:  # launched before CIDs were recorded: read the image CID from the metadata
                cids = [pump.cid_of(uri)]
                try:
                    cids.append(pump.cid_of(http.get_json(pump.GATEWAY + cids[0], timeout=20)["image"]))
                except (http.HttpError, OSError, ValueError, KeyError):
                    pass
            n = sum(pump.unpin(self.cfg.pinata_jwt, cid) for cid in cids)
            self.ledger.mark_cleaned(mint, n)
            freed += n
            coins.append(f"${symbol}")
        if coins:
            self.say(f"Pinata: deleted {freed} files of dead coins {', '.join(coins)}")

    def claim(self) -> str:
        pending = self.unclaimed()
        if pending <= 0:
            return "No creator fees waiting to be claimed."
        self.say(f"Claiming {pending:.6f} SOL of creator fees...")
        before = self.rpc.balance_sol(self.wallet)
        if self.pad:
            sigs = [self.pad.claim(self.rpc, self.dev)]
        else:
            sigs = []
            if pump.unclaimed_fees_sol(self.rpc, self.dev.pubkey()) > 0:
                sigs.append(pump.claim_creator_fees(self.rpc, self.dev, self.cfg.priority_fee_sol))
            for w, owed in self.rotated_unclaimed().items():
                if owed < ROTATED_CLAIM_MIN_SOL:
                    continue
                key = self.wallets.get(w)
                wallets_mod.top_up(self.rpc, self.dev, key)
                sigs.append(pump.claim_creator_fees(self.rpc, key, self.cfg.priority_fee_sol))
                wallets_mod.sweep(self.rpc, self.dev, key)
            if not sigs:
                return (f"{pending:.6f} SOL of creator fees is waiting, spread over rotated wallets with under "
                        f"{ROTATED_CLAIM_MIN_SOL:g} SOL each: not worth the tx fees yet.")
        after = self.rpc.balance_sol(self.wallet)
        self.ledger.record_claim(",".join(sigs), before, after)
        return (f"Claimed: wallet {before:.6f} -> {after:.6f} SOL ({after - before:+.6f}, net of tx fees)\n"
                + "\n".join(f"https://solscan.io/tx/{sig}" for sig in sigs))

    def command(self, text: str) -> None:
        cmd, _, arg = text.strip().partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            self.say(HELP)
        elif cmd == "/coin":
            if not arg.strip():
                self.say("Tell me the idea: /coin <what you saw and why it's funny>")
            else:
                self.say("Designing a coin from your idea...")
                self.offer(self.idea(arg))
        elif cmd == "/clone":
            self.say("Copying...")
            if arg.strip():
                src, copies, buyers = trends_mod.fetch_coin(arg.strip()), 0, 0
            else:
                n = self.live_narratives()[0]
                src, copies, buyers = n.examples[0], n.launches_1h - 1, int(n.score)
            self.offer(self.clone(src, copies, buyers), "CLONE")
        elif cmd == "/pvp":
            mode = arg.strip().lower()
            if mode in ("off", "ask", "auto"):
                self.ledger.put("pvp", mode)
                self.next_pvp = 0
            mode = self.ledger.get("pvp", "off")
            self.say(f"pvp {mode}: clones a coin once it has {self.cfg.pvp_min_buyers}+ outside "
                     f"buyers within {self.cfg.fresh_minutes:g} min of launch, skips ones with more than "
                     f"{self.cfg.pvp_max_copies} copies out, checks every {self.cfg.pvp_poll_s:g}s"
                     + (" (dry run: auto only previews)" if mode == "auto" and not self.cfg.live else ""))
        elif cmd == "/propose":
            self.say("Scanning and designing...")
            self.offer()
        elif cmd in ("/trends", "/scan"):
            ns, checked = trends_mod.hot_now(self.cfg.fresh_minutes, self.cfg.hot_min_buyers)
            self.say(f"Hot in the last {self.cfg.fresh_minutes:g} min ({self.cfg.hot_min_buyers}+ buyers):\n\n"
                     + ("\n\n".join(f"{i + 1}. {n.evidence()}" for i, n in enumerate(ns)) or "nothing yet")
                     + "\n\nChecked: " + ("; ".join(checked) or "no fresh coins"))
        elif cmd == "/auto":
            on = arg.strip().lower() == "on"
            self.ledger.put("auto", "1" if on else "0")
            self.next_cycle = time.time() + 5 if on else self.next_cycle
            self.say(f"auto {'on' if on else 'off'}" + ("" if self.cfg.live or not on else " (dry run: previews only)"))
        elif cmd == "/status":
            self.say(self.status())
        elif cmd == "/stats":
            self.say(self.stats())
        elif cmd == "/humans":
            n = int(arg) if arg.strip().isdigit() else 5
            self.say(f"Checking every buyer's wallet history for the last {min(n, 30)} coins...")
            self.say(self.humans(min(n, 30)))
        elif cmd == "/feed" and arg.strip().lower().startswith("auto"):
            val = arg.strip().lower()[4:].strip()
            if val:
                try:
                    sol = 0.0 if val == "off" else float(val)
                except ValueError:
                    sol = -1.0
                if sol < 0:
                    self.say("/feed auto <SOL a day> or /feed auto off")
                    return
                self.ledger.put("feed_auto_sol", repr(sol))
                self.feed_auto_cap = sol
            cap = self.feed_auto()
            self.say(f"feed auto-launch {'on' if cap > 0 else 'off'}"
                     + (f": posts with no coins on them yet, designed within {self.cfg.feed_auto_max_age_s:g}s, at "
                        f"most {self.cfg.feed_auto_max_per_hour} an hour and {cap:g} SOL a day (spent today "
                        f"{self.ledger.spent_today(AUTO_SPEND):.4f}); everything else still waits for your tap"
                        if cap > 0 else ""))
        elif cmd == "/replies":
            mode = arg.strip().lower()
            if mode in ("on", "off"):
                self.ledger.put("replies", "1" if mode == "on" else "0")
                self.next_feed_check = 0
                if self.feed:
                    self.feed.replying = mode == "on" and not quiet(time.time(), self.cfg.content_quiet_utc)
            self.say(self.replies_status())
        elif cmd == "/feed":
            mode = arg.strip().lower()
            if mode in ("on", "off"):
                self.ledger.put("feed", "1" if mode == "on" else "0")
                self.next_feed_check = 0
            f = self.feed
            self.say(f"feed coins {'on' if self.ledger.get('feed', '1') == '1' else 'off'}"
                     + (f": connected, {len(f.posts)} posts seen, {len(f.sent)} designed this hour "
                        f"(max {self.cfg.feed_max_per_hour}), accounts with {self.cfg.feed_min_followers:,}+ followers"
                        if f and f.alive else f": not connected{' (' + self.feed_error + ')' if self.feed_error else ''}"))
        elif cmd == "/post":
            if arg.strip().lower() in ("on", "off"):
                self.ledger.put("content", "1" if arg.strip().lower() == "on" else "0")
                due = float(self.ledger.get("next_content_ts", "0") or 0)
                self.say(f"scheduled X drafts {arg.strip().lower()} ({self.cfg.x_drafts_per_day}/day"
                         f"{', next ' + time.strftime('%H:%M UTC', time.gmtime(due)) if due else ''})")
            else:
                self.say("Drafting from what's trending...")
                self.draft_post()
        elif cmd == "/devbuy":
            if arg.strip():
                try:
                    sol = float(arg.strip())
                except ValueError:
                    sol = -1.0
                if not 0 <= sol <= MAX_DEV_BUY_SOL:
                    self.say(f"/devbuy <SOL>, between 0 and {MAX_DEV_BUY_SOL:g}")
                    return
                self.ledger.put("dev_buy_sol", repr(sol))
            self.say(f"dev buy {self.dev_buy():g} SOL per launch; a launch now costs ~{self.cost():.4f} SOL"
                     + (". The launch message gets Sell 25/50/100% buttons." if self.dev_buy() else ""))
        elif cmd == "/sell":
            ref, _, pct = arg.strip().partition(" ")
            pct = pct.strip().rstrip("%")
            if not ref or not pct.isdigit() or not 1 <= int(pct) <= 100:
                self.say("/sell <ticker or mint> <percent 1-100>, e.g. /sell HSTK 50")
            else:
                self.sell(ref, int(pct))
        elif cmd == "/claim":
            self.say(self.claim())
        elif cmd == "/unwrap":
            out = pump.unwrap_wsol(self.rpc, self.dev)
            self.say("No wSOL accounts." if out is None else
                     f"Unwrapped {out[0]:.6f} SOL (wSOL + account rent)\n"
                     f"balance now {self.rpc.balance_sol(self.wallet):.4f} SOL\nhttps://solscan.io/tx/{out[1]}")
        elif cmd == "/stop":
            self.ledger.put("stopped", "1")
            self.say("Stopped. No launch will be sent until /resume.")
        elif cmd == "/resume":
            self.ledger.put("stopped", "0")
            self.say("Resumed.")
        elif cmd == "/rebase":
            bal = self.rpc.balance_sol(self.wallet)
            self.ledger.put("baseline_sol", str(bal))
            self.say(f"Baseline set to {bal:.4f} SOL; floor is now {bal * self.cfg.floor_pct / 100:.4f}.")

    def callback(self, q: dict) -> None:
        action, _, pid = (q.get("data") or "").partition(":")
        try:
            self.tg("answerCallbackQuery", {"callback_query_id": q["id"]})
        except http.HttpError as e:  # too late to stop the spinner (the bot was busy); still act on the click
            log.warning("answerCallbackQuery: %s", e)
        if action in ("dp", "dt", "ds", "dx"):
            self.draft_click(action, pid)
            return
        if action == "sl":  # pid is "<mint>.<pct>"
            mint, _, pct = pid.rpartition(".")
            if pct.isdigit() and int(pct) in SELL_PCTS:
                self.sell(mint, int(pct))
            return
        p = self.proposals.pop(pid, None)
        if p is None or time.time() - p.created > PROPOSAL_TTL_S:
            self.say("That proposal expired; /propose again for a fresh one.")
        elif action == "go":
            self.launch(p)
            return
        else:
            self.say(f"Skipped ${p.concept.symbol}.")
        if p is not None:
            self.discard(p)

    def draft_click(self, action: str, ref: str) -> None:
        if action == "dx":  # ref is the post id
            social.delete(self.cfg.x_keys, ref)
            self.say(f"Deleted {social.post_url(ref)}")
        elif action == "ds":
            self.drafts.pop(ref, None)
            self.say("Skipped.")
        else:
            did, _, i = ref.partition(".")
            self.post_draft(did, int(i or 0), with_image=action == "dp")

    def handle(self, update: dict) -> None:
        msg = update.get("message") or {}
        q = update.get("callback_query")
        user = (q or msg).get("from", {}).get("id")
        if user not in self.cfg.allowed_users:
            return  # silently ignore strangers
        if q:
            self.callback(q)
        elif msg.get("text", "").startswith("/j7"):
            self.set_j7_session(msg, msg["text"].partition(" ")[2].strip())
        elif msg.get("text", "").startswith("/"):
            self.command(msg["text"])

    # ---- loop -----------------------------------------------------------------
    def guarded(self, what: str, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as e:
            log.error("%s failed: %s\n%s", what, e, traceback.format_exc())
            self.say(f"{what} failed: {e}")

    def set_menu(self) -> None:
        """Show the commands in Telegram's "/" menu, only in the allowed users' own chats."""
        commands = [{"command": c, "description": d} for c, d in COMMANDS]
        for user in self.cfg.allowed_users:
            self.tg("setMyCommands", {"commands": commands, "scope": {"type": "chat", "chat_id": user}})

    def run(self) -> None:
        self.guarded("menu", self.set_menu)
        self.say("devbot up.")
        self.guarded("status", lambda: self.say(self.status()))  # an RPC outage must not kill startup
        while True:
            try:
                # Auto-launch needs the loop back within seconds of a design, not after a 20 s long poll.
                wait = 2 if self.feed_auto_cap > 0 and self.feed and self.feed.alive else 20
                resp = self.tg("getUpdates", {"offset": self.offset, "timeout": wait,
                                              "allowed_updates": ["message", "callback_query"]})
                updates = resp.get("result", [])
            except Exception as e:
                log.warning("getUpdates: %s", e)
                time.sleep(5)
                updates = []
            for u in updates:
                self.offset = u["update_id"] + 1
                self.guarded("command", self.handle, u)
            now = time.time()
            for pid in [k for k, v in self.proposals.items() if now - v.created >= PROPOSAL_TTL_S]:
                self.discard(self.proposals.pop(pid))
            for did in [k for k, v in self.drafts.items() if now - v.created >= DRAFT_TTL_S]:
                del self.drafts[did]
            self.content_tick(now)
            self.guarded("auto", self.auto_tick)
            if now >= self.next_pvp:
                self.next_pvp = now + self.cfg.pvp_poll_s
                self.pvp_cycle()
            if now >= self.next_feed_check:
                self.next_feed_check = now + 30
                self.guarded("feed", self.feed_tick)
            if now >= self.next_poll:
                self.next_poll = now + POLL_S
                try:
                    self.trends.poll()
                except Exception as e:  # a missed poll only thins the sample; don't spam Telegram
                    log.warning("trends poll: %s", e)
            if now >= self.next_housekeep:
                self.next_housekeep = now + HOUSEKEEP_S
                self.housekeep()
            if now >= self.next_cycle:
                self.next_cycle = now + self.cfg.scan_minutes * 60
                if self.ledger.auto and not self.ledger.stopped:
                    self.auto_cycle()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = config_mod.load()
    except config_mod.ConfigError as e:
        log.error("config: %s", e)
        sys.exit(78)  # systemd: RestartPreventExitStatus
    Bot(cfg).run()


if __name__ == "__main__":
    main()
