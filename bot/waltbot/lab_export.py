"""Write the data files behind the tracker's Strategy lab and Research pages into the site repo."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from . import multiasset as m
from .contracts import ROOT

SITE = ROOT.parent / "walt-bot-tracker"


def _r(x, nd):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


def export() -> dict:
    out = SITE / "data"
    (out / "daily").mkdir(parents=True, exist_ok=True)
    data = m.load_all()
    index = []
    for name, df in data.items():
        df = df.dropna(subset=["open", "high", "low", "close"])
        ref = float(df.close.median())
        nd = 2 if ref >= 10 else 4 if ref >= 0.1 else 6
        rec = {"t": [int(t.timestamp() // 86400) for t in df.index],
               "o": [_r(x, nd) for x in df.open], "h": [_r(x, nd) for x in df.high], "l": [_r(x, nd) for x in df.low],
               "c": [_r(x, nd) for x in df.close], "v": [int(x) if x == x else 0 for x in df.volume.fillna(0)]}
        (out / "daily" / f"{name}.json").write_text(json.dumps(rec, separators=(",", ":")), encoding="utf-8")
        index.append({"asset": name, "class": df.attrs["cls"], "ticker": m.ASSETS[name][0],
                      "from": str(df.index[0].date()), "to": str(df.index[-1].date()), "bars": len(df)})
    vix = m.yahoo("^VIX").close.dropna()
    (out / "daily" / "_VIX.json").write_text(json.dumps({"t": [int(t.timestamp() // 86400) for t in vix.index], "c": [round(float(x), 2) for x in vix]}, separators=(",", ":")), encoding="utf-8")
    f = m.fear_greed()
    (out / "daily" / "_FNG.json").write_text(json.dumps({"t": [int(t.timestamp() // 86400) for t in f.index], "c": [int(x) for x in f]}, separators=(",", ":")), encoding="utf-8")
    (out / "assets.json").write_text(json.dumps(index), encoding="utf-8")
    rows = json.loads((ROOT / "data" / "research" / "multiasset.json").read_text(encoding="utf-8"))
    keep = ["asset", "class", "rule", "funding", "years", "cagr", "sharpe", "maxdd", "t", "trades_per_year", "time_in_market",
            "buyhold_cagr", "to 2014", "2015-19", "2020-22", "2023-now"]
    slim = [{k: r.get(k) for k in keep} for r in rows]
    for r in slim:
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                r[k] = None
    (out / "research.json").write_text(json.dumps(slim, separators=(",", ":")), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.rglob("*.json"))
    return {"assets": len(index), "bytes": size}
