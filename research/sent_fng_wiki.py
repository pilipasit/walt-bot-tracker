import json
import numpy as np, pandas as pd
from sent_lib import *

fng = json.load(open('sent_cache/fng.json'))['data']
F = pd.Series({pd.Timestamp(int(x['timestamp']), unit='s').normalize(): float(x['value']) for x in fng}).sort_index()
F = F.asfreq('D').ffill()


def z(s, w=60):
    return (s - s.rolling(w).mean().shift(1)) / s.rolling(w).std().shift(1)


def fng_sigs():
    fam = {'fng_level': [], 'fng_change': []}
    for lo in [10, 15, 20, 25]:
        for hi in [75, 80, 85]:
            s = pd.Series(0, index=F.index)
            s[F < lo] = 1
            s[F > hi] = -1
            fam['fng_level'].append((f'contrarian lo<{lo} hi>{hi}', s))
            fam['fng_level'].append((f'momentum lo<{lo} hi>{hi}', -s))
    for k in [1, 3, 7]:
        for th in [5, 10, 15, 20]:
            d = F.diff(k)
            s = pd.Series(0, index=F.index)
            s[d > th] = 1
            s[d < -th] = -1
            fam['fng_change'].append((f'rising->long k={k} th={th}', s))
            fam['fng_change'].append((f'rising->short k={k} th={th}', -s))
    return fam


def wiki(name):
    j = json.load(open(f'sent_cache/wiki_{name}.json'))['items']
    return pd.Series({pd.Timestamp(x['timestamp'][:8]): x['views'] for x in j}).sort_index().asfreq('D')


def wiki_sigs(name):
    zz = z(np.log(wiki(name)))
    fam = {'wiki_attention': []}
    for th in [1.5, 2, 3]:
        s = pd.Series(0, index=zz.index)
        s[zz > th] = 1
        fam['wiki_attention'].append((f'spike z>{th} ->long', s))
        fam['wiki_attention'].append((f'spike z>{th} ->short', -s))
        s2 = pd.Series(0, index=zz.index)
        s2[zz < -th] = 1
        fam['wiki_attention'].append((f'dull z<-{th} ->long', s2))
        fam['wiki_attention'].append((f'dull z<-{th} ->short', -s2))
    return fam


if __name__ == '__main__':
    out = {}
    tot = 0
    for sym in ['BTC-USD', 'ETH-USD', 'SOL-USD']:
        r, t = run_grid(sym, fng_sigs())
        out['FNG|' + sym] = r
        tot += t
    for name, sym in [('Bitcoin', 'BTC-USD'), ('Ethereum', 'ETH-USD'), ('Solana_', 'SOL-USD'), ('Gold', 'GC_F'),
                      ('Nvidia', 'NVDA'), ('Tesla,_Inc.', 'TSLA'), ('Federal_Reserve', 'SPY')]:
        r, t = run_grid(sym, wiki_sigs(name), minn=10)
        out[f'WIKI|{name}->{sym}'] = r
        tot += t
    out['_configs_tested'] = tot
    json.dump(out, open('sent_fng_wiki_results.json', 'w'), indent=1)
    print('configs', tot)
    for k, v in out.items():
        if k.startswith('_'):
            continue
        for fam, x in v.items():
            if x:
                print(fmt(k, fam, x))
