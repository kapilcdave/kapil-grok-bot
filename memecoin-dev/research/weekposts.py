"""X links per week runner: from tape admit rows (links, description) + Jupiter twitter field; post time from the status id."""
import gzip, json, re, time, collections, sys
S2 = {o['mint']: o for o in json.load(open('research/data/week/stage2.json'))}
FILES = ['/Users/kapil/pak/data/runners/runners_20261021.jsonl.gz', '/Users/kapil/pak/data/runners/runners_20261022.jsonl.gz',
         'runners/data_box/runners_20261023.jsonl.gz', 'runners/data_box/runners_20261024.jsonl.gz']
links = collections.defaultdict(set); desc = {}; story = {}
for fn in FILES:
    for l in gzip.open(fn, 'rt'):
        if '"t":"admit"' not in l and '"t":"nadmit"' not in l and '"t":"enrich"' not in l and '"t":"nenrich"' not in l: continue
        r = json.loads(l); m = r.get('mint')
        if m not in S2: continue
        for u in r.get('links') or []: links[m].add(u)
        if r.get('description'): desc[m] = r['description']
        st = r.get('story') or {}
        if st.get('post_url'): links[m].add(st['post_url'])
        if st.get('post'): story[m] = st['post']
        L = ((r.get('features') or {}).get('launch') or {})
        if L.get('twitter'): links[m].add(L['twitter'])
for m, o in S2.items():
    if o.get('tw'): links[m].add(o['tw'])
def xts(u):
    m = re.search(r'/status/(\d+)', u)
    return ((int(m.group(1)) >> 22) + 1288834974657) / 1000 if m else None
hm = lambda t: time.strftime('%m-%d %H:%M', time.gmtime(t))
real = [o for o in S2.values() if o['peak'] >= 1e6 and o['peak'] < 1e8]
n_tw = n_stat = 0; rows = []
for o in sorted(real, key=lambda o: -o['peak']):
    ls = links.get(o['mint'], set())
    tw = [u for u in ls if re.search(r'(x|twitter)\.com/', u)]
    stat = [u for u in tw if '/status/' in u]
    comm = [u for u in tw if '/i/communities/' in u]
    prof = [u for u in tw if u not in stat and u not in comm]
    n_tw += bool(tw); n_stat += bool(stat)
    pt = min([xts(u) for u in stat if xts(u)] or [None]) if stat else None
    rows.append(dict(name=o['name'], mint=o['mint'], created=o['created'], peak=o['peak'], tpk=o['tpk'], post=pt, stat=stat[:2], comm=comm[:1], prof=prof[:2],
                     x1m=o['x'].get('1000000.0'), x150=o['x'].get('150000.0'), desc=(desc.get(o['mint']) or '')[:80]))
print(f"{len(real)} runners; {n_tw} have any X link; {n_stat} link a specific post (status)")
lags = sorted(r['created'] - r['post'] for r in rows if r['post'])
if lags: print('post -> create (min): n=%d p10 %.0f p25 %.0f med %.0f p75 %.0f p90 %.0f' % (len(lags), lags[len(lags)//10]/60, lags[len(lags)//4]/60, lags[len(lags)//2]/60, lags[3*len(lags)//4]/60, lags[9*len(lags)//10]/60))
print('kinds: status', sum(1 for r in rows if r['stat']), 'community', sum(1 for r in rows if r['comm']), 'profile-only', sum(1 for r in rows if r['prof'] and not r['stat'] and not r['comm']))
for r in rows[:45]:
    if r['post']:
        print(f"{r['name'][:12]:<12} post {hm(r['post'])} create +{(r['created']-r['post'])/60:7.1f}m  peak {r['peak']/1e6:5.1f}M  {r['stat'][0][:70]}")
json.dump(rows, open('research/data/week/posts.json', 'w'))
