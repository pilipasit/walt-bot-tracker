"""Relative momentum portfolios, fixed configurations across two archive years."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
report=[]
for year in [2022,2023]:
 prices={}
 for symbol in ["BTC","ETH","SOL","XRP"]:
  chunks=[pd.read_csv(p,header=None).iloc[:,:6] for p in sorted((ROOT/f"bot/data/research/archive_{year}").glob(f"{symbol}-{year}-*.csv"))]
  d=pd.concat(chunks,ignore_index=True)
  d.columns=["ts","open","high","low","close","volume"]
  prices[symbol]=d.set_index("ts")
 common=sorted(set.intersection(*(set(d.index) for d in prices.values())))
 opens=pd.DataFrame({s:d.open.reindex(common) for s,d in prices.items()})
 closes=pd.DataFrame({s:d.close.reindex(common) for s,d in prices.items()})
 for lookback in [24,72,168]:
  for hold in [24,72,168]:
   for mode in ["momentum","reversal"]:
    values=[]
    for i in range(168,len(common)-hold-1,hold):
     if common[i+hold+1]-common[i-lookback]!=(hold+1+lookback)*3600000:continue
     rank=(closes.iloc[i]/closes.iloc[i-lookback]-1).sort_values()
     low,high=rank.index[0],rank.index[-1]
     ret=100*(opens.iloc[i+hold+1]/opens.iloc[i+1]-1)
     gross=(ret[high]-ret[low])/2
     if mode=="reversal":gross=-gross
     values.append(gross-.34-.03*hold/24)
    report.append(dict(year=year,lookback=lookback,hold=hold,mode=mode,n=len(values),stress_mean=float(np.mean(values))))
(ROOT/"research/relative_results.json").write_text(json.dumps(report,indent=2))
a=pd.DataFrame(report)
pivot=a.pivot(index=["lookback","hold","mode"],columns="year",values="stress_mean")
print(pivot.to_string())
