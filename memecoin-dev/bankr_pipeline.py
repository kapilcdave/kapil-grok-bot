"""DexScreener narrative -> coin concept -> art -> X post -> Bankr launch (Base, gas-sponsored).

    python bankr_pipeline.py once             # one cycle, DRY: scan, design, art, simulate; no X post, no launch
    python bankr_pipeline.py once --live      # one cycle for real
    python bankr_pipeline.py loop --live      # forever, every BANKR_CYCLE_MIN minutes (the systemd unit)
    python bankr_pipeline.py scan             # just print the narratives

Always-on deployment: `deploy/sync_bankr_env.sh` then `deploy/install_bankr.sh` installs
`bankr-pipeline.service` as a lingering user unit on **stockholm**. It cannot run on the Iowa e2-micro:
Bankr is behind an AWS WAF that answers POST /token-launches/deploy from that GCP IP with a 403
awselb HTML page while its GETs still return 200, so check a new box with `deploy/probe_bankr.sh`
(a 401 to a dummy key means the WAF passes it) before moving the unit.

Run from the devbot directory with its venv; keys come from ~/.devbot/.env (Groq / OpenRouter / Cloudflare /
Pollinations for the model and art, PINATA_JWT, the four X_* keys, BANKR_API_KEY, optional Telegram).

Order inside a live cycle: art is pinned to IPFS, the X post goes out (its URL becomes the coin's tweetUrl), the
launch is sent, then the contract address is replied under the post. If the launch fails the post is deleted.

Gates, all checked first: the STOP file (~/.devbot/BANKR_STOP), BANKR_MAX_PER_DAY (default 2; Bankr allows 3 a
wallet), BANKR_GAP_MIN between launches (default 30), X_MAX_POSTS_PER_DAY, no name/symbol already on Bankr's
recent list or in our ledger, and a narrative whose terms we haven't launched on in the last 24 h.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path

import bankr_launch as bl
from devbot import concept, http, image, openrouter, social
from devbot.trends import terms

# Passed per call, never merged into trends.STOP: that set is shared with devbot's own scanner.
EXTRA_STOP = frozenset({"protocol", "finance", "network", "labs", "ai", "agent", "swap", "dao",
                        "capital", "fi"})

DEX = "https://api.dexscreener.com"
PINATA = "https://uploads.pinata.cloud/v3/files"
GATEWAY = "https://gateway.pinata.cloud/ipfs/"
STATE_DIR = Path(os.environ.get("DEVBOT_STATE", "~/.devbot")).expanduser()
STATE = STATE_DIR / "bankr_pipeline.json"
STOP = STATE_DIR / "BANKR_STOP"
ROWS = Path(__file__).parent / "data" / "bankr_pipeline.jsonl"

MAX_AGE_H = 72          # a token older than this is not "now"
MIN_VOL_H1 = 1500.0     # USD traded in the last hour
MIN_LIQ = 3000.0
MIN_TOKENS = 2          # a narrative needs this many tokens ...
MIN_NAMES = 2           # ... under this many distinct names


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


# ---------------------------------------------------------------- scan

def _get(path: str):
    return http.get_json(DEX + path, timeout=25)


def candidates() -> dict[str, set[str]]:
    """chain -> token addresses DexScreener is currently promoting or has just profiled."""
    out: dict[str, set[str]] = defaultdict(set)
    for path in ("/token-boosts/top/v1", "/token-boosts/latest/v1", "/token-profiles/latest/v1",
                 "/community-takeovers/latest/v1"):
        try:
            for row in _get(path):
                if row.get("chainId") and row.get("tokenAddress"):
                    out[row["chainId"]].add(row["tokenAddress"])
        except (http.HttpError, OSError, ValueError) as e:
            log(f"dexscreener {path}: {str(e)[:120]}")
    gecko_candidates(out)
    return out


GECKO = "https://api.geckoterminal.com/api/v2/networks/{}/{}"
GECKO_CHAINS = {"solana": "solana", "base": "base"}   # gecko id -> dexscreener id


def gecko_candidates(out: dict[str, set[str]]) -> None:
    """GeckoTerminal's trending and new pools widen the universe (DexScreener's lists alone are ~80 tokens);
    only the addresses are used, DexScreener then supplies the names and numbers."""
    for net, chain in GECKO_CHAINS.items():
        for kind in ("trending_pools?duration=1h", "trending_pools?duration=24h", "new_pools"):
            try:
                raw = http.request("GET", GECKO.format(net, kind), timeout=25,
                                   headers={"accept": "application/json;version=20230302"})
                for pool in json.loads(raw).get("data", []):
                    tid = pool["relationships"]["base_token"]["data"]["id"]      # "solana_<address>"
                    out[chain].add(tid.split("_", 1)[1])
            except (http.HttpError, OSError, ValueError, KeyError) as e:
                log(f"gecko {net} {kind}: {str(e)[:100]}")
            time.sleep(12)    # the free plan allows only a handful of calls a minute


def token_rows(cands: dict[str, set[str]]) -> list[dict]:
    """One row per token (its deepest pair) with volume, move and age."""
    best: dict[tuple[str, str], dict] = {}
    now = time.time()
    for chain, addrs in cands.items():
        addrs = sorted(addrs)
        for i in range(0, len(addrs), 30):
            try:
                pairs = _get(f"/tokens/v1/{chain}/{','.join(addrs[i:i + 30])}")
            except (http.HttpError, OSError, ValueError) as e:
                log(f"dexscreener tokens {chain}: {str(e)[:120]}")
                continue
            for p in pairs or []:
                liq = (p.get("liquidity") or {}).get("usd") or 0
                key = (chain, p["baseToken"]["address"])
                if key not in best or liq > best[key]["liq"]:
                    created = (p.get("pairCreatedAt") or 0) / 1000
                    best[key] = {
                        "chain": chain, "address": key[1], "name": p["baseToken"]["name"],
                        "symbol": p["baseToken"]["symbol"], "liq": liq,
                        "vol_h1": (p.get("volume") or {}).get("h1") or 0,
                        "vol_h24": (p.get("volume") or {}).get("h24") or 0,
                        "chg_h1": (p.get("priceChange") or {}).get("h1") or 0,
                        "chg_h24": (p.get("priceChange") or {}).get("h24") or 0,
                        "age_h": (now - created) / 3600 if created else 9999,
                        "desc": "", "url": p.get("url", "")}
            time.sleep(0.25)
    return list(best.values())


def attach_descriptions(rows: list[dict]) -> None:
    """Boost rows carry the project's own one-line description; add it to the tokens we kept."""
    desc = {}
    for path in ("/token-boosts/top/v1", "/token-boosts/latest/v1"):
        try:
            for r in _get(path):
                if r.get("description"):
                    desc[(r["chainId"], r["tokenAddress"])] = r["description"]
        except (http.HttpError, OSError, ValueError):
            pass
    for r in rows:
        r["desc"] = desc.get((r["chain"], r["address"]), "")


