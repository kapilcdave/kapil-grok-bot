"""Turn measured narratives into one new coin: name, ticker, description, image prompt.

The narratives come from trends.py with their evidence (launch velocity,
traction, example coins, and the text of the X posts those coins link to). The
model is asked for the STORY behind a wave (the specific dog on the news, the
specific post), not the bare keyword. The code, not the model, picks the creative
ANGLE and the name/ticker CASING, so proposals vary and don't share one uniform
AI style. `distinct_from` rejects anything that copies an example coin.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass

from . import http, openrouter
from .trends import Narrative

ANGLES = {
    "counterpart": "the rival, opposite or nemesis of the story's main figure (dog->cat is the cliche; do better)",
    "mashup": "fuse the chosen story with the SECOND narrative in the list into one joke",
    "reaction": "the internet's punchline to the story: the reply everyone is posting, as a coin",
    "sidekick": "a specific overlooked character, object or place that is actually IN the story",
    "escalation": "the absurd next chapter of the story, one notch further",
    "insider": "a niche in-joke only people following this story will get",
}

# Casing of real pump.fun launches, measured on 3,000 coins (2026-10-13). Sampled per proposal so
# our coins don't all come out in tidy AI Title Case. Ticker equals the name ~32% of the time.
NAME_CASING = {"Title Case": 43, "ALL CAPS": 25, "all lowercase": 19, "as written": 13}
TICKER_CASING = {"ALL CAPS": 63, "Title": 20, "lowercase": 12, "as written": 5}

SYSTEM = """You name new pump.fun memecoins that ride a live, measured story as a distinct coin.

You get narratives with evidence: term groups, launch velocity, traction, example coins with their
descriptions, and the text of X posts those coins link to. A narrative is never just a keyword like
"dog": it is a specific story (a particular dog on the news, a particular post, a person's quote,
a product launch, an event). First work out the specific story from the evidence, then write THREE
different candidate coins for it using the ANGLE, best first.

A narrative with a `live` line is ONE coin that launched minutes ago and is being bought right now
(its first example; any others are copies already out). Then write a VARIANT of that coin. Your coin
reuses the live coin's own picture with ONE visible change, so someone buying it right now
recognises the same meme instantly, and the name tells them it is a DIFFERENT coin in that meme:
add a word or a role from the ANGLE ("Baby X", "X's Dog", "Mrs X", "X CEO", "Evil X", "X Jr").
Never the same name with a letter swapped, a number or "v2" added, or a lookalike spelling: a coin
that only works if people mistake it for the original is a scam. The ticker follows the name
(BABYX, MRSX), not a near-copy of the original's ticker. twist: the one visible change to the live
coin's picture that matches your name (a crown, a baby version, a hat, a sidekick next to it), a
short instruction for an image editor. Speed matters more than depth.

Rules for every candidate:
- Be specific. Real proper nouns are good: the actual person, pet, place, product, quote or event
  from the story. Generic words ("Sanctuary", "Sidekick", "Fund", "King", "Coin", "Zilla", "Bot")
  and the angle's own name are not. Never claim to be official, endorsed by, or affiliated with any
  person, brand or token. Never name a private individual (an ordinary account holder); public
  figures, companies, animals in the news and fictional characters are fine.
- Never reuse an example coin's exact name or ticker, never build the ticker out of a distinctive
  example ticker, and do not use anything in `avoid`.
- name: at most 32 characters, the way degens actually name coins: often one or two words, a quote,
  a nickname, a phrase from the post. Write it in the requested name_casing.
- symbol: 2-10 letters or digits, no $. A word or name people would type, often the name itself;
  NOT an abbreviation soup like SKSAN or UHYS. Write it in the requested ticker_casing.
- description: one or two playful sentences that reference the story. No price talk, no promises.
- image_prompt: a cartoon of YOUR coin's character or object, bold colours, plain background. No
  text or lettering in the image, no real logos, no photorealistic depiction of a real person.
