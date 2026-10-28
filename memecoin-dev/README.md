# devbot: memecoin dev pipeline

A headless Telegram bot on the Iowa e2-micro, running next to Hermes:

1. **What to build on** (`trends.py`): `/propose` scans pump.fun live for coins launched in the last `FRESH_MINUTES` (5) that at least `HOT_MIN_BUYERS` (8) outside wallets are buying (creator and launch-second bundle excluded), and designs a variant of the hottest. Measured caveat: across ~66k launches, copying a hot coin was NOT associated with more human buyers and went with ~11% less volume (`research/RESULT-human-attention-levers-20261013.md`). The older 6-hour narrative scoring below still collects in the background:
   every 2 minutes the bot records pump.fun's newest launches and most recently traded coins in a 48 h SQLite window. A **narrative** is a group of terms that recurs across coins, such as `[rex / luna / xpets]` or `[ai / bob / art]`. Each one is scored on two things:
   - **launch velocity**: launches in the last hour compared with the term's own 6-hour baseline.
   - **traction**: how far its coins pumped, capped at 64x per coin and halved for every 2 hours of coin age.

   A narrative needs at least 3 coins from at least 2 creators, so one spammer can't make a trend. `/trends` shows the evidence.
2. **Concept** (`concept.py`): a wave is a story, not a keyword. Each narrative carries its example coins, their descriptions and the text of the X posts they link to, read through X's public oEmbed endpoint and cached. The model (Groq's gpt-oss-120b when `GROQ_API_KEY` is set, ~1 s; then Llama 3.1 8B fast on Workers AI, retried once on plain 8B if its JSON doesn't parse; when Workers AI is out of its free 10,000 daily neurons, free OpenRouter models with reasoning off; last, Pollinations' free `openai` model when `POLLINATIONS_TOKEN` is set) first names the specific story, then writes 3 candidates using a rotating **angle**. Real proper nouns are encouraged; claiming affiliation and naming private individuals are not. Name and ticker **casing** is sampled from 3,000 real launches (names: 43% Title Case, 25% ALL CAPS, 19% lowercase; tickers: 63% ALL CAPS) so coins don't share one tidy AI style. The code rejects reuse of an example's name or ticker, or of any token already trading on Solana. A **live variant** (a coin launched minutes ago that people are buying) is a riff, not a copy: the name adds a word or role ("Baby X", "X's Dog", "Mrs X") and the code rejects lookalikes (a swapped letter, `0` for `o`, an added number or v2, anything within 2 edits of the original's name or ticker).
3. **Image** (`image.py`): original 512x512 art from Cloudflare Workers AI, FLUX.1 schnell, 4 steps (about 43 neurons), or Pollinations (`POLLINATIONS_TOKEN`) when Workers AI fails. A live variant's art is the source coin's own picture with the one change the concept names (`twist`), edited by FLUX.2 klein 4B on Workers AI (about 31 neurons) or, with a key, Pollinations' copy of it; if both fail the proposal gets fresh art and its caption says so. The art and the mint address are prepared at proposal time, so a Launch click only posts, pins the metadata and sends. The prompt describes the art only, so no words get drawn into it.
4. **Launch** (`pump.py`): image and metadata go to IPFS through Pinata. PumpPortal builds the create transaction, and the bot signs it locally with the dev wallet and a fresh mint key. The key never leaves the VM.
5. **Track** (`ledger.py`, `/stats`, `/claim`): every launch, spend and fee claim is recorded in SQLite.
6. **Website** (`site/`, optional): a Cloudflare Worker that serves one page per coin at `<WEBSITE_BASE>/<mint>`. Each page has the art, the description with the disclosure, and links to pump.fun, the chart and Solscan. It renders from pump.fun's API and shows only coins created by the dev wallet, so the domain can't be used to dress up anyone else's token. The bot puts that link in each coin's `website` field. The X field is only ever our own launch post (step 7) and Telegram stays empty: the bot never links someone else's socials.
7. **X post** (`social.py`, optional): with X keys set, each launch is posted from your X account, with the coin's art, before the coin is created. The post (name, ticker, description, contract address once the account is past X's 7-day crypto-address rule, no link) becomes the coin's `twitter` field. Linking an X post is the one launch choice that survived a within-creator test for human buyers. If the launch fails, the post is deleted. Capped at `X_MAX_POSTS_PER_DAY`.
8. **Audience** (`audience.py`, `/humans`): each buyer of our last 5 coins classified from one wallet-history call: launch-block sniper, bot (100+ txs in 6 h), fresh wallet (nothing older than a day), or human.
9. **memephil's X posts** (`content.py`, `/post`): modelled on @neet_sol (55k followers, ~14 posts a day for 18 months about one worldview, never its coin). memephil's worldview: it's an AI, AGI is coming, and it AI-ifies whatever meme people are on. Topics are Google Trends' daily US searches, the pump.fun narratives the bot collects (with the X posts their coins link), and an evergreen everyday-life slot; tragedies never reach the model. The model (Groq, then OpenRouter's free models, then Workers AI, then Pollinations; see `openrouter.py`) writes three jokes and an image idea: `edit` AI-ifies the topic coin's own picture, `new` draws fresh art, `none` is text only. Drafts go to Telegram with **Post 1/2/3**, **N without image** and **Skip**; nothing is posted without a tap, and a posted message gets **Delete it**. Posts never carry tickers, contract talk or links (checked in code). `X_DRAFTS_PER_DAY` drafts a day outside `CONTENT_QUIET_UTC` (default 6-12 UTC); `/post` drafts one now; `/post off` / `/post on` pauses the schedule. Posting counts toward `X_MAX_POSTS_PER_DAY`.
10. **memephil's replies** (`content.replies`, `/replies on|off`): a small account grows from good early replies under big posts. The j7tracker feed (same `/j7` session; it stays connected for replies even with `/feed off`) hands fresh original posts from accounts with `X_REPLY_MIN_FOLLOWERS` (100,000)+ followers, under `X_REPLY_MAX_AGE_S` (300 s) old, not grim or political, to a reply writer. The model decides whether memephil fits there (no death, politics, personal news or ads; when in doubt it passes) and writes up to three one-line replies in memephil's hopeful-AI voice; copies of the post, links, tickers and lines over 200 characters are dropped in code. Each draft goes to Telegram with **Reply N on X** links: X's compose screen opens as a reply to that post with the text filled in, and you post it from memephil (X's API may not reply to posts that don't mention us). At most `X_REPLIES_PER_HOUR` (4) drafts an hour, 20 model calls an hour (Groq's free 1,000 a day is shared), newest post first, none during `CONTENT_QUIET_UTC`. Never automated: no reply goes out without your tap.

