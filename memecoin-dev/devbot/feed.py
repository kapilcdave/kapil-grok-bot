"""Coins off other people's X posts, from j7tracker's live feed (docs.j7tracker.io/docs/feed). Only the
feed is j7tracker's: the coins themselves launch through LAUNCHER (PumpPortal by default).

The feed is Socket.IO: every post from the X accounts j7tracker watches arrives as `tweet` within
seconds, and `ai_suggestion` / `ai_suggestion_update` carry j7tracker's obvious coin name for it plus
the coins other users already launched for it. Sampled 2026-10-14: ~11 posts a minute, mostly news,
replies and politics, and 5 identical "Moneyball" coins within seconds of one post. So this is a
crowded race: the bot keeps only original posts from big accounts, skips grim and political ones,
designs its OWN name and art (never the sixth identical copy), shows you how many copies exist, and
waits for your Launch click. The coin links memephil's own launch post (X's API refuses to quote a
post that doesn't mention us), so the poster is never presented as involved.

Replies: posts that pass `worth_a_reply` go to a second worker, which asks memephil's reply writer
(content.replies) and sends the drafts to Telegram as "Reply on X" links. At most REPLY_ASKS_PER_HOUR
model calls an hour, so the free text quota lasts.

Threads: the socket, the designer and the reply writer run beside the bot's Telegram loop. The designer only touches
the network and the bot's in-memory proposals; the ledger stays on the main thread.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from .content import GRIM

log = logging.getLogger("devbot.feed")
HOSTS = ("https://nyc.j7tracker.io", "https://dfw.j7tracker.io")
POLITICS = re.compile(r"\b(?:trump|biden|harris|vance|obama|election|vote|voting|ballot|democrat\w*|republican\w*|gop|"
                      r"senate|congress|parliament|tariffs?|iran|israel\w*|gaza|hamas|ukrain\w*|russia\w*|putin|"
                      r"zelensk\w*|netanyahu|farage|starmer|immigra\w*|deport\w*|abortion)\b", re.IGNORECASE)
REPLY_ASKS_PER_HOUR = 20    # model calls for replies: Groq's free plan is 1,000 requests a day for everything


@dataclass
class Post:
    id: str
    handle: str
    name: str
    followers: int
    text: str
    url: str
    created: float
    images: list[str] = field(default_factory=list)
    suggestion: str = ""    # j7tracker's obvious coin name for the post
    copies: int = 0         # coins other users already launched for it


def _created(value) -> float:
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e12 else float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def to_post(t: dict) -> Post | None:
    a = t.get("author") or {}
    if not t.get("id") or not a.get("handle"):
        return None
    return Post(str(t["id"]), a["handle"], a.get("name") or a["handle"], int(a.get("followersCount") or 0),
                (t.get("text") or "").strip(), t.get("tweetUrl") or f"https://x.com/{a['handle']}/status/{t['id']}",
                _created(t.get("createdAt")), [i["url"] for i in (t.get("media") or {}).get("images") or [] if i.get("url")])


def worth_a_coin(t: dict, p: Post, min_followers: int, max_age_s: float, now: float | None = None) -> str:
    """'' if the post is a candidate, else why not."""
    now = now or time.time()
    if t.get("type") not in ("TWEET", "QUOTE") or t.get("isReply") or t.get("isRetweet"):
        return "not an original post"
    if p.followers < min_followers:
        return f"@{p.handle} has {p.followers:,} followers"
    if now - p.created > max_age_s:
        return f"{now - p.created:.0f}s old"
    if t.get("contractAddress"):
        return "already shills a coin"
    if len(re.sub(r"https?://\S+", "", p.text).strip()) < 3 and not p.images:
        return "nothing to make a coin of"
    if GRIM.search(p.text) or POLITICS.search(p.text):
        return "grim or political"
    return ""


def worth_a_reply(t: dict, p: Post, min_followers: int, max_age_s: float, now: float | None = None) -> str:
    """'' if memephil might reply to the post, else why not. The model still decides whether it fits."""
    now = now or time.time()
    if t.get("type") not in ("TWEET", "QUOTE") or t.get("isReply") or t.get("isRetweet"):
        return "not an original post"
    if p.followers < min_followers:
        return f"@{p.handle} has {p.followers:,} followers"
    if now - p.created > max_age_s:
        return f"{now - p.created:.0f}s old"
    if t.get("contractAddress"):
        return "shills a coin"
    if len(re.sub(r"https?://\S+", "", p.text).strip()) < 20:
        return "too little text to reply to"
    if GRIM.search(p.text) or POLITICS.search(p.text):
        return "grim or political"
    return ""


class Feed:
    """Connects with the bot's j7tracker access token and hands candidate posts to `design` (called on
    the feed's own worker thread, newest post first; at most `max_per_hour` designs that return True)."""

    def __init__(self, token: str, design, *, min_followers: int, max_age_s: float, max_per_hour: int,
                 reply=None, reply_min_followers: int = 0, reply_max_age_s: float = 300.0,
                 reply_max_per_hour: int = 0) -> None:
        self.token, self.design = token, design
        self.min_followers, self.max_age_s, self.max_per_hour = min_followers, max_age_s, max_per_hour
        self.reply, self.reply_min_followers = reply, reply_min_followers
        self.reply_max_age_s, self.reply_max_per_hour = reply_max_age_s, reply_max_per_hour
        self.posts: dict[str, Post] = {}
        self.todo: queue.Queue[Post] = queue.Queue()
        self.reply_todo: queue.Queue[Post] = queue.Queue()
        self.sent: list[float] = []
        self.replying = True             # the bot turns this off for /replies off and quiet hours
        self.replied: list[float] = []   # reply drafts sent to Telegram in the last hour
        self.asked: list[float] = []     # reply model calls in the last hour
        self.error = ""
        self.stopped = threading.Event()
        self.sio = None

    # ---- socket ----------------------------------------------------------------
    def start(self) -> None:
        import socketio  # only the feed needs it

        sio = socketio.Client(reconnection=True, reconnection_delay=2, reconnection_delay_max=30)
        sio.on("connect", lambda: sio.emit("user_connected", self.token))
        sio.on("tweet", self.on_tweet)
        sio.on("tweet_update", self.on_tweet)
        sio.on("ai_suggestion", self.on_suggestion)
        sio.on("ai_suggestion_update", self.on_copies)
        sio.on("auth_error", lambda e: self.fail(f"j7tracker refused the session: {(e or {}).get('error')}"))
        self.sio = sio
        threading.Thread(target=self._connect, daemon=True, name="j7-feed").start()
        threading.Thread(target=self._work, daemon=True, name="j7-design").start()
        if self.reply:
            threading.Thread(target=self._reply_work, daemon=True, name="j7-reply").start()

    def _connect(self) -> None:
        for host in HOSTS:
            try:
                self.sio.connect(host, transports=["websocket"], auth={"token": self.token}, wait_timeout=15)
                return
            except Exception as e:  # try the other region
                self.error = f"feed connect {host}: {e}"
                log.warning(self.error)
        self.fail(self.error)

    def stop(self) -> None:
        self.stopped.set()
        try:
            if self.sio:
                self.sio.disconnect()
        except Exception:
            pass

    def fail(self, why: str) -> None:
        self.error = why
        self.stop()

    @property
    def alive(self) -> bool:
        return not self.stopped.is_set()

    # ---- events ----------------------------------------------------------------
    def on_tweet(self, t: dict) -> None:
        p = to_post(t)
        if p is None or p.id in self.posts:
            return  # the first copy decides; updates only add media, which the designer doesn't need
        self.posts[p.id] = p
        if len(self.posts) > 2000:
            for k in list(self.posts)[:1000]:
                del self.posts[k]
        why = worth_a_coin(t, p, self.min_followers, self.max_age_s)
        if not why:
            self.todo.put(p)
        if self.reply and self.replying and not worth_a_reply(t, p, self.reply_min_followers, self.reply_max_age_s):
            self.reply_todo.put(p)

    def on_suggestion(self, s: dict) -> None:
        p = self.posts.get(str(s.get("tweet_id")))
        if p is not None:
            p.suggestion = re.sub(r"[⁠​]", "", s.get("prediction") or "").strip()

    def on_copies(self, s: dict) -> None:
        p = self.posts.get(str(s.get("tweet_id")))
        if p is not None:
            p.copies = max(p.copies, len(s.get("results") or []))

    # ---- designer ----------------------------------------------------------------
    def _work(self) -> None:
        while not self.stopped.is_set():
            try:
                p = self.todo.get(timeout=1)
            except queue.Empty:
                continue
            while not self.todo.empty():  # a burst: the newest post wins, the rest are already late
                p = self.todo.get_nowait()
            now = time.time()
            self.sent = [s for s in self.sent if now - s < 3600]
            if len(self.sent) >= self.max_per_hour or now - p.created > self.max_age_s:
                continue
            try:
                if self.design(p):  # a failed or skipped design doesn't use up the hour's proposals
                    self.sent.append(now)
            except Exception as e:
                log.warning("feed design for %s failed: %s", p.url, e)

    # ---- reply writer ----------------------------------------------------------------
    def _reply_work(self) -> None:
        while not self.stopped.is_set():
            try:
                p = self.reply_todo.get(timeout=1)
            except queue.Empty:
                continue
            while not self.reply_todo.empty():  # a burst: the newest post wins, early replies get seen
                p = self.reply_todo.get_nowait()
            now = time.time()
            self.replied = [s for s in self.replied if now - s < 3600]
            self.asked = [s for s in self.asked if now - s < 3600]
            if (len(self.replied) >= self.reply_max_per_hour or len(self.asked) >= REPLY_ASKS_PER_HOUR
                    or now - p.created > self.reply_max_age_s):
                continue
            self.asked.append(now)
            try:
                if self.reply(p):  # the model passing on a post doesn't use up the hour's drafts
                    self.replied.append(now)
            except Exception as e:
                log.warning("reply draft for %s failed: %s", p.url, e)