def token_score(t: dict) -> float:
    """Volume this hour, nudged by the hour's move (capped, so a 100x spike doesn't own the list)."""
    return math.log1p(t["vol_h1"]) * (1 + max(-0.5, min(2.0, t["chg_h1"] / 100)))


def narratives(rows: list[dict]) -> list[dict]:
    live = [t for t in rows if t["age_h"] <= MAX_AGE_H and t["vol_h1"] >= MIN_VOL_H1 and t["liq"] >= MIN_LIQ]
    by_term: dict[str, list[dict]] = defaultdict(list)
    for t in live:
        for w in terms(t["name"], t["symbol"], EXTRA_STOP):
            by_term[w].append(t)
    out = []
    for w, toks in by_term.items():
        names = {re.sub(r"[^a-z0-9]", "", t["name"].lower()) for t in toks}
        if len(toks) >= MIN_TOKENS and len(names) >= MIN_NAMES:
            out.append({"term": w, "tokens": sorted(toks, key=token_score, reverse=True),
                        "score": sum(token_score(t) for t in toks)})
    out.sort(key=lambda n: n["score"], reverse=True)
    return out


def describe(n: dict) -> str:
    lines = []
    for t in n["tokens"][:6]:
        lines.append(f"{t['name']} (${t['symbol']}, {t['chain']}): ${t['vol_h1']:,.0f} traded in the last hour, "
                     f"{t['chg_h1']:+.0f}% 1h" + (f". Its own pitch: {t['desc'][:140]}" if t["desc"] else ""))
    return (f"a trend on DexScreener right now around \"{n['term']}\": {len(n['tokens'])} tokens with real volume. "
            + " | ".join(lines)
            + ". Make a NEW, original coin riffing on the shared idea, not a copy of any of these.")


# ---------------------------------------------------------------- design

def avoid_list(extra: list[str]) -> list[str]:
    names = list(extra)
    try:
        for r in bl.call("GET", "/token-launches", auth=False)[1].get("launches", []):
            names += [r.get("tokenName") or "", r.get("tokenSymbol") or ""]
    except Exception as e:  # the list is a courtesy, not a gate
        log(f"bankr recent list: {str(e)[:100]}")
    for r in bl.ledger():
        names += [r.get("name", ""), r.get("symbol", "")]
    return [n for n in names if n]