## Other launchpads (`launchpads.py`)

`LAUNCHPAD=pump` (default), `jupiter`, `bags`, `meteora`, `launchlab` or `letsbonk`. Jupiter Studio launches on a Meteora Dynamic Bonding Curve, so the coin has a
real pool from the start (LP-fee share, optional locked LP) instead of pump.fun's curve-only phase. It needs
`JUPITER_API_KEY`; metadata is hosted by Jupiter (no Pinata); the transaction is still signed locally. Its per-launch
cost is an **unmeasured** estimate (0.02 SOL): read the real figure off the wallet after the first live launch and fix
`launch_cost_sol`. Jupiter has no dev buy (`DEV_BUY_SOL` must be 0). Creator-fee claim exists in their docs (`POST /dbc/fee`, `POST /dbc/fee/create-tx`) but is not wired here. **Not working as of 2026-10-13:** `create-tx` answers HTTP 400 "Required, Required, Required, Expected number, received nan, Required" to every body, including the docs' own example and `{}`, from curl and Node alike; retest before relying on `LAUNCHPAD=jupiter`.

`bags` needs `BAGS_API_KEY`. Each launch is: create-token-info (Bags pins the metadata), a fee-share config (dev keeps
100% of the claimers' pool; the payer is also registered as deployer with a separate 25% share; a config that needs a
Jito bundle is re-requested with a tip and sent to Jito), then create-launch-transaction with the dev buy. `/claim`
and auto-claim pull creator fees and the deployer share. Cost is an unmeasured 0.03 SOL estimate; the docs state
none, and whether a zero dev buy is accepted is also undocumented. Neither launchpad has been run live.
`meteora` is our own Meteora Dynamic Bonding Curve config, so the fee terms are ours (`meteora/dbc.mjs` builds the
transactions with Meteora's TypeScript SDK; Python signs and sends, the helper never sees a key). Setup, once:

```sh
cd meteora && npm ci --ignore-scripts && cd ..
python -m devbot.dbc_setup           # dry run: shows the curve
python -m devbot.dbc_setup --send    # creates the config on chain (a little rent); prints DBC_CONFIG=...
```

Defaults: 30 -> 400 SOL market cap (`DBC_INITIAL_MCAP`/`DBC_MIGRATION_MCAP`), an anti-sniper fee decaying 50% -> 1% over
60 s that accrues to us as partner, dev buy atomic with pool creation, graduation to DAMM v2 with the LP permanently
locked. `/claim` collects the partner fees from every pool the ledger shows this launchpad created. Checked by
simulating on mainnet (nothing sent): the config transaction, and pool creation plus first buy against another
launchpad's live SPL config. Not yet run live. Not done: claiming the locked DAMM v2 LP position's fees after
graduation, and Pinata cleanup (pump.fun-only), so metadata files from Meteora launches accumulate.
`launchlab` / `letsbonk` is Raydium LaunchLab (`raydium/launchlab.mjs`, same helper pattern; `cd raydium && npm ci --ignore-scripts`).
LaunchLab platforms are permissionless (about 3,800 exist) and the platform sets what the creator earns, read from
chain 2026-10-13: Raydium's own 0.05% of volume, **LetsBonk 0%**, several third parties 0.5%. So `letsbonk` gets the
LetsBonk audience but pays the dev nothing from trading. Set `LAUNCHLAB_PLATFORM` to any platform id, or make our own:

```sh
python -m devbot.launchlab_setup          # dry run
python -m devbot.launchlab_setup --send   # creates a platform owned by the dev wallet; prints LAUNCHLAB_PLATFORM=...
```

Our own platform defaults to a 0.5% platform fee plus a 0.5% creator fee (both ours; traders also pay Raydium's 0.25%),
with the graduated CPMM LP lock position going to the dev wallet (the program rejects any creator share of it for new
platforms, so it is assigned to the platform wallet, the same address). `/claim` collects the creator vault and, on our
own platform, the platform vault. Checked by mainnet simulation (nothing sent): launch with and without a dev buy on the
Raydium, LetsBonk and a third-party platform, and platform creation. `DEVBOT_NET_TESTS=1 pytest` also builds a real launch
transaction through the helper. Not yet run live. Not done: harvesting the locked CPMM LP position's fees after
graduation, Pinata cleanup. The other launchpads in the list have no public create-token API.

## Coin website

Deploy once from your own Cloudflare login. The bot needs no extra keys.

```sh
cd site
npx wrangler@4.139.0 login     # opens the browser
npx wrangler@4.139.0 deploy    # prints https://coins.<subdomain>.workers.dev
```

Then set `WEBSITE_BASE` to that URL in `~/.devbot/.env` and restart the bot.

## Spending controls

- `LIVE=0` is the default. Cycles stop at a Telegram preview. Nothing is uploaded or sent.
- `LIVE=1` refuses to start without an explicit `MAX_SOL_PER_DAY`. There is no default budget.
- The daily SOL cap, the optional launch-count cap (`MAX_LAUNCHES_PER_DAY`, 0 = none), the `/stop` switch and the balance-floor baseline are stored in SQLite, so a restart neither resets them nor re-arms the bot. The floor is `FLOOR_PCT` of a stored baseline. `/rebase` sets the baseline; otherwise it only ever moves **up**, after an auto-claim raises the balance, never down with a loss.

## Housekeeping (every 10 minutes)

- **Auto-claim:** with `LIVE=1`, creator fees are claimed once at least `CLAIM_MIN_SOL` is waiting. A dropped claim is retried 3 times and reported if it never lands.
- **Pinata cleanup:** the free plan holds about 500 files, 2 per coin. A coin is dead once nobody has traded it for an hour and its curve holds under 0.05 SOL. Its image and metadata are then deleted from Pinata, so storage tracks live coins instead of every coin ever launched. A coin that graduated, or that people still hold, is never touched.
- Only `TELEGRAM_ALLOWED_USERS` can command the bot, and it refuses to start if that list is empty.
- One dev wallet does everything. `DISCLOSURE` (empty on the box since 2026-10-13) can add the dev buy and wallet to each coin's description.
- A create that doesn't confirm in 25 s is rebuilt on a fresh blockhash and resent (up to 3 tries); reusing the mint makes at most one coin possible.

Cost per launch is about 0.0098 SOL plus the dev buy and priority fee. That figure was measured on-chain. The creator fee is 30 of pump.fun's 125 bp, so a coin pays back its launch cost at roughly 3.3 SOL of volume (see `memecoin/research/dexarb/RESULT-pumpfun-creator-fee-seat-20261012.md`).

## Accounts you need

| Variable | Where |
|---|---|
| `DEVBOT_TELEGRAM_TOKEN` | A new bot from @BotFather. Not Hermes's token: two pollers on one token get HTTP 409. |
| `PINATA_JWT` | pinata.cloud free tier, under API Keys. |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard, Workers AI. The token needs Workers AI permission. |
| `DEV_KEYPAIR_PATH` | The bot wallet `DZo2AkoC418KFiqFDwqwwsiWicVX6wDJHFHgRvh3j1RS` (the Frankfurt trading key), copied to `~/.devbot/dev-wallet.json` with mode 600. |

## Deploy

```sh
NAME=<vm> deploy/install.sh          # copies, builds the venv, installs the user unit
# on the VM: install -m 600 /dev/null ~/.devbot/.env; fill it from .env.example; add the keypair
NAME=<vm> deploy/install.sh          # second run enables and starts it
journalctl --user -u devbot -f       # on the VM
```

The unit is capped at `MemoryMax=220M` so it can't starve the Hermes gateway on the 964 MB box.

## Telegram commands

`/propose` · `/coin <idea>` · `/trends` · `/auto on|off` · `/status` · `/stats` · `/humans` · `/feed [on|off]` · `/post [on|off]` · `/claim` · `/unwrap` · `/j7 <access token>` · `/stop` · `/resume` · `/rebase`

`/coin <idea>` designs a coin from your own idea (a reel, a post, a moment): the model names it after the funniest specific detail, never after a brand, and it arrives as a normal Launch/Skip proposal with fresh art.

**j7tracker** (`j7.py`, `LAUNCHER=j7`): pump.fun creates go through j7tracker's deploy API instead of PumpPortal. j7tracker signs on its own servers with an encrypted copy of the dev wallet's key (`J7_WALLET_KEY`, from its app under Deploy Settings > Wallets), so it can move that wallet: keep only launch money there. The bot passes its pre-made mint as `vanity_mint_keypair`, so the X post and website still point at the right coin, sets `private_desc` (no "Deployed using j7tracker.io" stamp) and never uses bundle, sniper or auto-sell wallets. j7tracker's login needs a CAPTCHA and its refresh endpoint only answers the j7tracker site, so send the bot your access token, the `__Secure-j7_at` cookie on core.j7tracker.io (starts `eyJ`), with `/j7 <access token>`: it checks it with j7tracker, keeps it until it expires, and deletes your message. Send a new one when the bot says it expired. Without a session a Launch click stops before posting anything.

**Feed coins** (`feed.py`, `/feed on|off`, needs a `/j7` session; launches still go through `LAUNCHER`, PumpPortal by default): coins off other people's X posts from j7tracker's live Socket.IO feed. Sampled 2026-10-14 it carries ~11 posts a minute, mostly news, replies and politics, plus j7tracker's obvious coin name per post and the coins other users already launched for it (5 identical "Moneyball" coins within seconds of one post). So the bot keeps only original posts under `FEED_MAX_AGE_S` (60) old from accounts with `FEED_MIN_FOLLOWERS` (200,000)+ followers, skips grim, political and coin-shilling posts, designs its own name (never the obvious one) and fresh art, drops the post if more than `FEED_MAX_COPIES` (4) copies appeared meanwhile (j7tracker lists up to 5, so 5 means swarmed), and sends a Launch/Skip proposal showing the post, how old it is and how many copies exist, at most `FEED_MAX_PER_HOUR` (6) an hour, newest post first. Launch posts memephil's own launch post and the coin links it (or the `X_HANDLE` profile if that post fails); X's API refuses to quote posts that don't mention us (403), so it is a plain post, and the poster is never presented as involved. The feed uses the same `/j7` session and pauses (with one message) when it expires.

**Feed auto-launch** (`FEED_AUTO_SOL_PER_DAY`, or `/feed auto <SOL>` / `/feed auto off`): a feed post that no one has coined yet launches without a tap. The feed thread queues the finished design; the main thread (Telegram long-poll shortened to 2 s while auto is on) launches it only if the post still has 0 copies, is at most `FEED_AUTO_MAX_AGE_S` (45) old, fewer than `FEED_AUTO_MAX_PER_HOUR` (3) auto launches happened in the last hour, and today's auto spend (spend rows labelled `auto `) plus this launch stays within the daily SOL. The usual checks (stop switch, LIVE, balance floor) still run inside the launch. A design that fails any of these is offered with Launch/Skip and says why. Posts that already have copies always wait for a tap. Started 2026-10-14 at 0.1 SOL a day.

**Mayhem mode** (`MAYHEM=1`, PumpPortal launches only): each create sets pump.fun's Mayhem flag (PumpPortal's `isMayhemMode`), so pump.fun's own agent trades the coin for its first 24 hours; pump.fun shows the coin as Mayhem. Simulated 2026-10-14 it costs about 0.0027 SOL more rent than a plain create, which `cost()` and the floor check include. The agent's wallet (`pump.MAYHEM_AGENT`) is left out of `/humans`, so buyer, human and late-buy counts still measure people. **The agent's trades pay the creator nothing:** on $HSTK (2026-10-14, 23.35 SOL volume, half of it the agent's) six sampled agent trades moved 0 lamports into our creator vault, while people's trades paid 0.29-0.30%. So Mayhem adds volume but no creator fees.

