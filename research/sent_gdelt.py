import json, glob, os
import numpy as np, pandas as pd
from sent_lib import *
from sent_fng_wiki import z

MAP = {'bitcoin': 'BTC-USD', 'ethereum': 'ETH-USD', 'solana': 'SOL-USD', 'gold': 'GC_F', 'nvidia': 'NVDA',
       'tesla': 'TSLA', 'fed': 'SPY', 'stocks': 'SPY'}


def load(k, mode):
    files = glob.glob(f'sent_cache/gdeltfull_{k}_{mode}.json') or sorted(glob.glob(f'sent_cache/gdelt_{k}_{mode}_*.json'))
    ser = {}
    for f in files:
        j = json.load(open(f, encoding='utf-8'))
        for s in j['timeline']:
            ser.setdefault(s['series'], {}).update({pd.Timestamp(d['date'][:8]): d['value'] for d in s['data']})
    if not ser:
        return None, None
    ser = {n: pd.Series(v).sort_index() for n, v in ser.items()}
    return ser, len(files)


def tone_series(k):
    ser, nf = load(k, 'timelinetone')
    return None if ser is None else list(ser.values())[0].asfreq('D')


def vol_series(k):
    ser, nf = load(k, 'timelinevolraw')
    if ser is None:
        return None
    cnt = [v for n, v in ser.items() if 'Article Count' in n][0]
    tot = [v for n, v in ser.items() if 'Total' in n]
    s = cnt / tot[0] if tot else cnt
    return s.asfreq('D')


def sigs(k):
    fam = {}
    t = tone_series(k)
    if t is not None:
        zz = z(t.interpolate(limit=3))
        fam['gdelt_tone'] = []
        for th in [1.5, 2, 3]:
            s = pd.Series(0, index=zz.index)
            s[zz > th] = 1
            s[zz < -th] = -1
            fam['gdelt_tone'].append((f'tone shock z>{th}: positive->long', s))
            fam['gdelt_tone'].append((f'tone shock z>{th}: positive->short', -s))
    v = vol_series(k)
    if v is not None:
        zz = z(np.log(v.replace(0, np.nan).interpolate(limit=3)))
        fam['gdelt_volume'] = []
        for th in [1.5, 2, 3]:
            s = pd.Series(0, index=zz.index)
            s[zz > th] = 1
            fam['gdelt_volume'].append((f'volume spike z>{th} ->long', s))
            fam['gdelt_volume'].append((f'volume spike z>{th} ->short', -s))
    return fam


if __name__ == '__main__':
    out = {}
    tot = 0
    for k, sym in MAP.items():
        f = sigs(k)
        if not f:
            print('no data', k)
            continue
        r, t = run_grid(sym, f, minn=10)
        out[f'GDELT|{k}->{sym}'] = r
        tot += t
    out['_configs_tested'] = tot
    json.dump(out, open('sent_gdelt_results.json', 'w'), indent=1)
    print('configs', tot)
    for kk, v in out.items():
        if kk.startswith('_'):
            continue
        for fam, x in v.items():
            if x:
                print(fmt(kk, fam, x))
