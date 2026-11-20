"""News-shock drift on stocks (Claude, 20 Nov 2026).

A large overnight gap (|open/prev close - 1| > k x 60-day daily vol) marks a news/earnings event. Test whether the price
keeps drifting in the gap direction (post-event drift) or fades, entering at the CLOSE of the gap day (so the gap itself
is not captured) and holding h trading days. Thresholds and side are chosen on 2012-2019, reported frozen on 2020-2026.
Costs 0.14% round trip; funding: longs pay 0.08%/calendar day, shorts receive it (Walt median; zero-funding also shown).
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

D = Path(r"C:\Projects\walt-trading-bot\data\research\daily")
STOCKS = ["AAPL", "AMD", "AMZN", "AVGO", "BABA", "COIN", "DELL", "GME", "GOOGL", "HOOD", "IBM", "INTC", "META", "MRNA",
          "MRVL", "MSTR", "MU", "NVDA", "ORCL", "PLTR", "TSLA", "TSM"]
SPLIT = pd.Timestamp("2020-01-01")
FEE, FUND = 0.14, 0.08


def events(s, k, h):
    d = pd.read_csv(D / f"{s}.csv", index_col=0, parse_dates=True)
    d.index = pd.to_datetime(d.index).tz_localize(None)
    d = d[(d.open > 0) & (d.close > 0)].loc["2012-01-01":]
    gap = d.open / d.close.shift(1) - 1
    vol = (d.close.pct_change()).rolling(60).std().shift(1)
    z = gap / vol
    rows = []
    nxt = None
    idx = list(d.index)
    for i, t in enumerate(idx):
        if i + h >= len(idx) or not (abs(z.iloc[i]) > k) or (nxt is not None and i < nxt):
            continue
        side = 1 if gap.iloc[i] > 0 else -1
        ret = (d.close.iloc[i + h] / d.close.iloc[i] - 1) * 100
        days = (idx[i + h] - t).days
        rows.append((t, side, ret, days))
        nxt = i + h
    return rows


def summarize(rows, mode, funding=True):
    r = []
    for t, side, ret, days in rows:
        d = side * mode               # mode 1 = follow the gap, -1 = fade it
        r.append(d * ret - FEE - (d * FUND * days if funding else 0))
    r = np.array(r)
    if len(r) < 3:
        return {"n": int(len(r))}
    return {"n": int(len(r)), "avg": round(float(r.mean()), 3), "median": round(float(np.median(r)), 3),
            "win": round(float((r > 0).mean()), 3), "t": round(float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))), 2)}


res, train = {}, {}
for k in (2, 3, 4):
    for h in (1, 5, 20):
        allrows = [x for s in STOCKS if (D / f"{s}.csv").exists() for x in events(s, k, h)]
        tr = [x for x in allrows if x[0] < SPLIT]
        te = [x for x in allrows if x[0] >= SPLIT]
        for mode in (1, -1):
            key = f"k{k}_h{h}_{'follow' if mode == 1 else 'fade'}"
            res[key] = {"train": summarize(tr, mode), "test": summarize(te, mode),
                        "test_no_funding": summarize(te, mode, False),
                        "test_long_gaps_only": summarize([x for x in te if x[1] > 0], mode),
                        "test_short_gaps_only": summarize([x for x in te if x[1] < 0], mode)}
best = max(res, key=lambda k: res[k]["train"].get("avg", -99) if res[k]["train"]["n"] >= 50 else -99)
out = {"configs_tried": len(res), "chosen_on_train": best, "chosen": res[best], "all": res}
Path(__file__).with_name("news_gap_drift_results.json").write_text(json.dumps(out, indent=1))
print("chosen", best, json.dumps(res[best], indent=1))
for k, v in res.items():
    print(f"{k:22s} train {v['train'].get('n'):>4} {v['train'].get('avg')!s:>7}  test {v['test'].get('n'):>4} {v['test'].get('avg')!s:>7} t={v['test'].get('t')}  nofund {v['test_no_funding'].get('avg')}")
