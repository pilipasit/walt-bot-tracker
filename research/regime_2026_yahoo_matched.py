"""Portfolio audit of the frozen event-driven large-move long rule."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
out={}
for year in [2026]:
 curves=[]
 trades=[]
 for symbol in ["BTC","ETH","SOL","XRP"]:
  d=pd.read_csv(ROOT/f"bot/data/research/long_hourly/{symbol}.csv",index_col=0,parse_dates=True)
  d["ts"]=d.index.astype("int64")//1000000
  d=d[(d.ts>=1767225600000)&(d.ts<1793491200000)].reset_index(drop=True)
  if d.ts.median()>1e14:
   d["ts"]=d.ts//1000
  regime=np.sign(d.close.diff(720))
  returns=np.log(d.close).diff()
  vol=returns.rolling(72).std()
  strength=np.log(d.close).diff(120)/(vol*np.sqrt(120))
  pnl=pd.Series(0.,index=pd.to_datetime(d.ts,unit="ms",utc=True))
  available=0
  for i in range(720,len(d)-26):
   if pd.to_datetime(d.ts.iloc[i],unit="ms").year!=year:continue
   if i<available or abs(strength.iloc[i])<2 or vol.iloc[i]>.02:continue
   if np.sign(strength.iloc[i])!=regime.iloc[i]:continue
   if not (d.ts.iloc[i-720:i+27].diff().dropna()==3600000).all():continue
   entry=d.open.iloc[i+1]
   net=100*(d.open.iloc[i+25]/entry-1)-.37
   trades.append(net)
   # Each symbol has a fixed quarter of starting capital, mark to market hourly.
   for j in range(i+1,i+25):
    change=(d.open.iloc[j+1]-d.open.iloc[j])/entry
    pnl.iloc[j]+=.25*(change-.0003/24)
   pnl.iloc[i+1]-=.25*.0017
   pnl.iloc[i+24]-=.25*.0017
   available=i+24
  curves.append(pnl)
 portfolio=pd.concat(curves,axis=1).fillna(0).sum(axis=1)
 equity=1+portfolio.cumsum()
 dd=equity/equity.cummax()-1
 daily=portfolio.groupby(portfolio.index.date).sum()
 rng=np.random.default_rng(42)
 weeks=portfolio.groupby(portfolio.index.strftime("%Y-%W")).sum().to_numpy()
 samples=rng.integers(0,len(weeks),size=(5000,len(weeks)))
 boot=weeks[samples].sum(axis=1)*100
 out[year]=dict(trades=len(trades),return_on_starting_capital_pct=float((equity.iloc[-1]-1)*100),
                max_drawdown_pct=float(-dd.min()*100),mean_stressed_trade_pct=float(np.mean(trades)),
                without_top5_mean_pct=float(np.mean(sorted(trades)[:-5])),
                weekly_bootstrap_return_95ci=np.quantile(boot,[.025,.975]).tolist(),
                profitable_days=int((daily>0).sum()),active_days=int((daily!=0).sum()))
(ROOT/"research/regime_2026_yahoo_matched_results.json").write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