- story: one sentence naming the specific story you found. why: one sentence of evidence."""

CANDIDATE = {
    "type": "object",
    "properties": {
        "narrative_index": {"type": "integer"},
        "story": {"type": "string"},
        "name": {"type": "string"},
        "symbol": {"type": "string"},
        "description": {"type": "string"},
        "image_prompt": {"type": "string"},
        "why": {"type": "string"},
        "twist": {"type": "string"},
    },
    "required": ["narrative_index", "name", "symbol", "description", "image_prompt", "why"],
}
SCHEMA = {
    "type": "object",
    "properties": {"candidates": {"type": "array", "items": CANDIDATE, "minItems": 1, "maxItems": 3}},
    "required": ["candidates"],
}

# The fast 8B model is ~6x cheaper but not on Cloudflare's JSON-mode list; if its reply can't be
# parsed, the call is retried once on the plain 8B model, which is.
DEFAULT_MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8-fast"
FALLBACK_MODEL = "@cf/meta/llama-3.1-8b-instruct"

SYMBOL_RE = re.compile(r"^[A-Za-z0-9]{2,10}$")


class ConceptError(RuntimeError):
    pass


class ServiceError(ConceptError):
    """The model service itself failed (quota, outage), as opposed to a reply we couldn't use."""


@dataclass(frozen=True)
class Concept:
    narrative: Narrative
    angle: str
    name: str
    symbol: str
    description: str
    image_prompt: str
    why: str
    story: str = ""
    twist: str = ""     # a live variant: the one change made to the source coin's picture


BOILERPLATE = re.compile(r"https?://\S+|\b(?:deployed|launched|created|made)\s+(?:using|with|on|via|by)\b(?:[^.!?\n]|\.(?=\S))*",
                         re.IGNORECASE)


def clean_description(text: str) -> str:
    """Launch tools stamp descriptions ("Deployed using https://j7tracker.io"); the model reads that
    as the story. Drop links and those stamps, keep what the creator actually wrote."""
    return re.sub(r"\s+", " ", BOILERPLATE.sub(" ", text or "")).strip(" .,-|")


def _norm(s: str) -> str:
    return re.sub(r"[\W_]", "", s.lower())  # any script: ワイ must not normalise to nothing


def distinct_from(name: str, symbol: str, others: list[tuple[str, str]], generic: frozenset[str] = frozenset()) -> bool:
    """False if name/ticker equals any (name, symbol) in `others`, or contains a DISTINCTIVE
    example ticker. Short tickers and the narrative's own words (CAT in a cat wave) are not
    distinctive: a cat coin may say cat; it may not be $SWORDCAT2."""
    n, s = _norm(name), _norm(symbol)
    if not n or not s:
        return False
    common = {_norm(g) for g in generic}
    for on, os_ in others:
        on, os_ = _norm(on), _norm(os_)
        if n == on or s == os_:
            return False
        if len(os_) >= 4 and os_ not in common and (os_ in s or os_ in n):
            return False
    return True


HOMOGLYPHS = str.maketrans("0134578$@|!", "oieastbsail")


def _fold(s: str) -> str:
    """Lowercase, digits read as the letters they imitate, punctuation and spaces dropped."""
    return _norm(s.lower().translate(HOMOGLYPHS))


def _edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def lookalike(name: str, symbol: str, others: list[tuple[str, str]]) -> bool:
    """True if the name or ticker could be mistaken for any (name, symbol) in `others`: the same
    after folding lookalike characters, the same plus a number or v2, or at most 2 edits apart.
    A riff that adds a word ("Baby Phasor" from "Phasor") is 4+ edits away and passes."""
    def close(a: str, b: str) -> bool:
        a, b = _fold(a), _fold(b)
        if not a or not b:
            return False
        a2, b2 = re.sub(r"(?:v?\d+|x)$", "", a), re.sub(r"(?:v?\d+|x)$", "", b)
        return a == b or a2 == b or b2 == a or (min(len(a), len(b)) >= 3 and _edits(a, b) <= 2)
    return any(close(x, y) for on, os_ in others for x in (name, symbol) for y in (on, os_))


def apply_casing(name: str, symbol: str, name_casing: str, ticker_casing: str) -> tuple[str, str]:
    """Enforce the sampled casing; the model is asked for it but small models drift to Title Case."""
    if name_casing == "ALL CAPS":
        name = name.upper()
    elif name_casing == "all lowercase":
        name = name.lower()
    if ticker_casing == "ALL CAPS":
        symbol = symbol.upper()
    elif ticker_casing == "lowercase":
        symbol = symbol.lower()
    elif ticker_casing == "Title":
        symbol = symbol[:1].upper() + symbol[1:].lower()
    return name, symbol