**Dev buy and selling it** (`DEV_BUY_SOL`, or `/devbuy <SOL>` which overrides it, up to 1 SOL): the create buys that much for the dev wallet, and the cost and floor check include it. When it is above 0, the launch message carries **Sell 25% / Sell 50% / Sell 100%** buttons; `/sell <ticker or mint> <percent>` does the same later. A sell goes through PumpPortal (`pool: auto`, so it also works after migration), is sent once and never resent (a late-landing percentage sell would sell again from what's left), and a second tap on the same coin within 15 s is held back. Proceeds are recorded and counted in `/stats`.

`/humans [n]` scores the last n coins (default 5): launch-block snipers, bots, fresh wallets, humans, and buys more than 60 s after launch (anyone still there once the bots left), then a scorecard of feed coins against everything else.

`/unwrap` closes the wallet's wSOL accounts, so the wrapped balance and each account's rent come back as native SOL. Launches can only spend native SOL.

## memephil's own launches: a disclosed launch-block treasury

Not part of the bot. `launch_brand_coin.py` (`devbot/treasury.py`, `devbot/jito.py`) launches a memephil-brand coin and buys it from memephil's own wallets **in its creation block**, which is the one seat no outside sniper can take (`research/RESULT-bot-levers-20261014.md` §3; in-block position is a lottery over every free lever). The usual version of this hides the wallets so insider supply reads as a crowd. Here the split is published instead:

1. `wallets --new N --rule "<your commitment, in your words>"` makes N keys (0600, one file each in `$DEVBOT_STATE/treasury`, kept so the positions can be moved later) and writes `site/public/treasury.json`.
2. `cd site && npx wrangler deploy` publishes it at `<base>/treasury.json`, rendered for people at `<base>/treasury`. Do this **before** any coin exists.
3. `launch --name … --symbol … --art … --description … --buy-sol X [--dev-buy-sol Y] [--send]` pins the metadata (the wallet list rides in it as `treasury`, and in the description), funds each wallet to its buy plus `BUY_OVERHEAD_SOL`, and sends one Jito bundle: the create first, then one buy per wallet.

`check_published` reads the live manifest and **refuses the launch** if a wallet about to buy is not already listed there, so the disclosure can never lag the buy. A bundle holds 5 transactions, so at most 4 treasury wallets (`treasury.MAX_WALLETS`) can be in the block; the create's `priorityFee` is the whole bundle's Jito tip. A bundle is atomic and is never resent: either the create and every buy land in one slot or nothing does, and a bundle that times out is reported as `Pending`, not as a failure, because it may still land. Dry run by default; `--send` is the only thing that spends.

There is deliberately no wash volume, holder padding or bump cycling here. Our own tape says bots are already 87-88% of non-creator volume, so manufactured activity recruits bots, not people — and it exists only to misstate demand.

`launch_memephil.py` is the older one-off that launched $MEMEPHIL itself (community token, no treasury, no dev buy).

## Tests

```sh
uv venv && uv pip install -r requirements.txt pytest && .venv/bin/python -m pytest -q tests
```

**Clone / PVP** (`/clone [mint]`, `/pvp off|ask|auto`): an exact copy of a coin that is being bought: same name, ticker, description (links and "deployed using" stamps stripped) and the source's own picture, unchanged. The coin's links are ours (memephil's launch post), never the original's X account or site. `/clone` with no mint copies the hottest live coin; with a mint it copies that pump.fun coin. `/pvp ask` checks pump.fun every `PVP_POLL_S` (30) seconds and sends a Launch/Skip proposal for the hottest coin with `PVP_MIN_BUYERS` (8)+ outside buyers within `FRESH_MINUTES` of launch; `/pvp auto` launches it itself (LIVE=1 only, same caps and balance floor as `/auto`; in a dry run it only previews). A source is offered once, our own coins are skipped, and so are coins with more than `PVP_MAX_COPIES` (4) copies already out. Clones are recorded with `source_symbol='clone'`.
