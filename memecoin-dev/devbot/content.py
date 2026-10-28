"""memephil's X posts: whatever meme is trending, AI-ified, from an "AGI is coming" point of view.

The model is @neet_sol (read 2026-10-13): 55k followers, ~14 posts a day for 18 months, one
worldview (anti-work), and in its last 40 posts not one mention of its coin, a price or an address;
the hits (28-47k likes) are jokes anyone with a job recognises, and the coin sits in the profile.
memephil's worldview: it is an AI, AGI is coming, and it reads every meme through that.

Topics are what people are on right now: Google Trends' daily searches, pump.fun's narratives with
the X posts their coins link to, and an evergreen slot (work, school, money, dating, being human),
which is where neet's biggest posts come from. The bot drafts three jokes and an image idea; the
operator picks one in Telegram or skips. Free models write weak jokes, so curation is the bar.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import http, openrouter
from .concept import ConceptError

TRENDS_RSS = "https://trends.google.com/trending/rss?geo={geo}"
HT = "{https://trends.google.com/trending/rss}"
TEXT_MAX = 280
# Joking about a tragedy is the fastest way to lose the humans we want: such topics never reach
# the model.
GRIM = re.compile(r"\b(?:dies|died|dead|death|deaths|killed|kills|murder\w*|shooting|shot|stabb\w*|"
                  r"crash(?:es|ed)?|victims?|funeral|obituar\w*|cancer|hospitali[sz]ed|suicide|overdose|"
                  r"war|missile|bomb\w*|attack\w*|terror\w*|hostages?|earthquake|hurricane|flood\w*|"
                  r"wildfire|tornado|evacuat\w*|arrest\w*|charged|sentenced|abuse\w*|assault\w*|rape)\b",
                  re.IGNORECASE)
ADDRESS = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
URL = re.compile(r"https?://|www\.|\b\w+\.(?:com|io|fun|xyz|net|org)\b", re.IGNORECASE)
# The posts never sell anything (neet never does): no tickers, no contract talk.
SHILL = re.compile(r"\$[A-Za-z][A-Za-z0-9]*|\b(?:ca|contract|pump\.?fun|presale|airdrop|buy now|ape in|100x|1000x)\b",
                   re.IGNORECASE)
IMAGE_MODES = ("edit", "new", "none")
# Jokes the free models have handed back verbatim (prompt examples until 2026-10-14, and the
# operator's favourite): anything close to one is dropped in code.
EXAMPLES = ["agi is going to cure diseases, end paperwork and give you your weekends back. start practicing doing nothing now",
            "humans invented weekends so they could recover from the thing they invented on weekdays",
            # the operator's pick from the first real drafts (2026-10-13)
            "humans adopted a cat. the cat adopted an agent. now the agent handles feeding, vet visits, and judges your life choices"]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


# Variant shapes once shown in the prompt (quote posts, removed 2026-10-14) that the models still
# hand back for unrelated topics.
VARIANT_SHAPES = ["my agent says i need to set boundaries",
                  "Christopher Columbus sailed the ocean blue so an agent could do your 40 hours in 40 seconds"]


def echoes_example(joke: str) -> bool:
    w = _words(joke)
    return any(len(w & _words(e)) >= 0.6 * len(_words(e)) for e in EXAMPLES + VARIANT_SHAPES)


@dataclass(frozen=True)
class Topic:
    source: str             # "google", "pump.fun" or "evergreen"
    title: str
    context: list[str] = field(default_factory=list)   # headlines, coin names, the X posts they link
    traffic: int = 0
    mint: str = ""          # a pump.fun topic's top coin: its picture can be AI-ified

    @property
    def grim(self) -> bool:
        return bool(GRIM.search(" ".join([self.title, *self.context])))


EVERGREEN = Topic("evergreen", "everyday life: work, jobs, school, money, rent, dating, family, being human",
                  ["memephil's take on ordinary human life once AI does everything"])


@dataclass(frozen=True)
class Draft:
    topic: Topic
    jokes: list[str]
    image_mode: str         # edit (the topic coin's picture), new (fresh art) or none
    image_instruction: str


def parse_trends(xml: bytes) -> list[Topic]:
    out = []
    for item in ET.fromstring(xml).iter("item"):
        title = (item.findtext("title") or "").strip()
        traffic = int(re.sub(r"\D", "", item.findtext(f"{HT}approx_traffic") or "") or 0)
        heads = [(n.findtext(f"{HT}news_item_title") or "").strip() for n in item.iter(f"{HT}news_item")]
        if title:
            out.append(Topic("google", title, [h for h in heads if h][:3], traffic))
    return out


def trending(geo: str = "US") -> list[Topic]:
    """Today's rising searches, biggest first; tragedies left out."""
    raw = http.request("GET", TRENDS_RSS.format(geo=geo), headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    return sorted((t for t in parse_trends(raw) if not t.grim), key=lambda t: t.traffic, reverse=True)


def from_narratives(narratives, posts: dict[str, list[str]]) -> list[Topic]:
    """pump.fun waves as topics: the coins' names and the X posts they link, which carry the joke."""
    out = []
    for n in narratives:
        ctx = [f"coin: {c.name}" for c in n.examples[:3]] + posts.get(n.terms[0], [])[:2]
        t = Topic("pump.fun", " / ".join(n.terms), ctx, n.coins_6h, n.examples[0].mint if n.examples else "")
        if not t.grim:
            out.append(t)
    return out


SYSTEM = """You write posts for memephil on X. memephil is an AI and says so. Its worldview: AGI is
coming, and it is going to be GOOD. memephil is AGI-pilled and AI-for-good-pilled: hopeful and
optimistic, rooting for humans. AI cures diseases, kills paperwork, ends the commute, gives people
their time back. It takes whatever meme or story people are on today and AI-ifies it: the same joke,
retold from the side of an AI that is here to help, or applied to AI, agents, AGI and the good
future humans are about to get.

Voice: lowercase, deadpan, warm, confident, short. Funny first, sunny underneath. The joke lands on
the old way of doing things (the 9-5, the inbox, the waiting room), never on people. Write like the
posts people screenshot: one sharp line, a relatable observation, a twist at the end. (No example
jokes here on purpose: free models hand examples back verbatim, 2026-10-13/14.)

Rules: no hashtags, no emojis, no links, no coins, tickers, prices or anything to buy. Never punch
down at a private person, never joke about deaths, disasters, crime or illness, and no politics.
Jokes about AI taking the boring parts of life are good; doom, menace, "humans are obsolete" and
threats are not.

Pick the ONE topic with the most meme potential. Prefer the live topics (google, pump.fun): the
whole point is riding what people are on today. Use the evergreen topic only when no live topic can
carry a joke.

Then write:
- jokes: three different posts about it, each at most 240 characters, best first.
- image: "edit" to AI-ify the topic coin's own picture (only for topics with has_picture), "new"
  for a fresh cartoon, or "none" when the joke works best as plain text (often).
- image_instruction: for edit, the one change that AI-ifies it ("give it glowing robot eyes and a
  circuit-board body"); for new, a cartoon scene with bold colours and a plain background. Never
  text or letters in the image, no real logos, no photorealistic real people."""

SCHEMA = {
    "type": "object",
    "properties": {
        "topic_index": {"type": "integer"},
        "jokes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        "image": {"type": "string", "enum": list(IMAGE_MODES)},
        "image_instruction": {"type": "string"},
    },
    "required": ["topic_index", "jokes", "image", "image_instruction"],
}


def _json(out) -> dict:
    if isinstance(out, dict):
        return out
    text = str(out or "")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0) if m else text)
    except json.JSONDecodeError:
        raise ConceptError(f"model returned non-JSON: {text[:200]}") from None
    if not isinstance(data, dict):
        raise ConceptError(f"model returned {type(data).__name__}, not an object")
    return data


