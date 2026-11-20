"""Discovery on 2022, evaluation on 2023 for event-based reversal filters."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
data={}
for year in [2022,2023]:
 data[year]=[]
 for symbol in ["BTC","ETH","SOL","XRP"]:
  frames=[pd.read_csv(p,header=None).iloc[:,:6] for p in sorted((ROOT/f"bot/data/research/archive_{year}").glob(f"{symbol}-{year}-*.csv"))]
  d=pd.concat(frames,ignore_index=True)
  d.columns=["ts","open","high","low","close","volume"]
  r=np.log(d.close).diff()
  d["strength"]=np.log(d.close).diff(120)/(r.rolling(72).std()*np.sqrt(120))
  d["vol"]=r.rolling(72).std()
  d["rebound"]=r
  data[year].append(d)
results=[]
for threshold in [1,2,3]:
 for hold in [12,24,48]:
  for rebound in [False,True]:
   scores={}
   for year,frames in data.items():
    returns=[]
    for d in frames:
     available=0
     for i in range(168,len(d)-hold-1):
      if i<available or d.strength.iloc[i]>-threshold or d.vol.iloc[i]>.02:continue
      if rebound and d.rebound.iloc[i]<=0:continue
      if not (d.ts.iloc[i-168:i+hold+2].diff().dropna()==3600000).all():continue
      returns.append(100*(d.open.iloc[i+hold+1]/d.open.iloc[i+1]-1)-.34-.03*hold/24)
      available=i+hold
    scores[str(year)]={"n":len(returns),"stress_mean":float(np.mean(returns))}
   results.append(dict(threshold=threshold,hold=hold,rebound=rebound,scores=scores))
results.sort(key=lambda r:r["scores"]["2022"]["stress_mean"],reverse=True)
(ROOT/"research/rebound_results.json").write_text(json.dumps(results,indent=2))
print(json.dumps(results[:5],indent=2))
