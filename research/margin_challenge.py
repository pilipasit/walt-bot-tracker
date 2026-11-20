"""Intraday continuation after a breakout with equal margin slots and isolated liquidations."""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
from waltbot.markets import UNIVERSE, YAHOO

START = pd.Timestamp("2015-01-01")
END = pd.Timestamp("2025-12-31")
INPUT = Path(r"C:\Projects\walt-trading-bot\data\research\daily")
spec = {v["base_asset"]: v for v in json.loads((ROOT/"bot/data/contracts_raw.json").read_text())["contracts"]}
all_rows = []
for symbol, group, _ in UNIVERSE:
    ticker = YAHOO.get(symbol, symbol+"-USD" if group=="Crypto" else symbol)
    path = INPUT/(ticker.replace("^","_").replace("=","_")+".csv")
    if symbol == "US10Y" or not path.exists() or symbol not in spec:
        continue
    frame = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    frame = frame.loc[frame.index < END + pd.Timedelta(days=1)]
    high = frame.high.rolling(20).max().shift()
    low = frame.low.rolling(20).min().shift()
    size = frame.close.pct_change().rolling(60).std()
    side = (frame.close > high).astype(int) - (frame.close < low).astype(int)
    frame["side"] = side.shift()
    frame["vol"] = size.shift()
    frame["strong"] = (abs(frame.close.pct_change(20)) > size * np.sqrt(20)).shift()
    frame = frame.loc[(frame.index >= START) & frame.side.isin([-1,1]) & frame.vol.gt(0)].dropna()
    for day, bar in frame.iterrows():
        if min(bar.low, bar.open, bar.close) <= 0 or bar.high < max(bar.open,bar.close) or bar.low > min(bar.open,bar.close):
            continue
        side = int(bar.side)
        all_rows.append(dict(day=day,symbol=symbol,side=side,vol=bar.vol,strong=bool(bar.strong),
                             adverse=(bar.low/bar.open-1 if side==1 else 1-bar.high/bar.open),
                             gross=side*(bar.close/bar.open-1)))
rows = pd.DataFrame(all_rows)
# Every instrument has a dedicated equal slot. No asset needs another market's same-day open.
slots = len(rows.symbol.unique())
cache = ROOT/"research/margin_challenge_spy.csv"
if not cache.exists():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/SPY"
    raw = requests.get(url, params={"period1":1419465600,"period2":1767398400,"interval":"1d"},
                       timeout=20,headers={"User-Agent":"Mozilla/5.0"}).json()["chart"]["result"][0]
    data = pd.Series(raw["indicators"]["adjclose"][0]["adjclose"],
                     index=pd.to_datetime(raw["timestamp"],unit="s",utc=True).normalize().tz_localize(None),name="adjusted_close")
    data.dropna().to_csv(cache)
spy = pd.read_csv(cache,index_col=0,parse_dates=True).iloc[:,0].sort_index()
dates = pd.date_range(START,END)
bench = spy.reindex(pd.date_range(START-pd.Timedelta(days=1),END)).ffill().pct_change().loc[dates]
out = {}
for policy in ["unlevered","vol_target","signal_scaled","venue_max"]:
    copy = rows.copy()
    cap = copy.symbol.map(lambda s: spec[s]["max_leverage"])
    mm = copy.symbol.map(lambda s:float(spec[s]["margin"]["maintenance_margin_fraction"])/10000)
    fee = copy.symbol.map(lambda s:float(spec[s]["taker_fee_pct"]))+.0005
    if policy == "unlevered":
        lev = np.ones(len(copy))
    elif policy == "venue_max":
        lev = cap
    else:
        target = .04 if policy == "vol_target" else np.where(copy.strong,.08,.02)
        lev = np.minimum(cap, np.maximum(1,np.floor(target/copy.vol)))
    copy["lev"] = lev
    # Flat at each close. One full day of adverse funding. Entry and exit costs at traded notional.
    copy["liq"] = 1/lev + copy.adverse - fee - .0003 <= mm * (1+copy.side*copy.adverse)
    ordinary = lev * (copy.gross - fee*(2+copy.side*copy.gross)-.0003)
    copy["net_margin"] = np.where(copy.liq,-1,np.maximum(-1,ordinary))
    daily = copy.groupby("day").net_margin.sum().reindex(dates,fill_value=0)/slots
    curve = (1+daily).cumprod()
    dd = 1-curve/curve.cummax().clip(lower=1)
    out[policy] = dict(entries=len(copy),long=int((copy.side==1).sum()),short=int((copy.side==-1).sum()),
                       liquidations=int(copy.liq.sum()),leverage_range=[float(np.min(lev)),float(np.max(lev))],
                       return_pct=float((curve.iloc[-1]-1)*100),max_close_drawdown_pct=float(dd.max()*100),
                       yearly={str(y):round(100*((1+r).prod()-1),3) for y,r in daily.groupby(daily.index.year)})
bench_curve=(1+bench).cumprod()
report=dict(start=str(START.date()),end=str(END.date()),symbols=sorted(rows.symbol.unique()),slots=slots,
            spy_return_pct=float((bench_curve.iloc[-1]-1)*100),spy_drawdown_pct=float((1-bench_curve/bench_curve.cummax().clip(lower=1)).max()*100),
            benchmark_years={str(y):round(100*((1+r).prod()-1),3) for y,r in bench.groupby(bench.index.year)},policies=out)
(ROOT/"research/margin_challenge_results.json").write_text(json.dumps(report,indent=2))
print("SPY",report["spy_return_pct"],report["spy_drawdown_pct"],"slots",slots)
for key,r in out.items(): print(key, {k:v for k,v in r.items() if k!="yearly"})