def design(n: dict) -> concept.Concept:
    openrouter.configure(env("GROQ_API_KEY"), (), env("POLLINATIONS_TOKEN"), ())
    extra = [t["name"] for t in n["tokens"]] + [t["symbol"] for t in n["tokens"]]
    return concept.from_idea(
        env("CLOUDFLARE_ACCOUNT_ID"), env("CLOUDFLARE_API_TOKEN"),
        env("DEVBOT_MODEL", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
        describe(n), avoid_list(extra), openrouter_key=env("OPENROUTER_API_KEY"))


def make_art(c: concept.Concept) -> bytes:
    return image.generate(env("CLOUDFLARE_ACCOUNT_ID"), env("CLOUDFLARE_API_TOKEN"), c.image_prompt,
                          fallback=env("IMAGE_FALLBACK", "1") != "0", pollinations_token=env("POLLINATIONS_TOKEN"))


def pin_art(art: bytes, symbol: str) -> str:
    ext, mime = image.kind(art)
    body, ctype = http.multipart({"network": "public"}, {"file": (f"{symbol.lower()}.{ext}", art, mime)})
    raw = http.request("POST", PINATA, body=body, timeout=120,
                       headers={"Authorization": f"Bearer {env('PINATA_JWT')}", "Content-Type": ctype})
    url = GATEWAY + json.loads(raw)["data"]["cid"]
    http.request("GET", url, timeout=40)   # Bankr fetches it server-side: make sure the gateway serves it
    return url


# ---------------------------------------------------------------- state and gates

def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"launches": [], "posts": [], "terms": {}}


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s))


def gates(s: dict) -> str:
    """'' if a launch may go out now, else why not."""
    now = time.time()
    if STOP.exists():
        return f"STOP file {STOP}"
    day = [t for t in s["launches"] if now - t < 86400]
    if len(day) >= int(env("BANKR_MAX_PER_DAY", "2")):
        return f"{len(day)} launches in the last 24 h (BANKR_MAX_PER_DAY)"
    if day and now - max(day) < 60 * float(env("BANKR_GAP_MIN", "30")):
        return f"last launch {(now - max(day)) / 60:.0f} min ago (BANKR_GAP_MIN)"
    posts = [t for t in s["posts"] if now - t < 86400]
    if len(posts) >= int(env("X_MAX_POSTS_PER_DAY", "20")):
        return "X_MAX_POSTS_PER_DAY"
    return ""


def notify(text: str) -> None:
    tok, chat = env("DEVBOT_TELEGRAM_TOKEN"), env("TELEGRAM_HOME_CHAT")
    if not (tok and chat):
        return
    try:
        http.post_json(f"https://api.telegram.org/bot{tok}/sendMessage",
                       {"chat_id": chat, "text": text[:3500], "disable_web_page_preview": True}, timeout=20)
    except Exception as e:
        log(f"telegram: {str(e)[:100]}")


