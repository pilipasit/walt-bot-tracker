"""Fetch one day of English Bitcoin headlines per day from GDELT (artlist), 2025-01-01 .. 2026-11-19.
Respects GDELT's limit (one request per 5 s; we wait 6.5 s). Resumable: skips days already cached.
Output: research/llm_cache/btc_YYYY-MM-DD.json  ({"articles":[{"title","seendate","domain"}...]})."""
import json, time, urllib.parse, urllib.request
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).with_name("llm_cache"); OUT.mkdir(exist_ok=True)
d, end = date(2025, 1, 1), date(2026, 11, 19)
while d <= end:
    f = OUT / f"btc_{d}.json"
    if not f.exists():
        q = urllib.parse.quote("bitcoin sourcelang:english")
        s = d.strftime("%Y%m%d")
        url = (f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&maxrecords=75&sort=hybridrel"
               f"&format=json&startdatetime={s}000000&enddatetime={s}235959")
        for attempt in range(4):
            try:
                raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "research-script/1.0"}), timeout=30).read()
                data = json.loads(raw or b"{}")
                arts = [{"title": a.get("title"), "seendate": a.get("seendate"), "domain": a.get("domain")} for a in data.get("articles", [])]
                f.write_text(json.dumps({"articles": arts}), encoding="utf-8")
                print(d, len(arts), flush=True)
                break
            except Exception as e:  # noqa: BLE001 — 429 or bad JSON: back off and retry
                print(d, "retry", attempt, str(e)[:80], flush=True)
                time.sleep(30 * (attempt + 1))
        time.sleep(6.5)
    d += timedelta(days=1)
print("done", flush=True)
