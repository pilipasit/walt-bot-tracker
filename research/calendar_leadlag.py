"""Calendar effects and cross-asset lead-lag on 2 years of hourly data (Claude, 20 Nov 2026).

Train: 2024-11-22 .. 2025-11-19 picks the rule; Test: 2025-11-20 .. 2026-11-19 is reported with the frozen choice.
Costs: 0.14% round trip per trade; funding: longs pay 0.0032%/h (Walt median), shorts receive it.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "bot/data/research/long_hourly"
SPLIT = pd.Timestamp("2025-11-20", tz="UTC")
FEE, FUND = 0.14, 0.0032          # % per round trip, % per hour paid by longs
CRYPTO = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "LINK", "AVAX", "SUI", "TRX", "XMR", "HYPE"]


def load(s):
    d = pd.read_csv(H / f"{s}.csv", index_col=0, parse_dates=True)
    d.index = pd.to_datetime(d.index, utc=True)
    return d[~d.index.duplicated()].sort_index()


def stats(r):
    r = np.asarray(r, float)
    if len(r) == 0:
        return {"n": 0}
    return {"n": int(len(r)), "avg": round(float(r.mean()), 3), "win": round(float((r > 0).mean()), 3),
            "t": round(float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))), 2) if len(r) > 2 else None,
            "sum": round(float(r.sum()), 1)}


out = {}
px = {s: load(s) for s in CRYPTO if (H / f"{s}.csv").exists()}
close = pd.DataFrame({s: d.close for s, d in px.items()}).ffill(limit=2)
opn = pd.DataFrame({s: d.open for s, d in px.items()})
lr = np.log(close).diff() * 100            # hourly % log returns

# 1) Hour-of-day: a long (or short) held over the best contiguous 6-hour UTC block chosen on train.
hod = {}
for s in close:
    r = lr[s].dropna()
    tr, te = r[r.index < SPLIT], r[r.index >= SPLIT]
    mean_by_h = tr.groupby(tr.index.hour).mean()
    best = None
    for start in range(24):
        hrs = [(start + k) % 24 for k in range(6)]
        v = mean_by_h.loc[hrs].sum()
        for side in (1, -1):
            if best is None or side * v > best[2]:
                best = (start, side, side * v)
    start, side, _ = best
    def block_returns(x):
        x = x[x.index.hour.isin([(start + k) % 24 for k in range(6)])]
        g = x.groupby(x.index.date).sum()
        g = g[x.groupby(x.index.date).count() == 6]
        return side * g - FEE - side * FUND * 6
    hod[s] = {"start_utc": start, "side": side, "train": stats(block_returns(tr)), "test": stats(block_returns(te))}
out["hour_of_day_6h_block"] = hod

# 2) Day-of-week: long or short the best weekday (UTC day, close-to-close), chosen on train.
dow = {}
daily = np.log(close.resample("1D").last()).diff() * 100
for s in daily:
    r = daily[s].dropna()
    tr, te = r[r.index < SPLIT], r[r.index >= SPLIT]
    m = tr.groupby(tr.index.dayofweek).mean()
    day = int(m.abs().idxmax()); side = 1 if m[day] > 0 else -1
    f = lambda x: side * x[x.index.dayofweek == day] - FEE - side * FUND * 24
    dow[s] = {"day": day, "side": side, "train": stats(f(tr)), "test": stats(f(te))}
out["day_of_week"] = dow

# 3) BTC leads alts: after BTC moves > k sigma in 4h, trade each alt in BTC's direction for the next h hours
#    if the alt lagged (alt 4h move smaller than beta*BTC move). Threshold and hold chosen on train, pooled.
r4 = np.log(close).diff(4) * 100
vol = lr.rolling(24 * 30).std() * 2      # 4h sigma approx
alts = [s for s in close if s != "BTC"]
def leadlag(k, hold, lo, hi):
    res = []
    fwd = (np.log(opn.shift(-1 - hold)) - np.log(opn.shift(-1))) * 100   # enter next open, exit `hold` later
    z = r4["BTC"] / vol["BTC"]
    sig = z[(z.abs() > k) & (z.index >= lo) & (z.index < hi)]
    last = {}
    for t, zz in sig.items():
        d = 1 if zz > 0 else -1
        for a in alts:
            if a in last and t < last[a]:
                continue
            gap = d * (r4["BTC"][t] - r4[a][t])          # how far the alt lagged BTC
            f = fwd[a].get(t)
            if gap > 0 and f == f and f is not None:
                res.append(d * f - FEE - d * FUND * hold)
                last[a] = t + pd.Timedelta(hours=hold)
    return res
lo0 = close.index[0]
grid = {(k, h): stats(leadlag(k, h, lo0, SPLIT)) for k in (1.5, 2, 3) for h in (2, 4, 8)}
bk = max(grid, key=lambda g: grid[g].get("avg", -9) if grid[g]["n"] >= 30 else -9)
out["btc_leads_alts"] = {"grid_train": {f"k{k}_h{h}": v for (k, h), v in grid.items()},
                         "chosen": f"k{bk[0]}_h{bk[1]}", "test": stats(leadlag(*bk, SPLIT, close.index[-1]))}

# 4) Weekend: crypto is long-paying all week; does shorting into the weekend (Fri 20:00 -> Mon 00:00 UTC) earn?
wk = {}
for s in close:
    c = close[s].dropna()
    fr = c[(c.index.dayofweek == 4) & (c.index.hour == 20)]
    rs = []
    for t, v in fr.items():
        m = c.get(t + pd.Timedelta(hours=52))
        if m is not None and m == m:
            rs.append((t, (np.log(m) - np.log(v)) * 100))
    rr = pd.Series(dict(rs))
    wk[s] = {"train_long": stats(rr[rr.index < SPLIT] - FEE - FUND * 52),
             "test_long": stats(rr[rr.index >= SPLIT] - FEE - FUND * 52),
             "test_short": stats(-rr[rr.index >= SPLIT] - FEE + FUND * 52)}
out["weekend"] = wk

# Pooled summaries for the headline.
def pool(section, key):
    vals = [v[key] for v in out[section].values() if v[key].get("n")]
    n = sum(v["n"] for v in vals)
    return {"n": n, "avg": round(sum(v["avg"] * v["n"] for v in vals) / n, 3) if n else None}
out["summary"] = {"hour_of_day_train": pool("hour_of_day_6h_block", "train"), "hour_of_day_test": pool("hour_of_day_6h_block", "test"),
                  "dow_train": pool("day_of_week", "train"), "dow_test": pool("day_of_week", "test"),
                  "weekend_short_test": pool("weekend", "test_short"), "weekend_long_test": pool("weekend", "test_long"),
                  "leadlag": out["btc_leads_alts"]["chosen"], "leadlag_test": out["btc_leads_alts"]["test"]}
(ROOT / "research/calendar_leadlag_results.json").write_text(json.dumps(out, indent=1, default=str))
print(json.dumps(out["summary"], indent=1))
print("leadlag train grid", {k: (v["n"], v.get("avg")) for k, v in out["btc_leads_alts"]["grid_train"].items()})
