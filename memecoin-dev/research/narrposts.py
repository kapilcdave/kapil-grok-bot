"""Narrative collector, part 2: follow a snipefeed tape and save the TEXT of every X post a new coin links.

The tape (crypto/dexarb/snipefeed.py) has each coin's name, ticker, description, image and links, plus every
trade, so outcomes (did its term catch on, peak market cap, fees) can be labelled offline. What it lacks is the
narrative itself: the post the coin was made about. This reads the tape as it grows and fetches each linked
post once through X's free oEmbed endpoint (no key), so a later model can learn which posts become narratives.

Rows, one JSON object per line:
    {"t":"post","ts":..,"id":..,"url":..,"handle":..,"author":..,"text":..,"html":..,"first_mint":..}
    {"t":"post_fail","ts":..,"id":..,"url":..,"err":..}
    {"t":"ref","ts":..,"mint":..,"id":..}            every coin that links a post (incl. repeats)
    {"t":"stats","ts":..,"counts":{..},"backlog":..}

    python narrposts.py --tape ~/tapes/narr-tape-X.jsonl --out ~/tapes/narr-posts-X.jsonl --hours 12
Stop early: touch the STOP file next to --out (narr-posts-X.jsonl.STOP).
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

STATUS_RE = re.compile(r"(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)", re.I)
OEMBED = "https://publish.twitter.com/oembed?url={}&omit_script=true&dnt=true"
MAX_PER_MIN = 40
BACKOFF_CAP_S = 120     # a backoff longer than this would lose posts that get deleted overnight


def fetch(url: str) -> dict:
    req = urllib.request.Request(OEMBED.format(urllib.parse.quote(url, safe="")), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read(200_000))


def text_of(h: str) -> str:
    m = re.search(r"<p[^>]*>(.*?)</p>", h or "", re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(1).replace("<br>", "\n"))).strip() if m else ""


class Follower:
    """Yields new complete lines of a file that is still being written (waits for it to appear)."""

    def __init__(self, path: str) -> None:
        self.path, self.f, self.buf = path, None, b""

    def lines(self):
        if self.f is None:
            if not os.path.exists(self.path):
                return
            self.f = open(self.path, "rb")
        chunk = self.f.read(1 << 20)
        if not chunk:
            return
        self.buf += chunk
        *done, self.buf = self.buf.split(b"\n")
        yield from done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", type=float, default=12)
    a = ap.parse_args()
    stop = a.out + ".STOP"
    deadline = time.time() + a.hours * 3600
    out = open(a.out, "a", buffering=1)
    w = lambda row: out.write(json.dumps(row, ensure_ascii=False) + "\n")
    done: set[str] = set()
    queue: collections.OrderedDict[str, tuple[str, str]] = collections.OrderedDict()  # id -> (url, first mint)
    stats = collections.Counter()
    sent: collections.deque[float] = collections.deque()
    pause_until = backoff = 0.0
    next_stats = time.time() + 300
    tail = Follower(a.tape)
    while time.time() < deadline and not os.path.exists(stop):
        for line in tail.lines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                stats["bad_line"] += 1
                continue
            if d.get("t") != "meta" or not d.get("ok"):
                continue
            m = STATUS_RE.search(d.get("twitter") or "") or STATUS_RE.search(d.get("website") or "")
            if not m:
                stats["meta_no_post"] += 1
                continue
            pid = m.group(2)
            w({"t": "ref", "ts": time.time(), "mint": d["mint"], "id": pid})
            stats["refs"] += 1
            if pid not in done and pid not in queue:
                queue[pid] = (f"https://x.com/{m.group(1)}/status/{pid}", d["mint"])
        now = time.time()
        while sent and now - sent[0] > 60:
            sent.popleft()
        fetched = False
        if queue and now >= pause_until and len(sent) < MAX_PER_MIN:
            fetched = True
            pid, (url, mint) = queue.popitem(last=False)   # oldest first: posts can be deleted
            sent.append(now)
            try:
                data = fetch(url)
                w({"t": "post", "ts": now, "id": pid, "url": url,
                   "handle": (data.get("author_url") or "").rstrip("/").rsplit("/", 1)[-1],
                   "author": data.get("author_name"), "text": text_of(data.get("html", "")),
                   "html": (data.get("html") or "")[:4000], "first_mint": mint})
                done.add(pid)
                stats["post_ok"] += 1
                backoff = 0.0
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:   # throttled: put it back, wait (capped)
                    queue[pid] = (url, mint)
                    queue.move_to_end(pid, last=False)
                    backoff = min(BACKOFF_CAP_S, max(5.0, backoff * 2))
                    pause_until = now + backoff
                    stats[f"http_{e.code}"] += 1
                else:                                # 404/403: deleted, protected, or not a post
                    w({"t": "post_fail", "ts": now, "id": pid, "url": url, "err": f"HTTP{e.code}"})
                    done.add(pid)
                    stats[f"fail_{e.code}"] += 1
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                queue[pid] = (url, mint)
                pause_until = now + 5
                stats["net_" + type(e).__name__] += 1
        if now >= next_stats:
            w({"t": "stats", "ts": now, "counts": dict(stats), "backlog": len(queue)})
            next_stats = now + 300
        if not fetched:
            time.sleep(0.5)
    w({"t": "stats", "ts": time.time(), "counts": dict(stats), "backlog": len(queue), "final": True})


if __name__ == "__main__":
    main()
