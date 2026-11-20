"""Monthly cross-sectional winners and winners/losers on currently tracked markets.
Run from the repo with python research/rotation_spy.py [daily-cache-path].
"""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
from waltbot.markets import UNIVERSE, YAHOO

CACHE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Projects\walt-trading-bot\data\research\daily")
contracts = {c["base_asset"]: c for c in json.loads((ROOT/"bot/data/contracts_raw.json").read_text())["contracts"]}
groups, histories, scores, risk, confidence = {}, {}, {}, {}, {}
for symbol, group, _ in UNIVERSE:
    if symbol == "US10Y":  # Yield index cannot reproduce bond price P/L.
        continue
    ticker = YAHOO.get(symbol, symbol+"-USD" if group=="Crypto" else symbol)
    path = CACHE / (ticker.replace("^","_").replace("=","_")+".csv")
    if not path.exists() or symbol not in contracts:
        continue
    d = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    d = d.loc["2012":]
    assert d.index.is_unique
    if (d.close<=0).any() or (d.low<=0).any():
        continue
    groups[symbol], histories[symbol] = group, d
    scores[symbol] = d.close.shift(21)/d.close.shift(252)-1
    risk[symbol] = np.log(d.close).diff().rolling(60).std()
    confidence[symbol] = np.sign(d.close.diff(63)) == np.sign(scores[symbol])

symbols = list(histories)
calendar = pd.date_range("2015-01-01", "2026-10-30")
def panel(series, fill=False):
    d = pd.DataFrame(series).reindex(pd.date_range("2012-01-01","2026-10-30"))
    if fill: d = d.ffill(limit=5)
    return d.reindex(calendar).to_numpy(dtype=float)
