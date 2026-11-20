"""AI-scored Bitcoin headlines vs next-day returns (Claude, 20 Nov 2026).

1) Scores each day's GDELT headlines (research/llm_cache/btc_YYYY-MM-DD.json) with the local Ollama model
   (qwen3:14b, free, private): -2 clearly bearish .. +2 clearly bullish for Bitcoin's price, plus confidence 0-1.
   The model sees only that day's headlines, never prices. Scores are cached in research/llm_scores.csv (resumable).
2) Backtest on daily BTC (C:/Projects/walt-trading-bot/data/research/daily/BTC-USD.csv): signal from day t's
   headlines (UTC day), enter at day t+1 open, hold 1 or 3 days; long if score >= k, short if score <= -k.
   k, holding and follow/fade are chosen on 2025-01-01..2025-06-30 only; the frozen choice is reported for
   2025-07-01 onward, against random entries (same count and holding) and buy-and-hold.
   Costs 0.14% per round trip + funding 0.01%/day against longs (Walt's usual rate, paid hourly at 1/8 of the 8h rate).
Caveat: the model may know some 2025 events from training (look-ahead the backtest can't remove).
"""
import csv, json, random, urllib.request
from pathlib import Path
import numpy as np, pandas as pd

R = Path(__file__).parent
CACHE, SCORES = R / "llm_cache", R / "llm_scores.csv"
MODEL = "qwen3:14b"
PROMPT = ("You read one day's news headlines about Bitcoin. Judge only from these headlines whether they are bullish or "
          "bearish for Bitcoin's price over the next few days. Reply with JSON only: "
          '{"score": integer from -2 (clearly bearish) to 2 (clearly bullish), "confidence": number 0 to 1}. /no_think\n\nHeadlines:\n')


def score_day(titles):
    body = {"model": MODEL, "prompt": PROMPT + "\n".join("- " + t for t in titles[:60]), "stream": False,
            "format": "json", "think": False, "options": {"temperature": 0}}
    r = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:11434/api/generate",
                                                                data=json.dumps(body).encode()), timeout=300))
    d = json.loads(r["response"])
    return max(-2, min(2, int(round(float(d.get("score", 0)))))), float(d.get("confidence", 0))


done = {}
if SCORES.exists():
    done = {row["date"]: row for row in csv.DictReader(open(SCORES, encoding="utf-8"))}
with open(SCORES, "a", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, ["date", "score", "confidence", "n"])
    if not done:
        w.writeheader()
    for f in sorted(CACHE.glob("btc_*.json")):
        day = f.stem[4:]
        if day in done:
            continue
        titles = [a["title"] for a in json.loads(f.read_text(encoding="utf-8"))["articles"] if a.get("title")]
        if len(titles) < 5:
            continue
        s, c = score_day(titles)
        w.writerow({"date": day, "score": s, "confidence": round(c, 2), "n": len(titles)}); fh.flush()
        print(day, s, c, flush=True)

sc = pd.read_csv(SCORES, parse_dates=["date"]).set_index("date").sort_index()
px = pd.read_csv(r"C:\Projects\walt-trading-bot\data\research\daily\BTC-USD.csv", index_col=0, parse_dates=True)
px.index = pd.to_datetime(px.index).tz_localize(None)


def trades(k, h, mode, lo, hi):
    out = []
    for t, row in sc.loc[lo:hi].iterrows():
        side = 1 if row.score >= k else -1 if row.score <= -k else 0
        if not side:
            continue
        side *= mode
        i = px.index.searchsorted(t + pd.Timedelta(days=1))
        if i + h >= len(px):
            continue
        r = side * (px.close.iloc[i + h - 1] / px.open.iloc[i] - 1) * 100
        out.append(r - 0.14 - side * 0.01 * h)
    return np.array(out)


def stats(a):
    return {"n": int(len(a)), "avg": round(float(a.mean()), 3) if len(a) else None,
            "win": round(float((a > 0).mean()), 3) if len(a) else None,
            "t": round(float(a.mean() / a.std(ddof=1) * np.sqrt(len(a))), 2) if len(a) > 2 else None}


TR, TE = ("2025-01-01", "2025-06-30"), ("2025-07-01", "2026-11-19")
grid = {(k, h, m): stats(trades(k, h, m, *TR)) for k in (1, 2) for h in (1, 3) for m in (1, -1)}
best = max(grid, key=lambda g: grid[g]["avg"] if grid[g]["n"] >= 15 and grid[g]["avg"] is not None else -99)
test = trades(*best, *TE)
rng = random.Random(1)
days = list(px.loc[TE[0]:TE[1]].index[:-best[1]])
rand = [np.mean([rng.choice((1, -1)) * (px.close.iloc[px.index.get_loc(d) + best[1] - 1] / px.open.iloc[px.index.get_loc(d)] - 1) * 100 - 0.14
                 for d in rng.sample(days, len(test))]) for _ in range(200)] if len(test) else []
out = {"configs_tried": len(grid), "chosen": {"k": best[0], "hold_days": best[1], "mode": "follow" if best[2] == 1 else "fade"},
       "train": grid[best], "test": stats(test), "random_test_median_avg": round(float(np.median(rand)), 3) if rand else None,
       "score_distribution": sc.score.value_counts().sort_index().to_dict(), "grid_train": {str(k): v for k, v in grid.items()}}
(R / "llm_headlines_results.json").write_text(json.dumps(out, indent=1, default=str))
print(json.dumps({k: out[k] for k in ("chosen", "train", "test", "random_test_median_avg", "score_distribution")}, default=str))
