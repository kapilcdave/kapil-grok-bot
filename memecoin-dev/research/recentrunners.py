"""10-25..10-27 runners from GeckoTerminal trending + top-volume pools (recorder is gone): created >= 10-24 12Z, fdv >= 150k."""
import json, time, urllib.request, calendar, collections
def get(u):
    for k in range(4):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'accept': 'application/json', 'user-agent': 'Mozilla/5.0'}), timeout=25))
        except Exception as e: time.sleep(8 * (k + 1))
    return {}
G = 'https://api.geckoterminal.com/api/v2/networks/solana'
pools = {}
for path in [f'/trending_pools?duration={d}&page={p}' for d in ('1h', '6h', '24h') for p in (1, 2, 3)] + [f'/pools?sort=h24_volume_usd_desc&page={p}' for p in (1, 2, 3, 4, 5)]:
    d = get(G + path)
    for x in d.get('data') or []:
        a = x['attributes']; pools[a['address']] = (x, a)
    time.sleep(3)
T = calendar.timegm((2026, 10, 24, 12, 0, 0))
rows = []
for addr, (x, a) in pools.items():
    c = a.get('pool_created_at')
    if not c: continue
    ts = calendar.timegm(time.strptime(c[:19], '%Y-%m-%dT%H:%M:%S'))
    if ts < T: continue
    base = x['relationships']['base_token']['data']['id'].split('_', 1)[1]
    rows.append(dict(pool=addr, mint=base, name=a['name'], created=ts, fdv=float(a.get('fdv_usd') or 0), liq=float(a.get('reserve_in_usd') or 0),
                     vol24=float((a.get('volume_usd') or {}).get('h24') or 0), chg24=(a.get('price_change_percentage') or {}).get('h24'), dex=x['relationships']['dex']['data']['id']))
rows.sort(key=lambda r: -r['vol24'])
json.dump(rows, open('research/data/week/recent_pools.json', 'w'))
print(len(pools), 'pools seen;', len(rows), 'created since 10-24 12Z')
for r in rows[:40]:
    print(f"{r['name'][:22]:<22} {time.strftime('%m-%d %H:%M', time.gmtime(r['created']))} {r['dex'][:10]:<10} fdv {r['fdv']/1e3:8.0f}k liq {r['liq']/1e3:6.0f}k vol24 {r['vol24']/1e6:6.2f}M chg24 {r['chg24']}%")
