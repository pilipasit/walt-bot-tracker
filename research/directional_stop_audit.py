"""Audit directional filters and predeclared volatility stops on event-driven entries."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
results=[]
for year in [2022,2023,2024]:
 frames=[]
 for symbol in ["BTC","ETH","SOL","XRP"]:
  chunks=[pd.read_csv(p,header=None).iloc[:,:6] for p in sorted((ROOT/f"bot/data/research/archive_{year}").glob(f"{symbol}-{year}-*.csv"))]
  d=pd.concat(chunks,ignore_index=True)
  d.columns=["ts","open","high","low","close","volume"]
  d["vol"]=np.log(d.close).diff().rolling(72).std()
  d["strength"]=np.log(d.close).diff(120)/(d.vol*np.sqrt(120))
  frames.append((symbol,d))
 for direction in ["up","down","both"]:
  for multiple in [None,4,8]:
   trades=[]
   for symbol,d in frames:
    available=0
    for i in range(168,len(d)-25):
     strength=d.strength.iloc[i]
     qualifies=strength>=2 if direction=="up" else strength<=-2 if direction=="down" else abs(strength)>=2
     if i<available or not qualifies or d.vol.iloc[i]>.02:continue
     if not (d.ts.iloc[i-168:i+26].diff().dropna()==3600000).all():continue
     entry=d.open.iloc[i+1]
     price=d.open.iloc[i+25]
     duration=24
     stop=entry*(1-multiple*d.vol.iloc[i]) if multiple else 0
     for j in range(i+1,i+25):
      if d.low.iloc[j]<=stop:
       price=min(stop,d.open.iloc[j]);duration=j-i
       break
     trades.append(dict(symbol=symbol,net=100*(price/entry-1)-.34-.03*duration/24))
     available=i+24
   a=pd.DataFrame(trades)
   results.append(dict(year=year,direction=direction,stop_vol_multiple=multiple,n=len(a),
                       stress_mean=float(a.net.mean()),median=float(a.net.median()),
                       without_top5=float(a.net.sort_values().iloc[:-5].mean()),
                       by_asset=a.groupby("symbol").net.agg(["count","mean"]).to_dict("index")))
(ROOT/"research/directional_stop_results.json").write_text(json.dumps(results,indent=2))
print(pd.DataFrame(results).drop(columns=["by_asset"]).to_string(index=False))
