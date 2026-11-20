"""Broad-universe daily portfolio hypotheses with explicit cost and leverage scenarios."""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path("C:/Projects/walt-bot-tracker")
sys.path.insert(0,str(ROOT/"bot"))
from waltbot.markets import UNIVERSE, YAHOO
CACHE=Path(r"C:\Projects\walt-trading-bot\data\research\daily")
mapping={}
for symbol,group,binance in UNIVERSE:
 ticker=YAHOO.get(symbol, symbol+"-USD" if group=="Crypto" else symbol)
 path=CACHE/(ticker.replace("^","_").replace("=","_")+".csv")
 if path.exists() and symbol!="US10Y":
  d=pd.read_csv(path,index_col=0,parse_dates=True)
  d=d.loc[(d.index>="2010-01-01")&(d.index<"2026-11-20")]
  d=d[(d.open>0)&(d.close>0)]
  if len(d)>300:mapping[symbol]=(group,d)
index=pd.date_range("2011-01-01","2026-11-19",freq="D")
opens=pd.DataFrame({s:d.open.reindex(index) for s,(g,d) in mapping.items()})
close=pd.DataFrame({s:d.close.reindex(index).ffill(limit=4) for s,(g,d) in mapping.items()})
# Features use each instrument's observed trading bars.
signals={name:{} for name in ["trend126_long","hold_all"]}
for symbol,(group,d) in mapping.items():
 c=d.close; momentum=c/c.shift(126)-1
 signals["hold_all"][symbol]=pd.Series(1.0,index=c.index).where(c.shift(126).notna()).reindex(index).ffill(limit=4)
 signals["trend126_long"][symbol]=(momentum>0).astype(float).where(momentum.notna()).reindex(index).ffill(limit=4)
vol=close.pct_change(fill_method=None).rolling(60).std().replace(0,np.nan)
valued=opens.ffill()
market=valued.shift(-1)/valued-1
results=[]
for name,parts in signals.items():
 signal=pd.DataFrame(parts)
 if name=="rotation126":
  ranks=signal.rank(axis=1,pct=True)
  signal=(ranks>=.8).astype(float)-(ranks<=.2).astype(float)
 risk=signal.div(vol).replace([np.inf,-np.inf],np.nan).fillna(0)
 risk=risk.div(risk.abs().sum(axis=1).replace(0,np.nan),axis=0).fillna(0)
 # Predetermined weights are deferred until an actual opening print exists.
 target=risk.shift(1).where(opens.notna()).ffill().fillna(0)
 for leverage in [.5,1,2]:
  weight=target*leverage
  turnover=weight.diff().abs().fillna(weight.abs()).sum(axis=1)
  gross=(weight*market).sum(axis=1)
  exposure=weight.abs().sum(axis=1)
  for funding_daily in [0,.0003,.0008]:
   returns=gross-.0012*turnover-funding_daily*exposure
   returns=returns.loc["2015-01-01":].iloc[:-1]
   equity=(1+returns).cumprod()
   row=dict(rule=name,leverage=leverage,funding_daily=funding_daily,assets=len(mapping),
            max_gross=float(exposure.max()),return_pct=float((equity.iloc[-1]-1)*100),
            max_drawdown_pct=float((1-equity/equity.cummax()).max()*100),
            turnover_units=float(turnover.loc[returns.index].sum()),
            years={str(y):float(((1+r).prod()-1)*100) for y,r in returns.groupby(returns.index.year)})
   results.append(row)
report=dict(assets={s:dict(group=g,bars=len(d),first=str(d.index.min()),last=str(d.index.max())) for s,(g,d) in mapping.items()},results=results,limitations="Cached Yahoo spot/futures proxies. Present-day universe survivorship. Daily-open valuation omits intraday liquidation. Deferred opens may exceed gross target. Constant-mix daily rebalancing. Funding is a scenario, not historical. Excludes rates/yield proxy.")
pass
for r in results:
 if r["leverage"]==1:print(r["rule"],r["funding_daily"],round(r["return_pct"],1),round(r["max_drawdown_pct"],1),r["assets"])
for r in results:
 if r["leverage"]==1:print(r["rule"],"funding/day",r["funding_daily"],"return",round(r["return_pct"]),"maxDD",round(r["max_drawdown_pct"]),{y:round(v) for y,v in r["years"].items() if y>="2022"})