def _pick(weights: dict[str, int], rng: random.Random) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _parse(raw: bytes) -> dict:
    """Workers AI envelope {"result": {"response": ...}}; response is an object, a JSON string, or
    (from a model that ignores JSON mode) prose with the JSON inside it."""
    body = json.loads(raw)
    return _candidates((body.get("result") or {}).get("response", body.get("response")))


def _candidates(out) -> dict:
    """`out` is an object, a JSON string, or prose with the JSON inside it."""
    if isinstance(out, str):
        text = out
        try:
            out = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)
            try:
                out = json.loads(m.group(0)) if m else None
            except json.JSONDecodeError:
                out = None
            if out is None:
                raise ConceptError(f"model returned non-JSON: {text[:200]}") from None
    if isinstance(out, dict) and "candidates" not in out and all(k in out for k in CANDIDATE["required"]):
        out = {"candidates": [out]}  # a model that answered with a single candidate
    if not isinstance(out, dict) or not isinstance(out.get("candidates"), list):
        raise ConceptError(f"model output missing candidates: {str(out)[:200]}")
    cands = [c for c in out["candidates"] if isinstance(c, dict) and all(k in c for k in CANDIDATE["required"])]
    if not cands:
        raise ConceptError(f"model output missing fields: {str(out)[:200]}")
    return {"candidates": cands}


def _ask(account_id: str, api_token: str, model: str, prompt: str, system: str = "") -> dict:
    """One Workers AI chat call in JSON mode."""
    try:
        raw = http.post_json(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}",
            {"messages": [{"role": "system", "content": system or SYSTEM}, {"role": "user", "content": prompt}],
             "response_format": {"type": "json_schema", "json_schema": SCHEMA},
             "max_tokens": 900, "temperature": 0.95},
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=60,
        )
    except http.HttpError as e:
        raise ServiceError(f"Workers AI ({model}): {e}") from None
    return _parse(raw)


def _ask_openrouter(key: str, models: list[str], prompt: str, system: str = "") -> dict:
    """First model that answers with usable JSON; free models are often rate-limited upstream."""
    errors = []
    for model in models:
        try:
            # Room for the reasoning some models do before answering.
            content = openrouter.text_json(key, model, system or SYSTEM, prompt, SCHEMA, max_tokens=4000,
                                           temperature=0.95)
            return _candidates(content)
        except (ConceptError, http.HttpError, OSError, RuntimeError, KeyError, ValueError) as e:
            errors.append(f"{model}: {str(e)[:120]}")
    raise ServiceError("OpenRouter: " + "; ".join(errors))


def _ask_chain(providers: list[openrouter.Provider], prompt: str, system: str, errors: list[str]) -> dict | None:
    """First answer with usable JSON from `providers` (openrouter.FIRST or LAST), else None."""
    for p in providers:
        for model in p.models:
            try:
                return _candidates(openrouter.text_json(p.key, model, system or SYSTEM, prompt, SCHEMA,
                                                        max_tokens=4000, temperature=0.95, provider=p))
            except (ConceptError, http.HttpError, OSError, RuntimeError, KeyError, ValueError) as e:
                errors.append(f"{p.name} {model}: {str(e)[:120]}")
    return None


def _ask_any(account_id: str, api_token: str, model: str, prompt: str, system: str, openrouter_key: str,
             openrouter_models: list[str] | None, *, retry_fallback: bool) -> dict:
    """openrouter.FIRST (Groq), then Workers AI and OpenRouter as before, then openrouter.LAST."""
    errors: list[str] = []
    data = _ask_chain(openrouter.FIRST, prompt, system, errors)
    if data is not None:
        return data
    try:
        try:
            return _ask(account_id, api_token, model, prompt, system)
        except ServiceError:
            raise  # quota or outage: the other Workers AI model shares it, so go straight to OpenRouter
        except ConceptError:
            if not retry_fallback or model == FALLBACK_MODEL:
                raise
            return _ask(account_id, api_token, FALLBACK_MODEL, prompt, system)  # listed as JSON-mode capable
    except ConceptError as e:
        errors.append(str(e)[:300])
        if openrouter_key:
            try:
                return _ask_openrouter(openrouter_key, openrouter_models or openrouter.TEXT_MODELS, prompt, system)
            except ConceptError as e2:
                errors.append(str(e2)[:600])
        elif not openrouter.LAST and not openrouter.FIRST:
            raise
    data = _ask_chain(openrouter.LAST, prompt, system, errors)
    if data is None:
        raise ServiceError("; ".join(errors))
    return data


