"""从 FOFA 拉取 IP 入队"""
import urllib.request, json, base64, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

email = "740908876@qq.com"
key = "09cb5e3a29864bc9440130221c15f184"

queries = [
    ("机场HK", 'title="机场" && country="HK"', 100),
    ("机场TW", 'title="机场" && country="TW"', 100),
    ("SS-HK", 'port="8388" && country="HK"', 100),
    ("SS-TW", 'port="8388" && country="TW"', 100),
]

all_ips = set()
for name, q, size in queries:
    qb64 = base64.b64encode(q.encode()).decode()
    url = "https://fofa.info/api/v1/search/all?email=%s&key=%s&qbase64=%s&size=%d&fields=ip" % (email, key, qb64, size)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
        ips = [row[0] for row in d.get("results", []) if row and row[0]]
        all_ips.update(ips)
        print("%s: %d个 (总量%d)" % (name, len(ips), d.get("size", 0)))
    except Exception as e:
        print("%s: 失败 %s" % (name, e))
    time.sleep(0.5)

unique = list(all_ips)
print("去重后: %d 个 IP" % len(unique))

from netcheck.queue import enqueue, queue_stats
added = enqueue(unique, source="fofa_seed", priority=5)
print("入队: %d 个新 IP" % added)
print("队列状态:", queue_stats())
