"""Did a headline with the coin's name precede the launch? Google News RSS, exact phrase, 72 h before create."""
import json, re, time, urllib.parse, urllib.request, email.utils, html, collections
rows = json.load(open('research/data/week/posts.json'))
out = {}; seen = set()
def fetch(q):
    u = 'https://news.google.com/rss/search?q=' + urllib.parse.quote(f'"{q}" when:10d') + '&hl=en-US&gl=US&ceid=US:en'
    for k in range(3):
        try: return urllib.request.urlopen(urllib.request.Request(u, headers={'user-agent': 'Mozilla/5.0'}), timeout=20).read().decode('utf8', 'replace')
        except Exception: time.sleep(3)
    return ''
for r in sorted(rows, key=lambda r: -r['peak']):
    nm = (r['name'] or '').strip()
    if len(nm) < 4 or nm.lower() in seen: continue
    seen.add(nm.lower())
    x = fetch(nm); items = []
    for m in re.finditer(r'<item>(.*?)</item>', x, re.S):
        it = m.group(1); t = re.search(r'<title>(.*?)</title>', it, re.S); d = re.search(r'<pubDate>(.*?)</pubDate>', it)
        if not (t and d): continue
        ts = email.utils.parsedate_to_datetime(d.group(1)).timestamp(); ti = html.unescape(t.group(1))
        if nm.lower() in ti.lower(): items.append((ts, ti))
    pre = sorted(i for i in items if r['created'] - 72 * 3600 <= i[0] <= r['created'])
    out[nm] = dict(created=r['created'], peak=r['peak'], pre=[(round((r['created'] - t) / 3600, 1), ti[:110]) for t, ti in pre[:3]], npre=len(pre), npost=len(items) - len(pre))
    time.sleep(1.5)
json.dump(out, open('research/data/week/news.json', 'w'))
w = [k for k, v in out.items() if v['npre']]
print(f"{len(out)} names queried; {len(w)} have a headline containing the name in the 72h BEFORE launch")
for k in w[:40]:
    v = out[k]; print(f"{k[:16]:<16} peak {v['peak']/1e6:5.1f}M  n={v['npre']:3d}  first {v['pre'][0][0]:5.1f}h before: {v['pre'][0][1]}")