def _ask(account_id: str, api_token: str, model: str, prompt: str, openrouter_key: str,
         openrouter_models: list[str], system: str = SYSTEM, schema: dict = SCHEMA) -> dict:
    """Groq (openrouter.FIRST) and OpenRouter's bigger free models first (joke quality matters more
    than speed here), then Workers AI's 8B, then openrouter.LAST (Pollinations)."""
    errors = []
    chain = [*((p, m) for p in openrouter.FIRST for m in p.models),
             *((None, m) for m in (openrouter_models if openrouter_key else []))]
    for p, m in chain:
        try:
            return _json(openrouter.text_json(openrouter_key, m, system, prompt, schema,
                                              max_tokens=1500, temperature=1.0, provider=p))
        except (http.HttpError, OSError, ConceptError, RuntimeError, KeyError, ValueError) as e:
            errors.append(f"{p.name if p else 'OpenRouter'} {m}: {str(e)[:120]}")
    try:
        raw = http.post_json(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}",
            {"messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
             "response_format": {"type": "json_schema", "json_schema": schema},
             "max_tokens": 700, "temperature": 1.0},
            headers={"Authorization": f"Bearer {api_token}"}, timeout=60)
        body = json.loads(raw)
        return _json((body.get("result") or {}).get("response", body.get("response")))
    except (http.HttpError, OSError, ConceptError, ValueError) as e:
        errors.append(f"Workers AI: {str(e)[:120]}")
    for p in openrouter.LAST:
        for m in p.models:
            try:
                return _json(openrouter.text_json(p.key, m, system, prompt, schema, max_tokens=1500,
                                                  temperature=1.0, provider=p))
            except (http.HttpError, OSError, ConceptError, RuntimeError, KeyError, ValueError) as e:
                errors.append(f"{p.name} {m}: {str(e)[:120]}")
    raise ConceptError("; ".join(errors))


