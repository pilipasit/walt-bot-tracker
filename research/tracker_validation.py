import sys,json,datetime,statistics,collections,argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bot'))
from waltbot.backtest import market_signal
H=3600000
CUT=int(datetime.datetime(2026,10,20,tzinfo=datetime.timezone.utc).timestamp()*1000)
FEE=.14

def trade(cs,i,d,stop):
    if i+1>=len(cs) or cs[i+1][0]-cs[i][0]>H: return None
    start=cs[i+1][0]; entry=cs[i+1][1]; deadline=start+72*H
    if cs[-1][0]+H < deadline: return None
    sl=entry*(1-d*stop/100); tp=entry*(1+d*2*stop/100); last=None
    for c in cs[i+1:]:
        if c[0]+H>deadline: break
        last=c
        o,hi,lo=c[1:4]
        if (o<=sl if d==1 else o>=sl): return d*(o/entry-1)*100-FEE
        if (o>=tp if d==1 else o<=tp): return 2*stop-FEE
        if (lo<=sl if d==1 else hi>=sl): return -stop-FEE
        if (hi>=tp if d==1 else lo<=tp): return 2*stop-FEE
    return d*(last[4]/entry-1)*100-FEE if last else None

parser = argparse.ArgumentParser(description='Exploratory chronological trend analysis; not a clean holdout')
parser.add_argument('data_dir', type=Path, help='directory of hourly candle JSON files')
args = parser.parse_args()
paths = sorted(args.data_dir.glob('*.json'))
if not paths:
    parser.error('no candle JSON files found')
rows=[]
for path in paths:
    data=json.loads(path.read_text()); cs=data['candles']; closes=[c[4] for c in cs]; previous=None
    for i in range(167,len(cs)-1):
        s=market_signal(closes,i); tr=s['trend']; old=previous; previous=tr
        if old is None or tr==old or tr not in ('up','down'): continue
        ts=cs[i+1][0]
        phase='train' if ts+72*H<CUT else 'test' if ts>=CUT else None
        if not phase: continue
        stop=2.5*s['atr_pct_h']+s['band_pct']; d=1 if tr=='up' else -1
        p=trade(cs,i,d,stop); q=trade(cs,i,-d,stop)
        if p is None or q is None: continue
        rows.append(dict(symbol=data['symbol'],group=data['group'],phase=phase,aligned=s['aligned'], score=s['score'],pnl=p,random=(p+q)/2))
print('Data:',len(rows),'paired, completed episode transitions; split 2026-10-20 UTC with 72h training embargo')
for phase in ('train','test'):
  for label, pred in [('all',lambda r:True),('aligned',lambda r:r['aligned']),('unaligned',lambda r:not r['aligned']),('score .2-.4',lambda r:.2<=r['score']<.4)]:
    x=[r for r in rows if r['phase']==phase and pred(r)]
    if x:
      mean=statistics.mean(r['pnl'] for r in x); base=statistics.mean(r['random'] for r in x)
      by=collections.defaultdict(list)
      for r in x: by[r['group']].append(r['pnl'])
      print(phase,label,'n',len(x),'mean',round(mean,4),'random',round(base,4),'edge',round(mean-base,4),'groups', {k:(len(v),round(statistics.mean(v),3)) for k,v in by.items()})
x=[r for r in rows if r['phase']=='test' and r['aligned'] and r['group']=='Crypto']
by=collections.defaultdict(list)
for r in x: by[r['symbol']].append(r['pnl'])
print('test aligned crypto by asset', {k:(len(v),round(statistics.mean(v),3)) for k,v in sorted(by.items())})
print('test aligned crypto top 5',sorted((round(r['pnl'],3),r['symbol']) for r in x)[-5:])
