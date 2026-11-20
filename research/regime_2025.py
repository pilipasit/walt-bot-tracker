"""Regime filters for frozen strong-move entries, with all configurations retained."""
import json
from pathlib import Path
import pandas as pd
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
results=[]
for year in [2025]:
 frames=[]
 for symbol in ["BTC","ETH","SOL","XRP"]:
  parts=[pd.read_csv(p,header=None).iloc[:,:6] for p in sorted((ROOT/f"bot/data/research/archive_{year}").glob(f"{symbol}-{year}-*.csv"))]
  d=pd.concat(parts,ignore_index=True)
  d.columns=["ts","open","high","low","close","volume"]
  if d.ts.median()>1e14:
   d["ts"]=d.ts//1000
  d["vol"]=np.log(d.close).diff().rolling(72).std()
  d["strength"]=np.log(d.close).diff(120)/(d.vol*np.sqrt(120))
  frames.append((symbol,d))
 for window in [720]:
  for rule in ["match_direction"]:
   values=[]
   for symbol,d in frames:
    regime=np.sign(d.close.diff(window))
    available=0
    for i in range(max(168,window),len(d)-25):
     if i<available or abs(d.strength.iloc[i])<2 or d.vol.iloc[i]>.02:continue
     if not (d.ts.iloc[i-window:i+26].diff().dropna()==3600000).all():continue
     strong=np.sign(d.strength.iloc[i]); reg=regime.iloc[i]
     qualifies=(strong>0 and reg>0) if rule=="up_only" else strong==reg if rule=="match_direction" else strong!=reg if rule=="fade_against_regime" else reg>0
     if not qualifies:continue
     # All rules retain the long-only trade; only the entry filter changes.
     values.append(100*(d.open.iloc[i+25]/d.open.iloc[i+1]-1)-.37)
     available=i+24
   results.append(dict(year=year,window=window,rule=rule,n=len(values),stress_mean=float(np.mean(values)) if values else None))
(ROOT/"research/regime_2025_results.json").write_text(json.dumps(results,indent=2))
d=pd.DataFrame(results)
print(d.pivot(index=["window","rule"],columns="year",values="stress_mean").to_string())
print("counts",d.pivot(index=["window","rule"],columns="year",values="n").to_string())