close, high, low = [panel({s:d[k] for s,d in histories.items()}) for k in ("close","high","low")]
mark = pd.DataFrame(close).ffill().to_numpy()
score = panel(scores, True)
sigma = panel(risk, True)
confirm = panel(confidence, True)
caps = np.array([int(contracts[s]["max_leverage"]) for s in symbols])
mm = np.array([float(contracts[s]["margin"]["maintenance_margin_fraction"])/10000 for s in symbols])
spy_index = symbols.index("SPY")
sessions = calendar[np.isfinite(close[:,spy_index])]
firsts = pd.Series(sessions,index=sessions).groupby(sessions.to_period("M")).first()
# Exit on first SPY close of month; open next session, rank only through prior date.
entry_days = set(sessions[sessions.get_indexer(firsts)+1])
exit_days = set(firsts)
entry_days.discard(sessions[-1])
start = sessions[sessions.get_indexer(firsts.iloc[:1])[0]+1]
end = sessions[-1]
def simulate(side_mode, multiplier, funding=.0003, excluded_group=None, rng=None):
    cash=1.; positions={}; daily={}; ledger=[]; max_gross=0.
    for i, day in enumerate(calendar):
        if day < start: continue
        for j,p in list(positions.items()):
            qty,entry,margin,fees=p["qty"],p["entry"],p["margin"],p["cost"]
            prev=mark[i-1,j]
            fees+=abs(qty*prev)*funding
            p["cost"]=fees
            adverse=low[i,j] if qty>0 else high[i,j]
            liquidated=(np.isfinite(adverse) and margin+(adverse-entry)*qty-fees <= abs(qty)*adverse*mm[j])
            # Debiting the entire collateral on maintenance breach stresses fills.
            scheduled=day>=p["expiry"] and np.isfinite(close[i,j])
            if liquidated or scheduled or day==end:
                px=mark[i,j]
                remaining=0. if liquidated else max(0.,margin+qty*(px-entry)-fees-.0012*abs(qty*px))
                cash+=remaining
                ledger.append((str(day.date()),symbols[j],float(remaining-margin),bool(liquidated),p["leverage"],float(abs(qty*entry)),p["entered"],float(entry),float(px)))
                del positions[j]
        if day in entry_days:
            available=np.isfinite(close[i]) & np.isfinite(score[i-2]) & np.isfinite(sigma[i-2]) & (sigma[i-2]>0)
            if excluded_group is not None:
                available &= np.array([groups[s] != excluded_group for s in symbols])
            order=np.argsort(np.where(available,score[i-2],-np.inf))[::-1]
            if rng is not None:
                # Reproduce the ranked strategy's number of longs per group, then
                # sample from all age/price/volatility eligible names in that group.
                grouped = {}
                for j in order:
                    if not available[j] or j in positions or score[i-2,j]<=0: continue
                    group=groups[symbols[j]]
                    if grouped.get(group,0)>=3: continue
                    grouped[group]=grouped.get(group,0)+1
                    if sum(grouped.values())==6: break
                shuffled=[]
                for group,count in grouped.items():
                    pool=[j for j,s in enumerate(symbols) if available[j] and j not in positions and groups[s]==group]
                    shuffled.extend(rng.choice(pool,size=count,replace=False).tolist())
                order=shuffled
            per_slot=.12*cash
            chosen=set(); used={}
            expiry=next((e for e in sorted(exit_days) if e>day),end)
            for sign in ([1] if side_mode=="long" else [1,-1]):
                quota=6 if side_mode=="long" else 3
                n=0
                for j in (order if sign==1 else order[::-1]):
                    group=groups[symbols[j]]
                    if not available[j] or (rng is None and score[i-2,j]*sign<=0) or j in chosen or j in positions or used.get(group,0)>=3:continue
                    base=(.06 if confirm[i-2,j] else .03)/sigma[i-2,j]
                    leverage=1 if multiplier==0 else max(1,min(int(caps[j]),int(base*multiplier)))
                    margin=per_slot
                    qty=sign*leverage*margin/close[i,j]
                    positions[j]=dict(qty=qty,entry=close[i,j],margin=margin,cost=.0012*abs(qty*close[i,j]),expiry=expiry,leverage=leverage,entered=str(day.date()))
                    cash-=margin
                    n+=1;chosen.add(j);used[group]=used.get(group,0)+1
                    if n==quota:break
        equity=cash+sum(max(0.,p["margin"]+p["qty"]*(mark[i,j]-p["entry"])-p["cost"]) for j,p in positions.items())
        max_gross=max(max_gross,sum(abs(p["qty"]*mark[i,j]) for j,p in positions.items())/max(equity,1e-12))
        assert cash>=-1e-9
        daily[day]=equity
    return pd.Series(daily),ledger,max_gross

ticker_url="https://query1.finance.yahoo.com/v8/finance/chart/SPY"
response=requests.get(ticker_url,params=dict(period1=1417392000,period2=1793491200,interval="1d"),headers={"User-Agent":"Mozilla/5.0"},timeout=25)
response.raise_for_status()
quote=response.json()["chart"]["result"][0]
adj=pd.Series(quote["indicators"]["adjclose"][0]["adjclose"],index=pd.to_datetime(quote["timestamp"],unit="s").normalize()).dropna()
assert start in adj.index and end in adj.index
benchmark=adj.reindex(calendar).ffill().loc[start:end]/adj.loc[start]
def stats(series,base):
    curve=np.r_[base,series.to_numpy()]
    return dict(total_pct=round(100*(curve[-1]/base-1),3),max_close_dd_pct=round(100*max(1-curve/np.maximum.accumulate(curve)),3))
def report(series):
    return dict(full=stats(series,1),early=stats(series.loc[:"2021"],1),
                late=stats(series.loc["2022":],series.loc[:"2021"].iloc[-1]),
                years={str(y):stats(g,series.shift(1).fillna(1).loc[g.index[0]]) for y,g in series.groupby(series.index.year)})
