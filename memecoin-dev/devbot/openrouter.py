"""OpenAI-compatible text providers for concepts and drafts, tried in order around Workers AI (whose
free plan stops at 10,000 neurons per UTC day and answers HTTP 429 until midnight UTC):

    FIRST       Groq (GROQ_API_KEY): gpt-oss-120b answered a concept-sized JSON reply in 0.3 s
                (2026-10-14; free plan 1,000 requests a day, 8,000 tokens a minute)
    Workers AI  and OpenRouter's free models (OPENROUTER_API_KEY), as before
    LAST        Pollinations (POLLINATIONS_TOKEN): its `openai` model answers on the key's free
                allowance in ~4 s; every other model there needed paid credit (2026-10-14)

OpenRouter: free models (":free") are rate-limited upstream without warning, so several are tried in
order, and on 2026-10-14 the key hit OpenRouter's free-models-per-day cap. Tested 2026-10-13 with the
concept prompt: nemotron-3-super answered in ~21 s; both Gemma 4 models were rate-limited at the
time; nemotron-3.5-lightning, dots-3-note, apodex-1.1-mini and inkling-small returned no JSON, a 502,
a 400 or a 403. OpenRouter has no free image model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import http

API = "https://openrouter.ai/api/v1/chat/completions"
TEXT_MODELS = ["nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free",
               "google/gemma-4-26b-a4b-it:free"]
GROQ_API = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["openai/gpt-oss-120b"]
POLLINATIONS_API = "https://gen.pollinations.ai/v1/chat/completions"
POLLINATIONS_MODELS = ["openai"]


@dataclass(frozen=True)
class Provider:
    name: str
    url: str
    key: str
    models: tuple[str, ...]
    extra: dict = field(default_factory=dict)  # provider-specific request fields
    max_tokens: int = 0                        # cap on the caller's max_tokens (0 = none)


# Speed over depth: nemotron-3-super took 25-78 s reasoning first, 3.9 s without (2026-10-13).
OPENROUTER_EXTRA = {"reasoning": {"enabled": False}}
# Groq's free plan allows 8,000 tokens a minute, so one reply is capped well under it.
GROQ_EXTRA = {"reasoning_effort": "low", "include_reasoning": False}

FIRST: list[Provider] = []   # tried before Workers AI and OpenRouter
LAST: list[Provider] = []    # tried once those failed


def configure(groq_key: str = "", groq_models: tuple[str, ...] = (), pollinations_token: str = "",
              pollinations_models: tuple[str, ...] = ()) -> None:
    """Set FIRST and LAST from the bot's config (the bot calls this once at start)."""
    FIRST[:] = [Provider("Groq", GROQ_API, groq_key, tuple(groq_models or GROQ_MODELS), GROQ_EXTRA, 2000)] \
        if groq_key else []
    LAST[:] = [Provider("Pollinations", POLLINATIONS_API, pollinations_token,
                        tuple(pollinations_models or POLLINATIONS_MODELS))] if pollinations_token else []


def _chat(key: str, body: dict, timeout: float, url: str = API, name: str = "OpenRouter") -> dict:
    headers = {"Authorization": f"Bearer {key}"}
    if url == API:
        headers["X-Title"] = "devbot"
    out = json.loads(http.post_json(url, body, headers=headers, timeout=timeout))
    if not out.get("choices"):
        raise RuntimeError(f"{name} returned no choices: {str(out)[:300]}")
    return out["choices"][0]["message"]


def text_json(key: str, model: str, system: str, prompt: str, schema: dict, *,
              max_tokens: int, temperature: float, provider: Provider | None = None) -> str:
    """The reply's content: a JSON string matching `schema` (callers still parse defensively).
    `provider` None means OpenRouter with `key`."""
    p = provider or Provider("OpenRouter", API, key, (), OPENROUTER_EXTRA)
    msg = _chat(p.key, {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}},
        "max_tokens": min(max_tokens, p.max_tokens) if p.max_tokens else max_tokens, "temperature": temperature,
        **p.extra,
    }, timeout=60, url=p.url, name=p.name)
    return msg.get("content") or ""
