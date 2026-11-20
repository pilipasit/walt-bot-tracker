"""Collect and evaluate hourly proxy bars for the tracker's present-day universe."""
import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
from waltbot.markets import UNIVERSE, YAHOO

CACHE = ROOT / "bot/data/research/long_hourly"
OUT = Path(__file__).parent / "long_hourly_results.json"
START = pd.Timestamp("2024-11-22", tz="UTC")
END = pd.Timestamp("2026-11-20", tz="UTC")
# Use an alternative setup that waits for short-term weakness within a slower trend.
RULES = ["follow24", "follow120", "pullback", "reverse24", "long"]


def collect(asset):
    symbol, group, _ = asset
    ticker = "SUI20947-USD" if symbol == "SUI" else YAHOO.get(symbol, symbol + "-USD" if group == "Crypto" else symbol)
    path = CACHE / (symbol + ".csv")
    if not path.exists():
        url = "https://query1.finance.yahoo.com/v8/finance/chart/" + ticker
        response = requests.get(url, params=dict(interval="1h", period1=int(START.timestamp()),
                                period2=int(END.timestamp())), headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response.raise_for_status()
        raw = response.json()["chart"]["result"][0]
        frame = pd.DataFrame(raw["indicators"]["quote"][0], index=pd.to_datetime(raw["timestamp"], unit="s", utc=True))
        frame = frame.loc[(frame.index >= START) & (frame.index < END)].sort_index()
        frame = frame.loc[~frame.index.duplicated()].dropna(subset=["open","high","low","close"])
        frame = frame[(frame.low > 0) & (frame.low <= frame[["open","close"]].min(axis=1)) &
                      (frame.high >= frame[["open","close"]].max(axis=1))]
        if len(frame) < 200: raise ValueError("too few usable bars")
        frame.to_csv(path)
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    return symbol, group, ticker, frame, hashlib.sha256(path.read_bytes()).hexdigest()


def simulate(symbol, group, frame):
    close = frame.close
    fast = np.sign(close.diff(24))
    slow = np.sign(close.diff(120))
    dip = np.sign(close.diff(4))
    result = []
    # Fixed opportunities every eight observed bars. Restart flat at each exit.
    for i in range(120, len(frame)-9, 8):
        entry, exit_time = frame.index[i+1], frame.index[i+9]
        if (entry-frame.index[i]).total_seconds() > 3600: continue
        gross = 100*(frame.open.iloc[i+9]/frame.open.iloc[i+1]-1)
        sides = [fast.iloc[i], slow.iloc[i], slow.iloc[i] if slow.iloc[i]*dip.iloc[i]<0 else 0,
                 -fast.iloc[i], 1]
        result.append([symbol,group,entry,exit_time,gross,*sides])
    return result


def summarize(df, rule):
    side = df[rule]
    active = side != 0
    pnl = side * df.gross - .24*active
    elapsed = (df.exit-df.entry).dt.total_seconds()/86400
    stress = pnl - active*(.10 + .03*elapsed)
    return dict(trades=int(active.sum()), mean_trade=round(pnl[active].mean(),4),
                mean_opportunity=round(pnl.mean(),4),stress_opportunity=round(stress.mean(),4),
                same_entry_long=round((df.gross[active]-.24).mean(),4),
                same_entry_short=round((-df.gross[active]-.24).mean(),4))


def carry_test(sample, rule):
    # Constant starting notional per uninterrupted trade, no leverage or rebalancing.
    trades = []
    for _, series in sample.groupby("symbol"):
        current = None
        for row in series.sort_values("entry").itertuples():
            side = getattr(row, rule)
            if current and (side != current[0] or row.entry != current[2]):
                trades.append(current)
                current = None
            if side:
                factor = 1 + row.gross / 100
                if current:
                    current = (side, current[1], row.exit, current[3]*factor)
                else:
                    current = (side, row.entry, row.exit, factor)
        if current:
            trades.append(current)
    net = np.array([side*(factor-1)*100 - .12*(1+factor) for side, start, end, factor in trades])
    days = np.array([(end-start).total_seconds()/86400 for _,start,end,_ in trades])
    return dict(trades=len(net), mean_trade=round(float(net.mean()),4),
                mean_opportunity=round(float(net.sum()/len(sample)),4),
                stress_trade=round(float((net-.1-.03*days).mean()),4),
                median_trade=round(float(np.median(net)),4))


def main():
    CACHE.mkdir(exist_ok=True,parents=True)
    manifest, failures, rows = {}, {}, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {a[0]:pool.submit(collect,a) for a in UNIVERSE}
        for symbol, job in jobs.items():
            try:
                _, group, ticker, frame, digest = job.result()
            except (requests.RequestException,ValueError,KeyError) as err:
                failures[symbol] = str(err)
                continue
            manifest[symbol] = dict(ticker=ticker, bars=len(frame), first=str(frame.index.min()),
                                    last=str(frame.index.max()), sha256=digest)
            rows.extend(simulate(symbol,group,frame))
    df = pd.DataFrame(rows,columns=["symbol","group","entry","exit","gross",*RULES])
    split = pd.Timestamp("2026-01-01",tz="UTC")
    valid = df[df.exit<split]
    test = df[df.entry>=split]
    validation = {r:summarize(valid,r) for r in RULES}
    selected = max(RULES[:-1],key=lambda r: validation[r]["mean_opportunity"])
    results = dict(manifest=manifest,failures=failures,selected=selected,validation=validation,
                   test={r:summarize(test,r) for r in RULES},
                   by_group={k:summarize(g,selected) for k,g in test.groupby("group")},
                   by_month={str(k):summarize(g,selected) for k,g in test.groupby(test.entry.dt.strftime("%Y-%m"))})
    results["continuous"] = {r: {k:carry_test(sub,r) for k,sub in [("validation",valid),("test",test)]} for r in RULES}
    OUT.write_text(json.dumps(results,indent=2))
    print(len(manifest),sum(v["bars"] for v in manifest.values()),failures,selected)
    print(validation,results["test"],results["by_group"])
if __name__ == "__main__":
    main()
