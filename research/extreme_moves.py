"""After an extreme 7-day move, does the price keep going or bounce? (Claude, 20 Nov 2026)

Daily data for the Walt universe (C:/Projects/walt-trading-bot/data/research/daily). Move size z = 7-day log return /
(daily volatility over the prior 60 days x sqrt(7)). For days with |z| >= 2, 3, 4, measure the next 1, 3, 5 and 10
trading days' return in the SAME direction as the move (positive = continuation, negative = bounce), entering at the
next day's open. Earlier period 2012-2019, later period 2020-2026, both reported; no parameters are fitted.
Compared with the average same-direction drift of all days (baseline). Costs ignored here (it's a description of
behaviour, not a trade); subtract about 0.14% for a Walt round trip.
"""
import json
from pathlib import Path
import numpy as np, pandas as pd

D = Path(r"C:\Projects\walt-trading-bot\data\research\daily")
rows = []
for f in sorted(D.glob("*.csv")):
    if f.stem in ("_TNX", "fear_greed"):
        continue
    d = pd.read_csv(f, index_col=0, parse_dates=True)
    d.index = pd.to_datetime(d.index).tz_localize(None)
    d = d[(d.open > 0) & (d.close > 0)].loc["2012":]
    if len(d) < 400:
        continue
    lc = np.log(d.close)
    vol = lc.diff().rolling(60).std().shift(7)
    z = (lc - lc.shift(7)) / (vol * np.sqrt(7))
    o = d.open.values
    for i in range(70, len(d) - 11):
        zz = z.iloc[i]
        if not np.isfinite(zz):
            continue
        s = 1 if zz > 0 else -1
        fwd = {h: s * (np.log(d.close.iloc[i + h]) - np.log(o[i + 1])) * 100 for h in (1, 3, 5, 10)}
        rows.append((f.stem, d.index[i], abs(zz), s, fwd))

df = pd.DataFrame([{"asset": a, "t": t, "absz": z, "side": s, **{f"f{h}": v for h, v in fw.items()}} for a, t, z, s, fw in rows])
crypto = df.asset.str.endswith("-USD")
out = {}
for period, lo, hi in (("2012-2019", "2012", "2019"), ("2020-2026", "2020", "2026")):
    p = df[(df.t >= lo) & (df.t <= hi + "-12-31")]
    base = {f"f{h}": round(float(p[f"f{h}"].mean()), 3) for h in (1, 3, 5, 10)}
    res = {"baseline_all_days": base}
    for k in (2, 3, 4):
        for side, name in ((-1, "after_drops"), (1, "after_rallies")):
            for grp, mask in (("all", slice(None)), ("crypto", crypto), ("non_crypto", ~crypto)):
                q = p[(p.absz >= k) & (p.side == side)]
                q = q[mask.loc[q.index]] if grp != "all" else q
                if len(q) < 20:
                    continue
                # one event per asset per 5 days so clustered days don't count many times
                q = q.sort_values("t").groupby("asset", group_keys=False).apply(
                    lambda g: g[g.t.diff().dt.days.fillna(99) >= 5])
                st = {}
                for h in (1, 3, 5, 10):
                    v = q[f"f{h}"]
                    st[f"{h}d"] = {"avg": round(float(v.mean()), 2), "cont_share": round(float((v > 0).mean()), 3),
                                   "t": round(float(v.mean() / v.std() * np.sqrt(len(v))), 2)}
                res[f"z{k}_{name}_{grp}"] = {"events": int(len(q)), **st}
    out[period] = res
Path(__file__).with_name("extreme_moves_results.json").write_text(json.dumps(out, indent=1))
for per, res in out.items():
    print("==", per, "baseline", res["baseline_all_days"])
    for k, v in res.items():
        if k.startswith("z"):
            print(f"{k:32s} n={v['events']:5d}  " + "  ".join(f"{h}: {v[h]['avg']:+.2f}% ({v[h]['cont_share']:.0%}, t={v[h]['t']})" for h in ("1d", "3d", "5d", "10d")))
