"""Crypto "big up-week" momentum as a portfolio (Claude, 20 Nov 2026).

Rule (thresholds fixed in advance from extreme_moves.py, not fitted): at each daily close, for every crypto asset whose
7-day move is >= Z x its normal week (z = 7-day log move / (60-day daily vol x sqrt 7)), buy at the next open and hold
H days (no overlap per asset). Capital is split into N equal slots (N = number of crypto assets); a slot earns the
trade's return, idle slots earn 0. Costs: 0.14% per round trip + Walt funding 0.03%/day on longs (the upper
common rate; the usual rate is 0.01%). Leverage 1x. Reported per year with max drawdown (daily marks), against
buy-and-hold of the same assets (equal weight, rebalanced daily) and against a random-entry version with the same
number of trades (median of 100 runs).
"""
import json, random
from pathlib import Path
import numpy as np, pandas as pd

D = Path(r"C:\Projects\walt-trading-bot\data\research\daily")
coins = sorted(f.stem for f in D.glob("*-USD.csv"))
px = {}
for c in coins:
    d = pd.read_csv(D / f"{c}.csv", index_col=0, parse_dates=True)
    d.index = pd.to_datetime(d.index).tz_localize(None)
    d = d[(d.open > 0) & (d.close > 0)].loc["2016":]
    if len(d) > 300:
        px[c] = d
idx = pd.date_range("2017-01-01", "2026-11-19", freq="D")
O = pd.DataFrame({c: d.open.reindex(idx) for c, d in px.items()})
C = pd.DataFrame({c: d.close.reindex(idx) for c, d in px.items()})
N = len(px)
lc = np.log(C)
vol = lc.diff().rolling(60, min_periods=40).std().shift(7)
Z = (lc - lc.shift(7)) / (vol * np.sqrt(7))


def run(z_min, hold, fee=0.14, fund=0.03, entries=None):
    """Daily portfolio returns. entries: optional {asset: [dates]} to replace the rule (random baseline)."""
    w = pd.DataFrame(0.0, index=idx, columns=C.columns)        # 1 when the slot holds the asset that day
    trades = []
    for c in C.columns:
        days = entries[c] if entries else list(Z.index[(Z[c] >= z_min).fillna(False)])
        busy_until = None
        for t in days:
            i = idx.get_loc(t)
            if busy_until is not None and i <= busy_until or i + 1 + hold >= len(idx):
                continue
            o, cl = O[c].iloc[i + 1], C[c].iloc[i + hold]
            if not (o > 0 and cl > 0):
                continue
            w.iloc[i + 1:i + 1 + hold, w.columns.get_loc(c)] = 1.0
            trades.append((t, c, (cl / o - 1) * 100 - fee - fund * hold))
            busy_until = i + hold
    # daily mark-to-market: entry day uses open->close, then close->close
    r_cc = C.pct_change(fill_method=None)
    r_oc = C / O - 1
    first = w.diff().fillna(w) > 0
    daily = (w * np.where(first, r_oc, r_cc)).fillna(0)
    port = daily.sum(axis=1) / N
    # costs per trade spread on its entry day (fee) and holding days (funding)
    port -= (first.sum(axis=1) * fee / 100) / N + (w.sum(axis=1) * fund / 100) / N
    return port, trades


def stats(port):
    eq = (1 + port).cumprod()
    yrs = {str(y): round(float(((1 + r).prod() - 1) * 100), 1) for y, r in port.groupby(port.index.year)}
    dd = float((1 - eq / eq.cummax()).max() * 100)
    return {"total_pct": round(float((eq.iloc[-1] - 1) * 100), 1), "max_dd_pct": round(dd, 1), "years": yrs}


bh = C.pct_change(fill_method=None).mean(axis=1).fillna(0)
out = {"assets": list(px), "buy_and_hold_equal_weight": stats(bh), "rules": {}}
for z_min, hold in ((2, 3), (2, 10), (3, 10)):
    port, tr = run(z_min, hold)
    t = np.array([x[2] for x in tr])
    rnd = []
    rng = random.Random(7)
    for _ in range(100):
        ent = {c: [] for c in C.columns}
        for (_, c, _) in tr:
            valid = C[c].dropna().index[70:-hold - 2]
            ent[c].append(rng.choice(list(valid)))
        ent = {c: sorted(v) for c, v in ent.items()}
        rnd.append(stats(run(z_min, hold, entries=ent)[0])["total_pct"])
    out["rules"][f"z>={z_min}_hold{hold}d"] = {
        "trades": int(len(t)), "avg_trade_pct": round(float(t.mean()), 2), "win_rate": round(float((t > 0).mean()), 3),
        "median_trade_pct": round(float(np.median(t)), 2), **stats(port),
        "random_same_count_median_total_pct": round(float(np.median(rnd)), 1),
        "no_funding_total_pct": stats(run(z_min, hold, fund=0)[0])["total_pct"]}
Path(__file__).with_name("crypto_upweek_results.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
