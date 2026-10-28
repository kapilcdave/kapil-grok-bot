"""The pick rule, in one place so the live log and the scorer cannot disagree.

PRIMARY (preregistered 2026-10-21, `PREREG-runners-20261021.md`): a coin admitted to the runner zone is
a pick when, using only what was known by the time its enrichment finished,

    1. its market cap at admission is between $150k and $5M   (early in the run, not the top)
    2. it is the biggest AND the earliest of its look-alikes launched within 48 h   (not a vamp)
    3. it has a story: it links a specific X post (a status URL, not a profile or community), or Jev
       says a headline dated before admission is a fresh event about the coin's subject (news_event >= 0.5).
       A ticker match alone is not a story: PHP-UP-2026 matched headlines about the PHP language.
       Without a TYPESAFE_API_KEY only the X-post path is live.

Secondary variants, reported next to the primary and never instead of it:
    no_story   conditions 1-2 only. Primary minus this isolates what the story evidence adds.
    jev        conditions 1-2 plus Jev: not a misnamed/spin-off name, a real-world or celebrity story.
    float      the primary split by top-10 share above/below 50%. Sign unknown: Kimchi had 78.7%.
"""
from __future__ import annotations

MC_LO, MC_HI = 150_000, 5_000_000


def why_not(admit: dict, enr: dict) -> str:
    """'' if the coin is a primary pick, else the first failed condition."""
    mc = admit.get("mc") or 0
    if not MC_LO <= mc <= MC_HI:
        return "mc"
    v = original_ranks(admit, enr)
    if v.get("mc_rank") != 1 or v.get("t_rank") != 1:
        return "not_original"
    st = enr.get("story") or {}
    news = ((enr.get("judge") or {}).get("news_event") or 0) >= 0.5
    if not st.get("post_url") and not news:
        return "no_story"
    return ""


def original_ranks(admit: dict, enr: dict) -> dict:
    """The coin's rank among its look-alikes by size and by launch time. Fixed 2026-10-22: when DexScreener's
    search did not return the coin itself (PANGEA, CYBER on 10-21: mc_rank 0, n_sib -1) every such coin was
    called a vamp; the coin is now ranked with its own admission market cap and pair creation time."""
    v = enr.get("vamp") or {}
    if v.get("mc_rank"):
        return v
    sibs = [s for s in enr.get("siblings") or [] if s.get("mint") != admit.get("mint")]
    me = {"mint": admit.get("mint"), "mc": admit.get("mc") or 0, "created": admit.get("pair_created") or admit["ts"]}
    return {"n_sib": len(sibs),
            "mc_rank": 1 + sum(s["mc"] > me["mc"] for s in sibs),
            "t_rank": 1 + sum(s["created"] < me["created"] for s in sibs)}


def jev_rule(admit: dict, enr: dict) -> bool:
    """Secondary: conditions 1-2, with Jev deciding the story (not a misnamed or spin-off name, and a
    real-world or celebrity story)."""
    j = enr.get("judge") or {}
    if not j or j.get("err") or why_not(admit, enr) in ("mc", "not_original"):
        return False
    variant = j.get("variant")
    return bool(variant is not None and variant < 0.5 and j.get("kind") in ("real_world_news", "celebrity_post"))


# AVOID filter (preregistered 2026-10-21 ~23:30Z, `PREREG-runners-avoid-20261021.md`), from the 186-coin
# backfill: a coin less than 1 h old at admission whose curve was bought out at launch, whose dev bought
# >= 10 SOL, or whose subject is a mainstream name (the TRUMP / OpenAI / NVIDIA / YouTube brand-coin factory).
# In sample at 4 h: avoided n=33 mean -90%, rug 91%; kept n=53 mean -24%, rug 26%.
AVOID_AGE_S, AVOID_DEV_SOL, AVOID_MAINSTREAM = 3600, 10.0, 0.5


def avoid(admit: dict, enr: dict) -> bool | None:
    """True = a fresh launch-bought or brand coin; None when the features needed are missing."""
    f = enr.get("features") or {}
    lc = f.get("launch") or {}
    created = lc.get("created")
    if not created:
        return None
    if admit["ts"] - created >= AVOID_AGE_S:
        return False
    dev_sol = (f.get("dev") or {}).get("dev_buy_sol") or 0
    mainstream = (f.get("kind") or {}).get("mainstream") or 0
    return lc.get("grad_lag_s") == 0 or dev_sol >= AVOID_DEV_SOL or mainstream >= AVOID_MAINSTREAM


# NEWS FIRST (preregistered 2026-10-22, `PREREG-newsfirst-20261022.md`): start from the story, buy the coin
# named after it while it is small. A news candidate (`nadmit`) is a pick when, at the end of its enrichment,
NEWS_MC_LO, NEWS_MC_HI = 30_000, 300_000
NEWS_NAMED, NEWS_VARIANT = 0.5, 0.5


def reacts_to_story(admit: dict, judge: dict) -> bool:
    n = admit.get("news") or {}
    created = n.get("created") or 0
    after = any(s["src"] != "launch_burst" and created and s["ts"] <= created for s in n.get("stories") or [])
    return after or (judge.get("fresh_event") or 0) >= 0.5


def news_why_not(admit: dict, enr: dict, use_avoid: bool = True, source: str = "news", ranks: str = "news_ranks") -> str:
    """'' if a news pick, else the first failed condition.
        1. market cap at admission $30k-$300k
        2. it matched a story from `source`: "news" = a Google News headline or Google Trends search,
           "burst" = a burst of same-named launches (the X-post proxy), reported separately
        3. Jev: named after that story's subject (>= 0.5), and not a misnamed or spin-off name (< 0.5)
        3b. the coin reacts to the story: it was created after a matched headline was published, or Jev
            reads the story as a fresh event (>= 0.5). Amended 2026-10-22 05:50Z, before any return: the
            first news pick was JONATHAN (a 10-21 coin) on an NFL trade-deadline headline naming a player
            Jonathan; fresh_event 0.44. An old coin re-running on news (Macca on GTA 6) still passes on 3b's
            second clause.
        4. the biggest of the coins seen carrying the same story word (its rivals for the story)
        5. not an avoid coin (fresh and launch-bought, a >= 10 SOL dev buy, or a mainstream brand name)
    `ranks="news_ranks_live"` is rule v2 (PREREG-newsfirst-v2-20261023.md): condition 4 on rivals re-priced at
    the decision instead of their caps when last seen."""
    if not NEWS_MC_LO <= (admit.get("mc") or 0) <= NEWS_MC_HI:
        return "mc"
    srcs = {s["src"] for s in (admit.get("news") or {}).get("stories") or []}
    if source == "news" and not any(s != "launch_burst" for s in srcs):
        return "no_news_story"
    if source == "burst" and "launch_burst" not in srcs:
        return "no_burst"
    j = enr.get("news_judge") or {}
    if (j.get("named_after_story") or 0) < NEWS_NAMED:
        return "not_named_after_story"
    if source == "news" and not reacts_to_story(admit, j):
        return "coin_predates_story"
    if j.get("variant") is None or j["variant"] >= NEWS_VARIANT:
        return "variant"
    if (enr.get(ranks) or {}).get("mc_rank") != 1:
        return "not_biggest"
    if use_avoid and avoid(admit, enr) is not False:
        return "avoid"
    return ""
