"""News first: start from the story, find the coin named after it while it is still small.

Every 30 s the newest coins and the coins with the most trading and organic buying in the last 5 min
(Jupiter keyless lists) are matched against the StoryBook. A coin that carries a distinctive word of a
story already out is put on the news watch. A watched coin becomes a NEWS CANDIDATE the first time its
market cap is $30k-$300k (it has real buyers, but has not run yet), and the recorder admits it as
`nadmit` (a separate row type, so the price-led preregistration's sample is untouched).

Its enrichment adds what only the news path knows: the matched stories, every other coin seen
carrying the same word (its rivals for that story), and Jev's reading of whether the coin is named
after the story's subject. `rule.news_why_not` then decides.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

from devbot import http

from . import features, sources, stories

JUP = "https://lite-api.jup.ag/tokens/v2"
LISTS = ("recent", "toptrending/5m?limit=100", "toporganicscore/5m?limit=100", "toptraded/5m?limit=100")
POLL_S, STORIES_S = 30, 600
NEWS_MC_LO, NEWS_MC_HI = 30_000, 300_000
MAX_COIN_AGE_S = 72 * 3600
WATCH_FOR_S = 24 * 3600


def _ts(iso: str | None) -> float:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def jup_list(path: str) -> list[dict]:
    rows = features._jup(lambda: http.get_json(f"{JUP}/{path}", timeout=20)) or []
    out = []
    for t in rows:
        out.append({"mint": t.get("id"), "name": t.get("name") or "", "symbol": t.get("symbol") or "",
                    "dev": t.get("dev"), "mc": t.get("mcap") or 0, "created": _ts(t.get("createdAt")),
                    "twitter": t.get("twitter"), "organic": t.get("organicScore"), "launchpad": t.get("launchpad")})
    return [c for c in out if c["mint"]]


class NewsWatch:
    def __init__(self) -> None:
        self.book = stories.StoryBook()
        self.watch: dict[str, dict] = {}      # mint -> coin + {words, story_keys, first_seen, mc_seen}
        self.done: set[str] = set()           # mints already handed to the recorder
        self.seen_new: set[str] = set()       # mints already counted as launches
        self.next_poll = self.next_stories = 0.0

    def poll(self, now: float | None = None) -> list[dict]:
        """One round. Returns news candidates (coins entering the $30k-$300k band with a story)."""
        now = now or time.time()
        if now >= self.next_stories:
            self.next_stories = now + STORIES_S
            self.book.add(stories.fetch(), now)
        if now < self.next_poll:
            return []
        self.next_poll = now + POLL_S
        coins: dict[str, dict] = {}
        for path in LISTS:
            try:
                for c in jup_list(path):
                    coins.setdefault(c["mint"], c)
                    if path == "recent" and c["mint"] not in self.seen_new:
                        self.seen_new.add(c["mint"])
                        self.book.note_launch(c, now)
            except (http.HttpError, OSError, ValueError):
                continue
        if len(self.seen_new) > 50_000:
            self.seen_new.clear()
        out = []
        for m, c in coins.items():
            if m in self.done or not c["created"] or now - c["created"] > MAX_COIN_AGE_S:
                continue
            hits = self.book.match(c["name"], c["symbol"], before=now)
            if m not in self.watch and not hits:
                continue
            w = self.watch.setdefault(m, {**c, "first_seen": now, "words": [], "story_keys": []})
            w.update(mc=c["mc"], name=c["name"], symbol=c["symbol"])
            for word, s in hits:
                if word in s.get("subject_terms", ()) and word not in w.setdefault("subject_words", []):
                    w["subject_words"].append(word)
                if s["key"] not in w["story_keys"]:
                    w["story_keys"].append(s["key"])
                if word not in w["words"]:
                    w["words"].append(word)
            if NEWS_MC_LO <= c["mc"] <= NEWS_MC_HI:
                self.done.add(m)
                out.append(self.candidate(w, now))
        for m in [m for m, w in self.watch.items() if now - w["first_seen"] > WATCH_FOR_S]:
            del self.watch[m]
        return out

    def candidate(self, w: dict, now: float) -> dict:
        """The coin, its stories, and its rivals: every watched coin sharing one of its words."""
        sts = [self.book.stories[k] for k in w["story_keys"] if k in self.book.stories]
        rivals = [{"mint": m, "name": r["name"], "symbol": r["symbol"], "mc": r["mc"], "created": r["created"]}
                  for m, r in self.watch.items() if m != w["mint"] and set(r["words"]) & set(w["words"])]
        return {"mint": w["mint"], "name": w["name"], "symbol": w["symbol"], "dev": w.get("dev"),
                "mc_jup": w["mc"], "created": w["created"], "twitter": w.get("twitter"), "words": w["words"],
                "subject_words": w.get("subject_words", []),
                "stories": [{"src": s["src"], "title": s["title"], "ts": s["ts"], "seen": s["seen"]} for s in sts][:8],
                "rivals": rivals[:40], "first_seen": w["first_seen"], "decided": now}

    def state(self) -> dict:
        return {"stories": len(self.book.stories), "watch": len(self.watch), "done": len(self.done)}


def news_ranks(c: dict, mc_now: float) -> dict:
    """The candidate's rank among the coins sharing its story word, by market cap now and by launch."""
    rv = c.get("rivals") or []
    return {"n_rivals": len(rv), "mc_rank": 1 + sum((r["mc"] or 0) > mc_now for r in rv),
            "t_rank": 1 + sum(0 < (r["created"] or 0) < (c["created"] or 0) for r in rv)}