def generate(account_id: str, api_token: str, model: str, narratives: list[Narrative], angle: str,
             avoid: list[str], *, posts: dict[str, list[str]] | None = None,
             rng: random.Random | None = None, openrouter_key: str = "",
             openrouter_models: list[str] | None = None) -> Concept:
    """`posts` maps a narrative's first term to the text of X posts its coins link to."""
    if not narratives:
        raise ConceptError("no live narratives yet")
    rng = rng or random.Random()
    name_casing, ticker_casing = _pick(NAME_CASING, rng), _pick(TICKER_CASING, rng)
    posts = posts or {}
    listing = [
        {"index": i, "live": n.live,
         "examples": [{"name": c.name, "symbol": c.symbol, "peak_mcap_usd": round(c.peak_usd),
                       "description": clean_description(c.description)[:140]} for c in n.examples[:4]],
         "x_posts": posts.get(n.terms[0], [])[:3]}
        if n.live else
        {"index": i, "terms": n.terms, "launches_last_hour": n.launches_1h,
         "velocity_vs_baseline": round(n.lift, 1) if n.lift is not None else "unknown yet",
         "coins_6h": n.coins_6h, "creators": n.creators, "coins_that_4x": n.winners,
         "examples": [{"name": c.name, "symbol": c.symbol, "peak_mcap_usd": round(c.peak_usd),
                       "description": clean_description(c.description)[:140]} for c in n.examples[:4]],
         "x_posts": posts.get(n.terms[0], [])[:3]}
        for i, n in enumerate(narratives)
    ]
    prompt = json.dumps({"angle": angle, "angle_means": ANGLES[angle], "name_casing": name_casing,
                         "ticker_casing": ticker_casing, "narratives": listing, "avoid": avoid[-60:]},
                        ensure_ascii=False)
    data = _ask_any(account_id, api_token, model, prompt, SYSTEM, openrouter_key, openrouter_models,
                    retry_fallback=True)

    examples = [(c.name, c.symbol) for n in narratives for c in n.examples]
    generic = frozenset(t for n in narratives for t in n.terms)
    avoided = {_norm(a) for a in avoid}
    rejected = []
    for cand in data["candidates"]:  # best first: take the first one that passes every check
        idx = cand["narrative_index"]
        if not isinstance(idx, int) or not 0 <= idx < len(narratives):
            idx = 0
        name = str(cand["name"]).strip()[:32]
        symbol = str(cand["symbol"]).strip().lstrip("$")
        name, symbol = apply_casing(name, symbol, name_casing, ticker_casing)
        live = narratives[idx].live
        if not SYMBOL_RE.match(symbol):
            rejected.append(f"{name} / {symbol!r}: bad ticker")
        elif live and lookalike(name, symbol, examples):
            rejected.append(f"{name} / {symbol}: a lookalike of an example coin")
        elif live and not str(cand.get("twist") or "").strip():
            rejected.append(f"{name} / {symbol}: no twist for the live coin's picture")
        elif not live and not distinct_from(name, symbol, examples, generic):
            rejected.append(f"{name} / {symbol}: copies an example coin")
        elif _norm(symbol) in avoided or _norm(name) in avoided:
            rejected.append(f"{name} / {symbol}: on the avoid list")
        elif angle in _norm(name) or angle in _norm(symbol):
            rejected.append(f"{name} / {symbol}: names the angle")
        else:
            return Concept(narratives[idx], angle, name, symbol, str(cand["description"]).strip(),
                           str(cand["image_prompt"]).strip(), str(cand["why"]).strip(),
                           str(cand.get("story", "")).strip(), str(cand.get("twist") or "").strip() if live else "")
    raise ConceptError("; ".join(rejected))


