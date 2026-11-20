import numpy as np, pandas as pd

D = 'C:/Projects/walt-trading-bot/data/research/daily/'
COST = 0.0014   # round trip fees
FUND = 0.0008   # per calendar day: longs pay, shorts receive
SPLIT = pd.Timestamp('2023-12-31')
TRAIN = (pd.Timestamp('2000-01-01'), SPLIT)
TEST = (pd.Timestamp('2024-01-01'), pd.Timestamp('2030-01-01'))
SIDES = ['both', 'long', 'short']
HOLDS = [1, 3, 7]


class Asset:
    def __init__(s, sym):
        df = pd.read_csv(D + sym + '.csv', index_col=0, parse_dates=True).sort_index()
        df = df[['open', 'close']].dropna()
        s.idx = df.index
        s.o = df.open.values


def trades(a, sig, H, side, period, fund=True):
    """Signal on date t (end of day) -> enter open of next price row, exit H rows later.
    One position at a time. period applies to the signal date."""
    lo, hi = period
    out = []
    free = 0
    n = len(a.o)
    sd = sig[(sig != 0) & (sig.index >= lo) & (sig.index <= hi)]
    pos = a.idx.searchsorted(sd.index, side='right')
    for p, d in zip(pos, sd.values):
        if side == 'long' and d < 0:
            continue
        if side == 'short' and d > 0:
            continue
        if p < free or p + H >= n:
            continue
        days = (a.idx[p + H] - a.idx[p]).days
        r = d * (a.o[p + H] / a.o[p] - 1) - COST
        if fund:
            r += -FUND * days if d > 0 else FUND * days
        out.append((int(d), float(r), int(p)))
        free = p + H
    return out


def stats(tr):
    if not tr:
        return dict(n=0, avg=0.0, win=0.0, tot=0.0, dd=0.0, t=0.0)
    r = np.array([t[1] for t in tr])
    eq = np.cumprod(1 + r)
    pk = np.maximum.accumulate(np.r_[1, eq])[1:]
    return dict(n=len(r), avg=float(r.mean() * 100), win=float((r > 0).mean() * 100),
                tot=float((eq[-1] - 1) * 100), dd=float((eq / pk - 1).min() * 100),
                t=float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 and r.std() > 0 else 0))


def bh(a, period):
    lo, hi = period
    m = (a.idx >= lo) & (a.idx <= hi)
    if m.sum() < 2:
        return 0.0
    o = a.o[m]
    return float((o[-1] / o[0] - 1) * 100)


def rand_med(a, tr, H, period, fund=True, k=200, seed=1):
    if not tr:
        return 0.0
    rng = np.random.default_rng(seed)
    lo, hi = period
    cand = np.where((a.idx > lo) & (a.idx <= hi))[0]
    cand = cand[cand + H < len(a.o)]
    dirs = np.array([t[0] for t in tr])
    res = []
    for _ in range(k):
        chosen = []
        for p in rng.permutation(cand):
            if len(chosen) >= len(tr):
                break
            if all(abs(p - q) >= H for q in chosen):
                chosen.append(p)
        r = []
        for p, d in zip(sorted(chosen), rng.permutation(dirs)):
            days = (a.idx[p + H] - a.idx[p]).days
            x = d * (a.o[p + H] / a.o[p] - 1) - COST
            if fund:
                x += -FUND * days if d > 0 else FUND * days
            r.append(x)
        res.append((np.prod(1 + np.array(r)) - 1) * 100)
    return float(np.median(res))


def run_grid(sym, family_sigs, minn=15):
    """For each family: try all (signal, hold, side) configs, pick best by TRAIN total return
    (funding included, >=minn trades), report frozen on TEST."""
    a = Asset(sym)
    res = {}
    total = 0
    for fam, sigs in family_sigs.items():
        rows = []
        for label, sig in sigs:
            for H in HOLDS:
                for side in SIDES:
                    total += 1
                    trn = trades(a, sig, H, side, TRAIN)
                    tst = trades(a, sig, H, side, TEST)
                    rows.append(dict(label=label, H=H, side=side, sig=sig, trn=trn, tst=tst,
                                     train=stats(trn), test=stats(tst)))
        ok = [r for r in rows if r['train']['n'] >= minn]
        if not ok:
            res[fam] = None
            continue
        b = max(ok, key=lambda r: r['train']['tot'])
        H = b['H']
        t0 = trades(a, b['sig'], H, b['side'], TEST, False)
        tr0 = trades(a, b['sig'], H, b['side'], TRAIN, False)
        big = [r for r in rows if r['test']['n'] >= 5]
        res[fam] = dict(
            config=f"{b['label']} H={H} {b['side']}", train=b['train'], test=b['test'],
            train0=stats(tr0), test0=stats(t0),
            bh_train=bh(a, TRAIN), bh_test=bh(a, TEST),
            rand_test=rand_med(a, b['tst'], H, TEST), rand_test0=rand_med(a, t0, H, TEST, False),
            n_cfg=len(rows),
            share_pos_test=float(np.mean([r['test']['tot'] > 0 for r in big])) if big else 0.0,
            med_test_avg=float(np.median([r['test']['avg'] for r in big])) if big else 0.0)
    return res, total


def fmt(k, fam, x):
    return ('%s | %s | %s | train n%d avg%.2f tot%.0f | TEST n%d avg%.2f t%.1f win%.0f tot%.1f dd%.0f (nofund tot%.1f) | BH%.0f rand%.1f | all-cfg pos %.0f%% medavg %.2f'
            % (k, fam, x['config'], x['train']['n'], x['train']['avg'], x['train']['tot'], x['test']['n'], x['test']['avg'], x['test']['t'],
               x['test']['win'], x['test']['tot'], x['test']['dd'], x['test0']['tot'], x['bh_test'], x['rand_test'],
               100 * x['share_pos_test'], x['med_test_avg']))