def live_ranks(c: dict, mint: str) -> dict:
    """`news_ranks` re-priced at the decision. The rivals' market caps in `c` are from whenever each was last
    on a Jupiter list, often at launch, so on 2026-10-23 both coins of a same-minute pair ranked themselves
    biggest: Humor $33k (rugged in one minute) beside Humor $64k (4.2x), MEOWE $33k (rugged) beside MEOWE $55k
    (8.8x). One DexScreener batch per 30 mints; a rival DexScreener does not list keeps its stale cap."""
    rv = c.get("rivals") or []
    try:
        got = sources._batch([mint] + [r["mint"] for r in rv])
    except (http.HttpError, OSError, ValueError):
        return {"err": "price"}

    def mc(m: str, stale: float) -> float:
        p = got.get(m)
        return (p.get("marketCap") or p.get("fdv") or 0) if p else (stale or 0)

    own = mc(mint, c.get("mc_jup") or 0)
    caps = [mc(r["mint"], r["mc"]) for r in rv]
    return {"n_rivals": len(rv), "n_priced": sum(r["mint"] in got for r in rv), "mc": own,
            "mc_rank": 1 + sum(x > own for x in caps), "top_rival_mc": max(caps, default=0),
            "t_rank": 1 + sum(0 < (r["created"] or 0) < (c["created"] or 0) for r in rv)}


def judge(c: dict) -> dict:
    """Jev: is the coin named after the story's subject, and does its name get that subject wrong.
    Wording chosen 2026-10-22 on 9 hand cases (IN SAMPLE): a "named after the specific person, animal, thing
    or event" wording gave PHP-UP-2026 0.46 and cybercurrency 0.14; this one gives noise 0.11-0.38
    (once, First Jet Airways, Trump Index, Golden Retriever) and the real ones 0.54-0.96 (PHP-UP-2026,
    PATCHED, cybercurrency, Macca The Gator, Microduck)."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        return {}
    state = {"coin": {"name": c["name"], "ticker": c["symbol"], "linked_x_post": c.get("twitter") or ""},
             "stories": [s["title"] for s in c.get("stories") or []]}
    qs = {
        "named_after_story": {"type": "noul", "instructions": "Is the memecoin `coin` a coin about the story in "
                              "`stories`: does its name or ticker point at that story's main subject, its event, or "
                              "its key word (directly, as a nickname, a pun or a ticker), so that traders who saw the "
                              "story would buy it as the coin for that story? Sharing an everyday word that is not "
                              "what the story is about does not count."},
        "variant": {"type": "noul", "instructions": "Does the coin's name or ticker get its subject wrong or mark "
                    "itself as a spin-off: a misspelling or wrong name for the person, animal or thing it is about, "
                    "or a '2.0', 'baby', 'real', 'official', 'CTO', 'wife of' or 'son of' version?"},
        "fresh_event": {"type": "noul", "instructions": "Is the story the coin is named after a specific new event "
                        "from the last day or two (an announcement, incident, release, viral moment), rather than "
                        "an evergreen subject that is always in the news?"},
    }
    try:
        out = json.loads(http.post_json(features.JEV, {"model": "jev-latest", "state": state, "questions": qs},
                                        headers={"Authorization": f"Bearer {key}"}, timeout=30))
    except (http.HttpError, OSError, ValueError) as e:
        return {"err": str(e)[:160]}
    a = out.get("answers") or {}
    return {q: (a.get(q) or {}).get("noul") for q in ("named_after_story", "variant", "fresh_event")}