IDEA_SYSTEM = """You turn the operator's own memecoin idea into a pump.fun coin. The idea is usually a
moment they saw (a reel, a post, a clip): find the single funniest, most specific detail in it and
name the coin after THAT, the way degens name coins: a nickname, a phrase, a character.

Brands: if the idea involves a brand or company, the coin must never be named after the brand, use
its name as the ticker, use its logo, or suggest it is official or affiliated. Name the moment, not
the trademark (a jacket water bounces off -> the drop that can't get in, the bead, the jacket guy).

People: if the idea is someone's X post, the coin is about the post's moment, never presented as the
poster's own coin or as endorsed by them. Name the joke, not the person; never name a private
individual. If an "obvious name" is given, other devs are already launching it: make yours
recognisably about the same post but different from it.

Write THREE candidates, best first, each with: narrative_index 0; story (one sentence: the moment);
name (at most 32 characters, in the requested name_casing); symbol (2-10 letters or digits, no $, a
word people would type, in the requested ticker_casing); description (one or two playful sentences
about the moment, no price talk, no promises); image_prompt (a cartoon of the coin's character or
object, bold colours, plain background, no text or lettering, no logos, no photorealistic real
person); why (one sentence: why this is funny)."""


def from_idea(account_id: str, api_token: str, model: str, idea: str, avoid: list[str], *,
              rng: random.Random | None = None, openrouter_key: str = "",
              openrouter_models: list[str] | None = None) -> Concept:
    """A coin from the operator's own idea (/coin). Same casing, ticker and avoid checks as a
    narrative concept; the narrative is just the idea."""
    idea = re.sub(r"\s+", " ", idea).strip()
    if len(idea) < 3:
        raise ConceptError("describe the idea: /coin <what you saw and why it's funny>")
    rng = rng or random.Random()
    name_casing, ticker_casing = _pick(NAME_CASING, rng), _pick(TICKER_CASING, rng)
    prompt = json.dumps({"idea": idea[:600], "name_casing": name_casing, "ticker_casing": ticker_casing,
                         "avoid": avoid[-60:]}, ensure_ascii=False)
    data = _ask_any(account_id, api_token, model, prompt, IDEA_SYSTEM, openrouter_key, openrouter_models,
                    retry_fallback=False)
    narrative = Narrative(terms=[idea[:40].lower()], score=0, launches_1h=0, base_per_h=0.0, traction=0.0,
                          coins_6h=0, creators=0, winners=0, live=f"your idea: {idea[:300]}")
    avoided = {_norm(a) for a in avoid}
    rejected = []
    for cand in data["candidates"]:
        name = str(cand["name"]).strip()[:32]
        symbol = str(cand["symbol"]).strip().lstrip("$")
        name, symbol = apply_casing(name, symbol, name_casing, ticker_casing)
        if not SYMBOL_RE.match(symbol):
            rejected.append(f"{name} / {symbol!r}: bad ticker")
        elif _norm(symbol) in avoided or _norm(name) in avoided:
            rejected.append(f"{name} / {symbol}: on the avoid list")
        else:
            return Concept(narrative, "idea", name, symbol, str(cand["description"]).strip(),
                           str(cand["image_prompt"]).strip(), str(cand["why"]).strip(),
                           str(cand.get("story", "")).strip())
    raise ConceptError("; ".join(rejected))


def from_post(account_id: str, api_token: str, model: str, handle: str, followers: int, text: str,
              obvious: str, avoid: list[str], *, rng: random.Random | None = None, openrouter_key: str = "",
              openrouter_models: list[str] | None = None) -> Concept:
    """A coin off someone's X post (the j7tracker feed): its own name, never the obvious one."""
    idea = f'a post by @{handle} ({followers:,} followers) on X, seconds old: "{text[:400]}"'
    if obvious:
        idea += f'. Obvious name: "{obvious}"'
    c = from_idea(account_id, api_token, model, idea, avoid + ([obvious] if obvious else []), rng=rng,
                  openrouter_key=openrouter_key, openrouter_models=openrouter_models)
    c.narrative.live = f"@{handle}'s post: {text[:200]}"
    return c
