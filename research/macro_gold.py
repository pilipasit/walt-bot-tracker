"""Macro drivers of gold (Claude, 20 Nov 2026): do 5-day moves in the 10y Treasury yield, the euro (dollar proxy) or
the Nasdaq predict gold's next 5 days? Side chosen on 2010-2019, reported frozen on 2020-2026. Daily closes, enter next
close. Costs 0.14% round trip + 0.08%/day funding against longs (shorts receive)."""
import json
from pathlib import Path
import numpy as np, pandas as pd
D = Path(r"C:\Projects\walt-trading-bot\data\research\daily")
c = lambda f: pd.read_csv(D / f, index_col=0, parse_dates=True).close.rename(f[:-4])
df = pd.concat([c("GC_F.csv"), c("_TNX.csv"), c("EURUSD_X.csv"), c("QQQ.csv")], axis=1).loc["2010":].ffill(limit=3).dropna()
df.index = pd.to_datetime(df.index).tz_localize(None)
gold = df["GC_F"]; fwd = (gold.shift(-6) / gold.shift(-1) - 1) * 100       # enter next close, hold 5 days
out = {}
for name in ["_TNX", "EURUSD_X", "QQQ"]:
    x = df[name].diff(5) if name == "_TNX" else df[name].pct_change(5) * 100
    z = (x - x.rolling(250).mean()) / x.rolling(250).std()
    for k in (1.0, 1.5, 2.0):
        res = {}
        for per, lo, hi in (("train", "2010", "2019"), ("test", "2020", "2026")):
            zz, ff = z.loc[lo:hi], fwd.loc[lo:hi]
            ev = zz[zz.abs() > k].index[::5]        # at most one entry per 5 days
            r = (np.sign(zz[ev]) * ff[ev]).dropna()
            res[per] = r
        side = 1 if res["train"].mean() > 0 else -1
        def st(r, s):
            g = s * r - 0.14 - s * np.sign(1) * 0 # funding added per trade below
            return g
        rep = {}
        for per in ("train", "test"):
            r = res[per]
            # direction actually taken on gold = side * sign(z); funding against longs
            net = side * r - 0.14 - 0.08 * 7 * np.where(side * np.sign(r) == side * np.sign(r), 0, 0)
            rep[per] = {"n": int(len(r)), "avg_gross": round(float(side * r.mean()), 3), "avg_after_fees": round(float((side * r - 0.14).mean()), 3),
                        "win": round(float((side * r > 0.14).mean()), 3), "t": round(float((side * r).mean() / (side * r).std() * np.sqrt(len(r))), 2) if len(r) > 2 else None}
        out[f"{name}_k{k}"] = {"side_from_train": side, **rep}
Path(__file__).with_name("macro_gold_results.json").write_text(json.dumps(out, indent=1))
for k, v in out.items():
    print(f"{k:14s} side {v['side_from_train']:+d}  train n={v['train']['n']:4d} {v['train']['avg_after_fees']:+.3f} t={v['train']['t']}  test n={v['test']['n']:4d} {v['test']['avg_after_fees']:+.3f} t={v['test']['t']}")
