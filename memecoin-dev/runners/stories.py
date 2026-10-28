"""Stories breaking right now, from free feeds, indexed by the words a coin would be named with.

    Google News   top stories and the World, Nation, Business, Technology, Entertainment, Science and
                  Sports sections (RSS, keyless)
    Google Trends US daily trending searches (RSS), with their news headlines
    launch burst  three or more new coins from two or more devs sharing a name word within 30 min: the
                  crypto-native trace of an X post or a moment we have no feed for (Microduck, an NVIDIA post)

A coin matches a story through a DISTINCTIVE word: one that appears in only a few of the current
headlines ("php" yes, "market" no). 2026-10-21: "PHP warns admins of two critical flaws" was
published 20:56Z on 10-20; PHP-UP-2026 launched 05:14Z on 10-21 and ran $67k -> $761k by 08:00Z.
"""
from __future__ import annotations

import email.utils
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

from devbot import http
from devbot.trends import terms

GN = "https://news.google.com/rss"
GN_Q = "?hl=en-US&gl=US&ceid=US:en"
SECTIONS = ("WORLD", "NATION", "BUSINESS", "TECHNOLOGY", "ENTERTAINMENT", "SCIENCE", "SPORTS")
TRENDS = "https://trends.google.com/trending/rss?geo=US"
KEEP_S = 48 * 3600
DF_MAX = 4                 # a word in more than 4 live headlines is too common to name a coin after
BURST_WINDOW_S, BURST_COINS, BURST_DEVS = 1800, 3, 2
NOT_A_SUBJECT = frozenset("""news report reports update live watch video photo photos new year week month
first last top late early world us uk state city country people man woman men women life game games season
""".split())


def _rss(url: str) -> list[dict]:
    try:
        root = ET.fromstring(http.request("GET", url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15))
    except (http.HttpError, OSError, ET.ParseError):
        return []
    out = []
    ns = {"ht": "https://trends.google.com/trending/rss"}
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        try:
            ts = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            ts = time.time()
        extra = [e.text or "" for e in it.findall("ht:news_item/ht:news_item_title", ns)]
        out.append({"title": title, "ts": ts, "extra": extra[:3]})
    return out


def fetch() -> list[dict]:
    """Every current headline and trending search: [{key, src, title, ts, text}]."""
    rows = []
    for name, url in [("news_top", f"{GN}{GN_Q}")] + [
            (f"news_{s.lower()}", f"{GN}/headlines/section/topic/{s}{GN_Q}") for s in SECTIONS]:
        for r in _rss(url):
            title = re.sub(r"\s+-\s+[^-]+$", "", r["title"])          # drop " - Publisher"
            rows.append({"src": name, "title": title, "ts": r["ts"], "text": title})
    for r in _rss(TRENDS):
        rows.append({"src": "trends", "title": r["title"], "ts": r["ts"],
                     "text": " ".join([r["title"]] + r["extra"])})
    for r in rows:
        r["key"] = re.sub(r"\W+", " ", r["title"].lower()).strip()[:120]
    return rows


def subject_text(title: str) -> str:
    """The capitalised and upper-case words of a headline (PHP, Nvidia, Macca, GTA): its likely subjects.
    Sentence case also capitalises the first word, and Title Case every word; the rarity filter and Jev
    handle those."""
    words = re.findall(r"[A-Za-z][\w'&.-]*", title)
    keep = []
    for i, w in enumerate(words):
        if w.isupper() and len(w) > 1:
            keep.append(w)
        elif w[0].isupper():
            keep.append(w)
    return " ".join(keep)


class StoryBook:
    def __init__(self) -> None:
        self.stories: dict[str, dict] = {}              # key -> {key, src, title, ts, seen, terms}
        self.launches: list[tuple[float, str, str, str]] = []   # (ts, word, mint, dev) from new coins

    def add(self, rows: list[dict], now: float | None = None) -> list[dict]:
        now = now or time.time()
        new = []
        for r in rows:
            if r["key"] in self.stories or not r["key"]:
                continue
            # Match on every word ("tiny robot duck" names Microduck in lower case); whether the word was a
            # capitalised subject word is kept for the rule and the scorer, and Jev reads the headline.
            subj = r["text"] if r["src"] == "trends" else subject_text(r["text"])
            st = {**r, "seen": now, "terms": sorted(terms(r["text"], "", NOT_A_SUBJECT)),
                  "subject_terms": sorted(terms(subj, "", NOT_A_SUBJECT))}
            self.stories[r["key"]] = st
            new.append(st)
        for k in [k for k, s in self.stories.items() if now - min(s["ts"], s["seen"]) > KEEP_S]:
            del self.stories[k]
        return new

    def note_launch(self, coin: dict, now: float | None = None) -> list[dict]:
        """Record a new coin's name words; a word now shared by a burst of launches becomes a story."""
        now = now or time.time()
        made = []
        for w in terms(coin.get("name", ""), coin.get("symbol", ""), NOT_A_SUBJECT):
            self.launches.append((now, w, coin["mint"], coin.get("dev") or ""))
        self.launches = [x for x in self.launches if now - x[0] <= BURST_WINDOW_S]
        by: dict[str, list] = defaultdict(list)
        for x in self.launches:
            by[x[1]].append(x)
        for w, xs in by.items():
            key = f"launch burst {w}"
            if key in self.stories or len({x[2] for x in xs}) < BURST_COINS or len({x[3] for x in xs}) < BURST_DEVS:
                continue
            st = {"key": key, "src": "launch_burst", "title": f"{len({x[2] for x in xs})} new coins named '{w}' "
                  f"from {len({x[3] for x in xs})} devs in 30 min", "ts": min(x[0] for x in xs), "seen": now,
                  "text": w, "terms": [w], "subject_terms": [w]}
            self.stories[key] = st
            made.append(st)
        return made

    def df(self) -> dict[str, int]:
        d: dict[str, int] = defaultdict(int)
        for s in self.stories.values():
            if s["src"] != "launch_burst":
                for t in s["terms"]:
                    d[t] += 1
        return d

    def match(self, name: str, symbol: str, before: float | None = None) -> list[tuple[str, dict]]:
        """(word, story) pairs where the coin's name or ticker carries a distinctive word of a story
        that was already out (seen by us) before `before`."""
        before = before or time.time()
        df = self.df()
        words = terms(name, symbol, NOT_A_SUBJECT)
        out = []
        for s in self.stories.values():
            if s["seen"] > before:
                continue
            for w in words & set(s["terms"]):
                if s["src"] == "launch_burst" or df.get(w, 0) <= DF_MAX:
                    out.append((w, s))
        return out