result=dict(start=str(start.date()),end=str(end.date()),symbols=symbols,benchmark=report(benchmark),runs={})
for mode in ["long","long_short"]:
    for mult in [0,1,3]:
        equity,ledger,gross=simulate(mode,mult)
        result["runs"][f"{mode}/{mult}"]=dict(**report(equity),trades=len(ledger),liquidations=sum(x[3] for x in ledger),
                max_leverage=max(x[4] for x in ledger),max_gross=gross,assets_traded=sorted(set(x[1] for x in ledger)),
                leverage_by_asset={s:max(x[4] for x in ledger if x[1]==s) for s in set(x[1] for x in ledger)},
                trade_counts_year={str(y):sum(x[0].startswith(str(y)) for x in ledger) for y in range(2015,2027)})
main_equity, main_ledger, _ = simulate("long",0)
def cagr(series, base, start_day):
    return float(((series.iloc[-1]/base)**(365.25/(series.index[-1]-start_day).days)-1)*100)
result["ranked_cagr_pct"] = dict(full=cagr(main_equity,1,start),late=cagr(main_equity.loc["2022":],main_equity.loc[:"2021"].iloc[-1],pd.Timestamp("2021-12-31")))
random_runs = []
for seed in range(64):
    curve, _, _ = simulate("long",0,rng=np.random.default_rng(seed))
    random_runs.append([curve.iloc[-1],curve.iloc[-1]/curve.loc[:"2021"].iloc[-1]])
random_runs=np.array(random_runs)
ranked=np.array([main_equity.iloc[-1],main_equity.iloc[-1]/main_equity.loc[:"2021"].iloc[-1]])
result["random_placebo"] = dict(seeds=64,percentiles_full_late=(100*(np.percentile(random_runs,[5,50,95],axis=0)-1)).tolist(),fraction_random_beating_ranked=np.mean(random_runs>ranked,axis=0).tolist(),fraction_random_beating_spy=np.mean(random_runs>np.array([benchmark.iloc[-1],benchmark.iloc[-1]/benchmark.loc[:"2021"].iloc[-1]]),axis=0).tolist())
result["largest_winners"]=[]
for exit_day,symbol,pnl,liq,lev,notional,entry_day,entry_px,exit_px in sorted(main_ledger,key=lambda row:-row[2])[:10]:
    bars=histories[symbol].loc[entry_day:exit_day]
    max_gap=bars.index.to_series().diff().dt.days.max()
    result["largest_winners"].append(dict(symbol=symbol,entry=entry_day,exit=exit_day,entry_px=entry_px,exit_px=exit_px,pnl_initial_equity=pnl,max_calendar_gap=int(max_gap),max_abs_one_bar=float(bars.close.pct_change().abs().max()),raw_bars=len(bars)))
result["group_omission"] = {}
for group in ["Stocks", "Crypto"]:
    equity,ledger,gross = simulate("long",0,excluded_group=group)
    result["group_omission"][group]=report(equity)
result["assumptions"]=dict(rule="Rank trailing 252-to-21 observed bar price changes. Pick at most six positive names, max three per asset class. Long/short picks at most three winners and three losers. Each position has 12% of free equity as isolated margin. Close at first SPY session close each month, open at next session close with two calendar days of signal lag.",
                           leverage="0 means 1x contract leverage. Dynamic integer = multiplier times 0.06 / trailing 60-bar daily vol (0.03 if trailing 63-bar sign disagrees), clipped to [1, snapshot contract cap].",
                           costs="0.12% of traded notional on entry and exit, 0.03% daily of prior marked notional in adverse funding. Full collateral loss at modeled maintenance breach.",
                           limitations="Spot/future proxies with no dividends credited to derivatives. Reinvested dividends in SPY. Current listing/contract snapshot, survivorship bias, daily extremes, stale marks on holidays, uniform cost assumptions, calendar synchronization across venues. No exchange margin mode replay.")
path=ROOT/"research/rotation_spy_results.json"
path.write_text(json.dumps(result,indent=2))
for name,details in {"SPY":result["benchmark"],**result["runs"]}.items():print(name,details["full"],details["late"],details.get("liquidations"),details.get("max_leverage"))
