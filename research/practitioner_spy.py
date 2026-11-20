"""Test a first-hour SPY relative-strength adaptation on current Walt US stock proxies."""
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"bot"))
from waltbot.markets import UNIVERSE

CACHE = ROOT/"bot/data/research/long_hourly"
TICKERS = [s for s,g,_ in UNIVERSE if g=="Stocks"]
ROUNDTRIP = .0024
SOURCE = "https://www.reddit.com/r/RealDayTrading/comments/qo1gr6/the_biggest_tip_i_can_give_to_day_traders/"

def bars(symbol):
    df = pd.read_csv(CACHE/f"{symbol}.csv",index_col=0,parse_dates=True)
    df.index = df.index.tz_convert("America/New_York")
    df = df[~df.index.duplicated()].sort_index()
    def at(time, column):
        rows = df.at_time(time)[column].copy()
        rows.index = rows.index.normalize()
        return rows
    planned = pd.DatetimeIndex([d.replace(hour=12,minute=30) for d in at("10:30","open").index])
    positions = df.index.searchsorted(planned)
    available = positions < len(df)
    actual = df.iloc[positions[available]]
    exit_open = pd.Series(actual.open.to_numpy(), index=planned[available].normalize())
    duration = pd.Series(((actual.index - (planned[available] - pd.Timedelta(hours=2))).total_seconds()/86400), index=planned[available].normalize())
    return pd.DataFrame({"first":at("09:30","close")/at("09:30","open")-1,
                         "entry":at("10:30","open"),"exit":exit_open,"days_held":duration})

def benchmark(days):
    cache=ROOT/"research/practitioner_spy_benchmark.json"
    if not cache.exists():
        url="https://query1.finance.yahoo.com/v8/finance/chart/SPY"
        response=requests.get(url,headers={"User-Agent":"Mozilla/5.0"},params={"interval":"1d","period1":"1727740800","period2":"1795132800"},timeout=15)
        response.raise_for_status()
        cache.write_text(response.text)
    raw=json.loads(cache.read_text())["chart"]["result"][0]
    dates=pd.to_datetime(raw["timestamp"],unit="s",utc=True).tz_convert("America/New_York").normalize()
    close=pd.Series(raw["indicators"]["adjclose"][0]["adjclose"],index=dates).dropna()
    return close.pct_change().loc[days]

def summarize(daily):
    eq=pd.concat([pd.Series([1.0]),(1+daily).cumprod()],ignore_index=True)
    return {"total_return_pct":round((eq.iloc[-1]-1)*100,3),"daily_max_dd_pct":round((1-eq/eq.cummax()).max()*100,3)}

def main():
    market=bars("SPY")["first"].dropna()
    universe={s:bars(s) for s in TICKERS}
    days=market.index[market.index>="2025-01-01"]
    baseline=benchmark(days)
    results={}
    for mode in ["follow","fade","follow_long_only"]:
        returns=[]; counts=[]; gross_returns=[]
        for date in days:
            direction=np.sign(market.loc[date])
            ranked=[]
            for symbol,frame in universe.items():
                if date not in frame.index:continue
                row=frame.loc[date]
                signed_excess=direction*(row["first"]-market.loc[date])
                if signed_excess>0 and direction*(row["first"])>0 and pd.notna(row.entry):
                    ranked.append((signed_excess,symbol))
            selected=sorted(ranked,reverse=True)[:3] if mode!="follow_long_only" or direction>0 else []
            pnl=0
            gross_pnl=0
            for _,symbol in selected:
                row=universe[symbol].loc[date]
                if pd.isna(row.exit):raise RuntimeError(f"missing selected exit: {symbol} {date}")
                side=-direction if mode=="fade" else direction
                gross_pnl+=side*(row.exit/row.entry-1)/3
                pnl+=(side*(row.exit/row.entry-1)-ROUNDTRIP-.0003*row.days_held)/3
            returns.append(pnl); counts.append(len(selected)); gross_returns.append(gross_pnl)
        daily=pd.Series(returns,index=days)
        results[mode]={str(year):{**summarize(part),"trades":int(pd.Series(counts,index=days).loc[part.index].sum()),"spy":summarize(baseline.loc[part.index])} for year,part in daily.groupby(daily.index.year)}
        results[mode]["before_costs"]=summarize(pd.Series(gross_returns,index=days))
        results[mode]["full"]={**summarize(daily),"trades":sum(counts),"spy":summarize(baseline)}
    report={"dates":[str(days[0]),str(days[-1])],"source":SOURCE,"universe":TICKERS,"results":results,"notes":"Present-day constituents; Yahoo regular-session stock candles; SPY adjusted close prior session to final close. Daily start flat; score at first-hour close, execution next bar open, assumed 12bps per side + 3bps/day funding; max 3 concurrent one-third-equity slots. Pooled daily returns compound. Drawdown on daily closes. Approximates discretionary practitioner idea. Exit uses next available open at/after 12:30, including possible delayed exits. No volume trigger or historical Walt quotes."}
    (ROOT/"research/practitioner_spy_results.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(results))

if __name__=="__main__":
    main()