def x_keys() -> social.XKeys | None:
    v = [env(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    return social.XKeys(*v) if all(v) else None


# ---------------------------------------------------------------- cycle

def eligible(s: dict, nars: list[dict]) -> list[dict]:
    """Best first, the narratives that share no term and no token with one we launched on in the
    last 24 h. Also expires both 24 h ledgers in place."""
    now = time.time()
    s["terms"] = {w: t for w, t in s["terms"].items() if now - t < 86400}
    s["addrs"] = {a: t for a, t in s.get("addrs", {}).items() if now - t < 86400}
    return [n for n in nars
            if n["term"] not in s["terms"] and not any(t["address"] in s["addrs"] for t in n["tokens"])]


def pick(s: dict, nars: list[dict]) -> dict | None:
    return next(iter(eligible(s, nars)), None)


DESIGN_TRIES = 2        # model re-asks per narrative, before moving down the list
DESIGN_NARRATIVES = 4   # narratives tried per cycle, so one dud reply is not a dud cycle


def design_any(nars: list[dict]) -> tuple[dict, concept.Concept]:
    """The first narrative that yields a usable concept.

    A small model often answers a two-word term with a concatenated ticker over SYMBOL_RE's 10
    characters, which rejects every candidate it returned. That is a dud reply, not a dud cycle, so
    re-ask and then try the next narrative instead of losing the scan."""
    last = ""
    for n in nars:
        for i in range(DESIGN_TRIES):
            try:
                return n, design(n)
            except concept.ConceptError as e:
                last = f"{n['term']}: {str(e)[:200]}"
                log(f"design {n['term']} try {i + 1}/{DESIGN_TRIES}: {str(e)[:160]}")
    raise RuntimeError(f"no usable concept from {len(nars)} narratives; last: {last}")


def cycle(live: bool) -> dict:
    s = load_state()
    why = gates(s)
    if why:
        log(f"skip: {why}")
        return {"skipped": why}
    cands = candidates()
    log(f"candidates: {sum(len(v) for v in cands.values())} tokens on {len(cands)} chains")
    rows = token_rows(cands)
    attach_descriptions(rows)
    nars = narratives(rows)
    log(f"{len(rows)} priced, {len(nars)} narratives: " + ", ".join(f"{n['term']}({len(n['tokens'])})" for n in nars[:6]))
    fresh = eligible(s, nars)
    if not fresh:
        log("no fresh narrative")
        return {"skipped": "no fresh narrative"}
    n, c = design_any(fresh[:DESIGN_NARRATIVES])
    log(f"concept: {c.name} ${c.symbol} :: {c.description}")
    art = make_art(c)
    img_url = pin_art(art, c.symbol) if env("PINATA_JWT") else ""
    row = {"ts": int(time.time()), "term": n["term"], "name": c.name, "symbol": c.symbol, "desc": c.description,
           "image": img_url, "tokens": [t["name"] for t in n["tokens"][:6]], "live": live}
    keys = x_keys()
    post_id = ""
    try:
        if live and keys:
            media = social.upload_image(keys, art, *image.kind(art))
            post_id = social.post(keys, social.launch_text(c.name, c.symbol, c.description, "", ""), media)
            s["posts"].append(time.time())
            row["post"] = post_id
            log(f"X post {social.post_url(post_id)}")
        handle = env("X_HANDLE")
        tweet = f"https://x.com/{handle}/status/{post_id}" if post_id and handle else (
            social.post_url(post_id) if post_id else "")
        body = {"tokenName": c.name, "tokenSymbol": c.symbol, "chain": "base", "provider": "doppler",
                "feeRecipient": {"type": "wallet", "value": bl.wallet()}, "simulateOnly": not live}
        # "image", not "imageUrl" — see the note in bankr_launch.launch(); the wrong spelling is
        # accepted with a 200 and dropped, so the token mints with no picture.
        for k, v in (("image", img_url), ("tweetUrl", tweet)):
            if v:
                body[k] = v
        code, resp = bl.call("POST", "/token-launches/deploy", body)
        row.update(code=code, resp=resp)
        if code not in (200, 201) or not resp.get("tokenAddress"):
            raise RuntimeError(f"bankr deploy {code}: {json.dumps(resp)[:300]}")
    except Exception as e:
        if post_id and keys:
            try:
                social.delete(keys, post_id)
                log("launch failed: X post deleted")
            except Exception as d:
                log(f"could not delete X post {post_id}: {d}")
        row["error"] = str(e)[:400]
        _write(row)
        save_state(s)
        notify(f"bankr pipeline FAILED ({c.name} ${c.symbol}): {str(e)[:400]}")
        raise
    token = resp["tokenAddress"]
    if live:
        s["launches"].append(time.time())
        s["terms"][n["term"]] = time.time()
        s.setdefault("addrs", {}).update({t["address"]: time.time() for t in n["tokens"]})
        bl.LEDGER.parent.mkdir(exist_ok=True)
        with bl.LEDGER.open("a") as f:
            f.write(json.dumps({"ts": row["ts"], "name": c.name, "symbol": c.symbol, "token": token,
                                "tx": resp.get("txHash"), "post": post_id, "resp": resp}) + "\n")
        if post_id and keys:
            try:
                social.post(keys, f"CA (Base): {token}", reply_to=post_id)
            except Exception as e:
                log(f"CA reply skipped: {'new account: X blocks addresses for 7 days' if social.address_blocked(e) else str(e)[:120]}")
    save_state(s)
    _write(row)
    msg = (f"{'LAUNCHED' if live else 'DRY (simulated)'} {c.name} ${c.symbol} on Base\n{token}\n"
           f"narrative: {n['term']} ({len(n['tokens'])} tokens)\n"
           + (f"https://basescan.org/token/{token}\n" if live else "")
           + (social.post_url(post_id) if post_id else ""))
    log(msg.replace("\n", " | "))
    notify(msg)
    return row


def _write(row: dict) -> None:
    ROWS.parent.mkdir(exist_ok=True)
    with ROWS.open("a") as f:
        f.write(json.dumps(row) + "\n")


def main() -> None:
    bl.load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["once", "loop", "scan"])
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    if a.cmd == "scan":
        rows = token_rows(candidates())
        attach_descriptions(rows)
        for n in narratives(rows)[:10]:
            print(f"{n['term']:<16} score {n['score']:6.1f}  " + ", ".join(t["symbol"] for t in n["tokens"][:8]))
        return
    if a.cmd == "once":
        cycle(a.live)
        return
    every = 60 * float(env("BANKR_CYCLE_MIN", "20"))
    while True:
        try:
            cycle(a.live)
        except Exception as e:
            log(f"cycle error: {str(e)[:300]}")
        time.sleep(every * random.uniform(0.9, 1.1))


if __name__ == "__main__":
    main()
