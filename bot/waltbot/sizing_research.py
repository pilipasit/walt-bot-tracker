"""Leverage x portfolio size x basket research for the live rule (20/100 SMA cross, 4-hour bars).

Each coin in a basket gets an equal slice of the portfolio as its own account; the portfolio
value is the sum. History: 3 years, judged on the full period and on three time segments
(60 / 20 / 20). Run twice: funding at zero, and funding of 0.004 %/hour charged against every
position (a stress close to what Walt has shown).
"""
from __future__ import annotations

import itertools
import json

from . import strategies
from .contracts import get_contract
from .engine import SimConfig, Simulator
from .research import DIR, load
from .risk import RiskConfig, RiskManager

STEP = 4 * 3_600_000
BASKETS = {"BTC": ["BTC"], "BTC+ETH": ["BTC", "ETH"], "4 coins (live)": ["BTC", "ETH", "SOL", "XRP"],
           "8 coins": ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX"]}
LEVERAGE = [1, 2, 3, 5, 10]
SIZES = [50, 100, 500, 1000]
MARGIN_SHARE = [0.4, 0.8]  # share of each coin's slice put up as margin per trade (live: 20 of 25 = 0.8)


def run_coin(sym, candles, series, equity, lev, share, funding, trade_from=0):
    c = get_contract(sym)
    if lev > c.max_leverage:
        return None
    rc = RiskConfig(margin_fraction=share, leverage=lev, stop_loss_pct=0.03, take_profit_pct=None,
                    daily_loss_limit_pct=0.99, max_drawdown_pct=0.5)
    sim = Simulator(c, Precomputed(series), RiskManager(rc, c, equity),
                    SimConfig(fill_slippage=0.0005, funding_rate_hourly=funding, adverse_funding=funding > 0),
                    equity, STEP, trade_from)
    res = sim.run(candles)
    return {ts: v for ts, v in res.curve}, res


class Precomputed(strategies.Strategy):
    def __init__(self, s):
        self.s = s

    def target(self, candles, i):
        return self.s[i]


def portfolio(curves: list[dict], start_eq: float, t0: int, t1: int) -> dict:
    times = sorted({t for c in curves for t in c if t0 <= t < t1})
    last = [None] * len(curves)
    vals = []
    for t in times:
        tot = 0.0
        for i, c in enumerate(curves):
            if t in c:
                last[i] = c[t]
            tot += last[i] if last[i] is not None else start_eq / len(curves)
        vals.append(tot)
    if not vals:
        return {"ret": 0.0, "dd": 0.0}
    base = vals[0] if t0 > 0 else start_eq
    peak, dd = base, 0.0
    for v in vals:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    return {"ret": round((vals[-1] / base - 1) * 100, 1), "dd": round(dd * 100, 1)}


def run() -> list[dict]:
    syms = sorted({s for b in BASKETS.values() for s in b})
    data = {}
    for s in syms:
        cs = load(s, "4h", 1095)
        st = strategies.SmaCross(20, 100, allow_short=True)
        data[s] = (cs, [st.target(cs, i) for i in range(len(cs))])
    t_all = sorted({c.ts for s in syms for c in data[s][0]})
    cut_a, cut_b = t_all[int(len(t_all) * .6)], t_all[int(len(t_all) * .8)]
    rows = []
    for (bname, basket), lev, size, share, funding in itertools.product(BASKETS.items(), LEVERAGE, SIZES, MARGIN_SHARE, (0.0, 0.00004)):
        slice_eq = size / len(basket)
        curves, liq, trades, skipped, fees, ok = [], 0, 0, 0, 0.0, True
        for s in basket:
            cs, ser = data[s]
            r = run_coin(s, cs, ser, slice_eq, lev, share, funding)
            if r is None:
                ok = False
                break
            curve, res = r
            curves.append(curve)
            liq += sum(e.kind == "liquidation" for e in res.events)
            trades += sum(e.kind == "open" for e in res.events)
            skipped += sum(e.kind == "skip" for e in res.events)
            fees += res.fees
        if not ok:
            continue
        full = portfolio(curves, size, 0, 1 << 62)
        segs = [portfolio(curves, size, 0, cut_a), portfolio(curves, size, cut_a, cut_b), portfolio(curves, size, cut_b, 1 << 62)]
        rows.append({"basket": bname, "leverage": lev, "size": size, "margin_share": share,
                     "funding": "stress" if funding else "zero", "ret": full["ret"], "dd": full["dd"],
                     "seg1": segs[0]["ret"], "seg2": segs[1]["ret"], "seg3": segs[2]["ret"],
                     "liquidations": liq, "trades": trades, "skipped": skipped, "fees": round(fees, 2)})
    (DIR / "sizing.json").write_text(json.dumps(rows), encoding="utf-8")
    return rows