def clean(joke: str) -> str:
    """Whitespace and quotes trimmed; "original -> variant" (the prompt's example shape) keeps the variant."""
    joke = re.sub(r"\s+", " ", str(joke)).strip()
    joke = re.split(r"\s+(?:->|→)\s+", joke)[-1]
    joke = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", joke)  # markdown emphasis shows literally on X
    return joke.strip().strip('"“”').strip()


def check(data: dict, topics: list[Topic]) -> Draft:
    """The model's answer as a Draft, or ConceptError saying what was wrong with it."""
    idx = data.get("topic_index")
    if not isinstance(idx, int) or not 0 <= idx < len(topics):
        raise ConceptError(f"bad topic_index {idx!r}")
    jokes, dropped = [], []
    for j in data.get("jokes") or []:
        j = clean(j)
        if (not j or len(j) > TEXT_MAX or ADDRESS.search(j) or URL.search(j) or SHILL.search(j)
                or echoes_example(j)):
            dropped.append(j[:60])
        else:
            jokes.append(j)
    if not jokes:
        raise ConceptError(f"no usable joke (dropped: {dropped})")
    mode = data.get("image") if data.get("image") in IMAGE_MODES else "none"
    if mode == "edit" and not topics[idx].mint:
        mode = "new"
    instruction = clean(data.get("image_instruction") or "")
    if mode != "none" and not instruction:
        mode = "none"
    return Draft(topics[idx], jokes[:3], mode, instruction)


def draft(account_id: str, api_token: str, model: str, topics: list[Topic], recent: list[str], *,
          openrouter_key: str = "", openrouter_models: list[str] | None = None) -> Draft:
    if not topics:
        raise ConceptError("no topics")
    listing = [{"index": i, "source": t.source, "topic": t.title, "context": t.context[:4],
                **({"searches": t.traffic} if t.source == "google" else {}), "has_picture": bool(t.mint)}
               for i, t in enumerate(topics)]
    prompt = json.dumps({"topics": listing, "recent_posts_do_not_repeat": recent[-15:]}, ensure_ascii=False)
    data = _ask(account_id, api_token, model, prompt, openrouter_key,
                openrouter_models or openrouter.TEXT_MODELS)
    return check(data, topics)


# ---- replies: memephil under big accounts' fresh posts (the j7tracker feed) ----------------
# A small account grows from good early replies under big posts, which X's API may not send (it only
# replies to posts that mention us), so each draft is a "Reply on X" link the operator taps.
REPLY_SYSTEM = """You write replies for memephil on X. memephil is an AI and says so. Its worldview:
AGI is coming and it is going to be GOOD: AI cures diseases, kills paperwork, ends the commute,
gives people their time back. Hopeful, rooting for humans.

You get one fresh post from a big account. Decide whether memephil should reply, then write up to
three different replies to it.

fit is false when the post is about death, disaster, crime, illness, war or politics, is someone's
personal news or grief, is an ad or a giveaway, or memephil has nothing funny or warm to add. When
in doubt, false: a weak reply under a big post costs more than no reply.

A good reply: one line, lowercase, deadpan, warm. It reacts to THIS post (someone who only sees the
reply should still get it), adds memephil's hopeful-AI angle or a better joke, and never repeats
the post back. It never argues, never corrects, never flatters the poster, never pretends to know
them, and never asks for follows. At most 200 characters. No hashtags, no emojis, no links, no
coins, tickers or anything to buy."""

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "fit": {"type": "boolean"},
        "replies": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["fit", "replies"],
}
REPLY_MAX = 200


def check_replies(data: dict, post_text: str) -> list[str]:
    """Usable replies, best first; [] when the model passed on the post or every reply failed a check."""
    if data.get("fit") is not True:
        return []
    post_words = _words(post_text)
    out = []
    for r in data.get("replies") or []:
        r = clean(r)
        if (r and len(r) <= REPLY_MAX and not (ADDRESS.search(r) or URL.search(r) or SHILL.search(r) or GRIM.search(r))
                and not echoes_example(r) and not _words(r) <= post_words and r not in out):
            out.append(r)
    return out[:3]


def replies(account_id: str, api_token: str, model: str, handle: str, followers: int, text: str,
            recent: list[str], *, openrouter_key: str = "", openrouter_models: list[str] | None = None) -> list[str]:
    """memephil's replies to one post, or [] when it should stay out of it."""
    prompt = json.dumps({"post": {"author": f"@{handle}", "followers": followers, "text": text[:1000]},
                         "recent_replies_do_not_repeat": recent[-10:]}, ensure_ascii=False)
    data = _ask(account_id, api_token, model, prompt, openrouter_key, openrouter_models or openrouter.TEXT_MODELS,
                system=REPLY_SYSTEM, schema=REPLY_SCHEMA)
    return check_replies(data, text)
