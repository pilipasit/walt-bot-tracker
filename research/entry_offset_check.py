"""Check all 24 entry-grid offsets for the frozen large-move long rule."""
import json
from pathlib import Path
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
results={}
for year in [2022,2023]:
 frames=[]
 for symbol in ["BTC","ETH","SOL","XRP"]:
  parts=[pd.read_csv(p,header=None).iloc[:,:6] for p in sorted((ROOT/f"bot/data/research/archive_{year}").glob(f"{symbol}-{year}-*.csv"))]
  frame=pd.concat(parts,ignore_index=True)
  frame.columns=["ts","open","high","low","close","volume"]
  volatility=np.log(frame.close).diff().rolling(72).std()
  strength=np.log(frame.close).diff(120)/(volatility*np.sqrt(120))
  eligible=(strength.abs()>=2)&(volatility<=.02)
  frames.append((frame,eligible))
 event_values=[]
 for frame,eligible in frames:
  available=0
  for i in range(168,len(frame)-25):
   if i<available or not eligible.iloc[i]:continue
   if not (frame.ts.iloc[i-168:i+26].diff().dropna()==3600000).all():continue
   event_values.append(100*(frame.open.iloc[i+25]/frame.open.iloc[i+1]-1)-.24)
   available=i+24
 offsets=[]
 for offset in range(24):
  values=[]
  for frame,eligible in frames:
   for i in range(168+offset,len(frame)-25,24):
    if not eligible.iloc[i]:continue
    if not (frame.ts.iloc[i-168:i+26].diff().dropna()==3600000).all():continue
    values.append(100*(frame.open.iloc[i+25]/frame.open.iloc[i+1]-1)-.24)
  offsets.append(dict(offset=offset,n=len(values),mean=float(np.mean(values)),stress=float(np.mean(values)-.13)))
 results[str(year)]=dict(event_driven=dict(n=len(event_values),mean=float(np.mean(event_values)),stress=float(np.mean(event_values)-.13)),offsets=offsets,positive_offsets=sum(r["mean"]>0 for r in offsets),
                         median_offset_mean=float(np.median([r["mean"] for r in offsets])),
                         worst_offset_mean=min(r["mean"] for r in offsets))
(ROOT/"research/entry_offset_results.json").write_text(json.dumps(results,indent=2))
print(json.dumps({k:{a:b for a,b in v.items() if a!="offsets"} for k,v in results.items()},indent=2))
