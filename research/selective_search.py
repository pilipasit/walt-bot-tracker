import json
from pathlib import Path
import pandas as pd
import numpy as np
root=Path(__file__).resolve().parents[1]
rows=[]
for path in (root/'bot/data/research/long_hourly').glob('*.csv'):
 d=pd.read_csv(path,index_col=0,parse_dates=True)
 c=d.close
 vol=np.log(c).diff().rolling(72).std()
 drift=np.log(c).diff(120)/(vol*np.sqrt(120))
 for hold in [24,72,120]:
  for i in range(168,len(d)-hold-1,hold):
   e=d.index[i+1]; x=d.index[i+hold+1]
   if (e-d.index[i]).total_seconds()>3600:continue
   rows.append([path.stem,e,x,hold,float(drift.iloc[i]),float(vol.iloc[i]),100*(d.open.iloc[i+hold+1]/d.open.iloc[i+1]-1)])
df=pd.DataFrame(rows,columns=['symbol','entry','exit','hold','strength','vol','gross'])
results=[]
for hold in [24,72,120]:
 for strength in [0,1,2,3]:
  for cap in [.005,.01,.02]:
   for mode in ['long','short','trend','reverse']:
    a=df[(df.hold==hold)&(df.strength.abs()>=strength)&(df.vol<=cap)].copy()
    side=np.ones(len(a)) if mode=='long' else -np.ones(len(a)) if mode=='short' else np.sign(a.strength).to_numpy()*(1 if mode=='trend' else -1)
    a['pnl']=side*a.gross-.24
    a['stress']=a.pnl-.10-.03*(a.exit-a.entry).dt.total_seconds()/86400
    train=a[a.exit<pd.Timestamp('2026-01-01',tz='UTC')]
    test=a[a.entry>=pd.Timestamp('2026-01-01',tz='UTC')]
    if len(train)<100 or len(test)<100:continue
    results.append(dict(hold=hold,strength=strength,vol_cap=cap,mode=mode,train_n=len(train),train_mean=float(train.pnl.mean()),test_n=len(test),test_mean=float(test.pnl.mean()),stress=float(test.stress.mean())))
results.sort(key=lambda r:r['train_mean'],reverse=True)
(root/'research/selective_results.json').write_text(json.dumps(results,indent=2))
print('configurations',len(results))
print(json.dumps(results[:8],indent=2))
