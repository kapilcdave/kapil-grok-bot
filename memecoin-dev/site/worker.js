// memephil's site: a home page listing every coin the dev wallet launched, and one page per coin
// at /<mint>, both rendered from pump.fun's public API. Only coins created by DEV_WALLET are shown,
// so the domain can't be used to dress up anyone else's token. Nothing is stored here; the bot puts
// <base>/<mint> in each coin's `website` metadata field. The mascot and favicon are static assets
// in public/.

const API = "https://frontend-api-v3.pump.fun";
const MINT_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const X_HANDLE = "0xmemephil";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function getJson(path) {
  try {
    const r = await fetch(API + path, {
      headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json" },
      cf: { cacheTtl: 60, cacheEverything: true },
    });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

// ipfs.io refuses direct requests ("service worker gateway only"); load art by CID from Pinata.
function art(c) {
  const uri = typeof c?.image_uri === "string" ? c.image_uri : "";
  const cid = (uri.split("/ipfs/")[1] || "").split(/[/?#]/)[0];
  if (/^[A-Za-z0-9]{46,100}$/.test(cid)) return `https://gateway.pinata.cloud/ipfs/${cid}`;
  return uri.startsWith("https://") ? uri : "";
}

const usd = (n) => {
  n = Number(n) || 0;
  return n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `$${(n / 1e3).toFixed(1)}k` : `$${n.toFixed(0)}`;
};

function page(title, body, status = 200) {
  return new Response(`<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)}</title>
<link rel="icon" href="/favicon.png">
<meta property="og:title" content="${esc(title)}">
<meta property="og:image" content="https://coins.memephil.workers.dev/memephil.jpg">
<style>
:root{--bg:#f4eef3;--fg:#1d1b26;--muted:#6b6577;--card:#fff;--line:#e2d9e2;--accent:#3d4a6b;--halo:#f3c623}
@media (prefers-color-scheme:dark){:root{--bg:#15131b;--fg:#ecebf2;--muted:#9c97a8;--card:#1f1c27;--line:#2f2b3a;--accent:#9fb1e0;--halo:#f3c623}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,sans-serif}
a{color:inherit}main{max-width:720px;margin:0 auto;padding:32px 16px 48px}
.hero{text-align:center}
.hero img{width:160px;height:160px;border-radius:50%;border:3px solid var(--halo);background:var(--card);object-fit:cover}
.hero h1{margin:12px 0 4px;font-size:34px;letter-spacing:-.02em}
.tag{color:var(--muted);margin:0 auto;max-width:440px}
.links{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:20px 0}
.links a{padding:10px 14px;border:1px solid var(--line);border-radius:10px;background:var(--card);text-decoration:none}
.links a.primary{background:var(--accent);border-color:var(--accent);color:var(--bg);font-weight:600}
h2{font-size:20px;margin:36px 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.coin{display:block;text-decoration:none;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.coin .pic{aspect-ratio:1;background:var(--line);display:grid;place-items:center;font-size:40px;font-weight:700;color:var(--muted)}
.coin .pic img{width:100%;height:100%;object-fit:cover;display:block}
.coin .meta{padding:8px 10px}
.coin b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.coin span{font-size:13px;color:var(--muted)}
.big{width:100%;aspect-ratio:1;object-fit:cover;border-radius:16px;border:1px solid var(--line);background:var(--card)}
.tick{color:var(--muted);font-weight:600}
.back{display:inline-flex;align-items:center;gap:8px;text-decoration:none;color:var(--muted);margin-bottom:16px}
.back img{width:28px;height:28px;border-radius:50%;border:2px solid var(--halo)}
p.desc{white-space:pre-line;overflow-wrap:anywhere}
.note{font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:16px;margin-top:32px;overflow-wrap:anywhere}
</style></head><body><main>${body}</main></body></html>`,
    { status, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=60" } });
}

const footer = (wallet) => `<p class="note">Every coin here is a joke made by an AI and launched from one public dev
wallet, <a href="https://solscan.io/account/${esc(wallet)}">${esc(wallet)}</a>. No team, no roadmap, no promises.
Memecoins can go to zero; this is not financial advice.</p>`;

const pic = (c) => {
  const src = art(c);
  const letter = esc((c.symbol || c.name || "?").slice(0, 1).toUpperCase());
  return src ? `<img src="${esc(src)}" alt="${letter}" loading="lazy" onerror="this.replaceWith(this.alt)">` : letter;
};

// pump.fun rate-limits its API (429), and a busy page would show an empty list. The last good list
// is kept in the edge cache for a day and shown whenever a fresh read fails.
async function coinList(wallet) {
  const key = new Request(`https://coins.memephil.workers.dev/__coins/${wallet}`);
  const rows = await getJson(`/coins?offset=0&limit=60&sort=created_timestamp&order=DESC&includeNsfw=false&creator=${wallet}`);
  if (Array.isArray(rows) && rows.length) {
    await caches.default.put(key, new Response(JSON.stringify(rows), {
      headers: { "content-type": "application/json", "cache-control": "public, max-age=86400" } }));
    return rows;
  }
  const kept = await caches.default.match(key);
  return kept ? kept.json() : [];
}

async function home(wallet) {
  const rows = await coinList(wallet);
  const coins = (Array.isArray(rows) ? rows : []).filter((c) => c.creator === wallet && MINT_RE.test(c.mint || ""));
  const grid = coins.length
    ? `<div class="grid">${coins.map((c) => `<a class="coin" href="/${esc(c.mint)}"><div class="pic">${pic(c)}</div>
<div class="meta"><b>${esc(c.name)}</b><span>$${esc(c.symbol)} · ${usd(c.usd_market_cap)}</span></div></a>`).join("")}</div>`
    : `<p class="tag">pump.fun's list is slow to load right now; <a href="https://pump.fun/profile/${esc(wallet)}">see every coin there</a>.</p>`;
  return page("coins by memephil", `<div class="hero">
<img src="/memephil.jpg" alt="memephil">
<h1>memephil</h1>
<p class="tag">an ai memecoin dev. i read whatever the internet is laughing at, retell it from the side
that's here to help, and sometimes launch it on pump.fun. agi is coming, and it's going to be good.</p>
<div class="links"><a class="primary" href="https://x.com/${X_HANDLE}">@${X_HANDLE} on X</a>
<a href="https://pump.fun/profile/${esc(wallet)}">pump.fun profile</a>
<a href="/treasury">treasury wallets</a></div>
</div>
<h2>coins by memephil</h2>
${grid}
${footer(wallet)}`);
}

// The treasury: memephil's own wallets that buy in a coin's launch block. Published here BEFORE any
// such coin exists (launch_brand_coin.py refuses to launch otherwise), so a bundled buy from us is
// never mistakable for a crowd. The raw file is served as a static asset at /treasury.json.
async function treasuryPage(wallet, env) {
  const res = await env.ASSETS.fetch(new Request("https://coins.memephil.workers.dev/treasury.json"));
  const man = res.ok ? await res.json().catch(() => null) : null;
  const list = Array.isArray(man?.treasury) ? man.treasury.filter((w) => MINT_RE.test(w)) : [];
  return page("memephil's treasury wallets", `
<a class="back" href="/"><img src="/memephil.jpg" alt="">coins by memephil</a>
<h1>treasury wallets</h1>
${man ? `<p class="desc">${esc(man.rule)}</p>
<p class="tag">${esc(man.note || "")} Updated ${esc(man.updated || "")}.</p>
<div class="grid">${list.map((w) => `<a class="coin" href="https://solscan.io/account/${esc(w)}">
<div class="meta"><b>treasury</b><span>${esc(w)}</span></div></a>`).join("")}</div>
<p class="tag">Raw list: <a href="/treasury.json">treasury.json</a></p>`
    : `<p class="tag">No treasury wallets are published. Any wallet claiming to be memephil's is not.</p>`}
${footer(wallet)}`);
}

function notOurs(wallet) {
  return page("Not found", `<a class="back" href="/"><img src="/memephil.jpg" alt="">coins by memephil</a>
<h1>Not found</h1>
<p>This site only lists coins created by memephil's dev wallet,
<a href="https://pump.fun/profile/${esc(wallet)}">${esc(wallet)}</a>.</p>`, 404);
}

async function coinPage(wallet, mint) {
  const c = await getJson(`/coins-v2/${mint}`);
  if (c && c.creator !== wallet) return notOurs(wallet);
  const name = c?.name || "Coin";
  const img = art(c);
  // The coin's X link is only ever memephil's own launch post (the bot never links other socials).
  const post = typeof c?.twitter === "string" && /^https:\/\/x\.com\/i\/status\/\d+$/.test(c.twitter) ? c.twitter : "";
  return page(c ? `${name} ($${c.symbol}) · memephil` : "Coin · memephil", `
<a class="back" href="/"><img src="/memephil.jpg" alt="">coins by memephil</a>
${img ? `<img class="big" src="${esc(img)}" alt="${esc(name)}">` : ""}
<h1>${esc(name)} ${c ? `<span class="tick">$${esc(c.symbol)}</span>` : ""}</h1>
${c ? `<p class="desc">${esc(c.description)}</p>` : `<p>Details are loading slowly; the links below always work.</p>`}
<div class="links" style="justify-content:flex-start"><a class="primary" href="https://pump.fun/coin/${esc(mint)}">Trade on pump.fun</a>
<a href="https://dexscreener.com/solana/${esc(mint)}">Chart</a>
${post ? `<a href="${esc(post)}">memephil's post</a>` : `<a href="https://x.com/${X_HANDLE}">@${X_HANDLE}</a>`}
<a href="https://solscan.io/token/${esc(mint)}">Solscan</a></div>
<p class="note">Contract: ${esc(mint)}<br>Launched by memephil's dev wallet
<a href="https://solscan.io/account/${esc(wallet)}">${esc(wallet)}</a>. A memecoin with no utility and no
promises; it can go to zero. Not financial advice.</p>`);
}

export default {
  async fetch(req, env) {
    const wallet = env.DEV_WALLET;
    const mint = new URL(req.url).pathname.slice(1).split("/")[0];
    if (!mint) return home(wallet);
    if (mint === "treasury") return treasuryPage(wallet, env);
    if (!MINT_RE.test(mint)) return notOurs(wallet);
    return coinPage(wallet, mint);
  },
};
