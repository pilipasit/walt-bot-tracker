import argparse,json,sys
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
from sklearn.ensemble import RandomForestRegressor, HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.tree import DecisionTreeRegressor, export_text
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

def ts(s): return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()*1000)
H=3600000
p=argparse.ArgumentParser(); p.add_argument('data',type=Path); p.add_argument('--out',type=Path,default=Path('research/ml_results.json')); args=p.parse_args()
rows=[]
for f in sorted(args.data.glob('*.json')):
 d=json.loads(f.read_text()); c=d['candles']; by={x[0]:x for x in c}; close=np.array([x[4] for x in c],float)
 for i in range(168,len(c)-25):
  t=c[i][0]; future=by.get(t+25*H); entry=by.get(t+H)
  if entry is None or future is None or any(c[j+1][0]-c[j][0]!=H for j in range(i-168,i)): continue
  x=close[i-168:i+1]; r=np.diff(np.log(x)); vol=np.std(r[-24:]); slope=np.polyfit(np.arange(24),np.log(x[-24:]),1)[0]
  v=[np.log(x[-1]/x[-1-n]) for n in (1,4,12,24,48,72,168)] + [np.std(r[-k:]) for k in (12,24,72,168)] + [slope/(vol+1e-8), (x[-1]/np.mean(x[-24:])-1), (max(x[-24:])-min(x[-24:]))/x[-1]]
  y=100*(future[1]/entry[1]-1)
  rows.append((t,d['symbol'],d['group'],v,y))
rows.sort(key=lambda x:(x[0],x[1])); X=np.array([r[3] for r in rows]); y=np.array([r[4] for r in rows]); dates=np.array([r[0] for r in rows]); syms=np.array([r[1] for r in rows]); groups=np.array([r[2] for r in rows]);
cut1,cut2=ts('2026-10-01'),ts('2026-11-01'); train=dates+25*H<cut1; valid=(dates>=cut1)&(dates+25*H<cut2); test=dates>=cut2
models={'ridge':make_pipeline(StandardScaler(),Ridge(alpha=100)), 'forest':RandomForestRegressor(n_estimators=80,min_samples_leaf=100,max_features=.8,n_jobs=-1,random_state=7), 'tree':DecisionTreeRegressor(max_depth=2,min_samples_leaf=200,random_state=7), 'boost':HistGradientBoostingRegressor(max_iter=80,max_leaf_nodes=7,min_samples_leaf=100,l2_regularization=1,random_state=7)}
def evaluate(mask,pred,threshold, mode='model'):
 idx=np.where(mask)[0]; last={}; out=[]
 for j in idx:
  v=pred[j]; direction=(1 if v>0 else -1) if mode=='model' else (1 if mode=='long' else -1)
  if mode=='model' and abs(v)<threshold: continue
  if dates[j]<last.get(syms[j],0): continue
  last[syms[j]]=dates[j]+24*H; out.append((direction*y[j]-.14,groups[j],syms[j],dates[j]))
 if not out:return {'n':0,'mean':None}
 z=np.array([a[0] for a in out]); return {'n':len(z),'mean':round(z.mean(),4),'median':round(float(np.median(z)),4),'win_rate':round(float((z>0).mean()),4),'by_group':{g:{'n':sum(a[1]==g for a in out),'mean':round(float(np.mean([a[0] for a in out if a[1]==g])),4)} for g in sorted(set(a[1] for a in out))}}
output={'method':{'target':'24h next-open to next-open return; 0.14 percentage points round-trip','signals':'all eligible continuous hourly windows; at most one position per symbol per 24h','split':'training before Oct 1; validation Oct 1-31; test Nov 1 onward; 25h embargo at boundaries','features':'trailing returns, volatility, normalized slope and price channel; no asset identifiers','limitations':'proxy OHLC, idealized open fills, no spread/slippage/funding; no stop loss or position portfolio constraints; data previously inspected so test not pristine'},'counts':{'train':int(train.sum()),'validation':int(valid.sum()),'test':int(test.sum())},'models':{}}
for name,model in models.items():
 model.fit(X[train],y[train]); pred=model.predict(X); candidates=[]
 for threshold in (0,.05,.1,.2,.3,.5,1):
  z=evaluate(valid,pred,threshold)
  if z['n']>=30:candidates.append((z['mean'],threshold,z))
 if not candidates:continue
 _,thr,v=max(candidates,key=lambda a:a[0]); output['models'][name]={'selected_threshold_pct':thr,'validation':v,'test':evaluate(test,pred,thr),'baseline_test_long':evaluate(test,pred,0,'long'),'baseline_test_short':evaluate(test,pred,0,'short')}
 if name == 'tree': output['models'][name]['rules'] = export_text(model, feature_names=['ret_1h','ret_4h','ret_12h','ret_24h','ret_48h','ret_72h','ret_168h','vol_12h','vol_24h','vol_72h','vol_168h','slope_norm','distance_mean','range_24h'])
output['selected_model']=max(output['models'],key=lambda n:output['models'][n]['validation']['mean']); args.out.parent.mkdir(parents=True,exist_ok=True); args.out.write_text(json.dumps(output,indent=2),encoding='utf-8'); print(json.dumps(output,indent=2))
