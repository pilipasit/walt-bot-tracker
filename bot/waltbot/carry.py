"""Funding-carry research: get paid funding instead of predicting price.

Idea: on a perp venue, the side that is crowded pays the other side every funding period.
A market-neutral basket that is long the coins with the lowest funding (longs get paid or pay
least) and short the coins with the highest funding (shorts get paid) collects the funding
spread, and its price risk is only the difference between the two baskets.

Walt has no funding history, so this uses Binance futures funding and Binance prices for two
years as a proxy. That answers one question honestly - does the price side of such a basket
lose what the funding side earns? - but NOT how large Walt's own funding is over time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .llm_experiment import UNIVERSE
from .mlresearch import futures_series
from .research import load

DAYS, DAY = 730, 86_400_000
COST_PER_SIDE = 0.0014  # taker fee 0.09 % + assumed slippage 0.05 %, per unit traded


def panel() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily close and daily summed funding rate for every coin with data."""
    closes, funding = {}, {}
    for s in UNIVERSE:
        try:
            cs = load(s, "1d", DAYS)
            f = futures_series(s, "funding", cs[0].ts, cs[-1].ts + DAY)
        except Exception:
            continue
        if len(cs) < DAYS * 0.9 or len(f) < DAYS * 2:
            continue
        closes[s] = pd.Series({c.ts: c.c for c in cs})
        f.index = (f.index.astype("int64") // DAY) * DAY
        funding[s] = f.groupby(level=0).sum()
    return pd.DataFrame(closes).dropna(), pd.DataFrame(funding)


def backtest(close: pd.DataFrame, fund: pd.DataFrame, k: int, lookback: int, funding_scale: float = 1.0) -> dict:
    """Each day: long the k lowest trailing-funding coins, short the k highest, equal size.

    Returns are per unit of gross exposure (long notional + short notional = 1).
    """
    fund = fund.reindex(close.index).fillna(0.0)
    ret = close.pct_change().shift(-1)  # next day's return, earned by today's positions
    next_fund = fund.shift(-1)  # funding paid during the day the position is held
    signal = fund.rolling(lookback).mean()
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for t in close.index[lookback:]:
        rank = signal.loc[t].sort_values()
        weights.loc[t, rank.index[:k]] = 0.5 / k
        weights.loc[t, rank.index[-k:]] = -0.5 / k
    price = (weights * ret).sum(axis=1)
    carry = -(weights * next_fund).sum(axis=1) * funding_scale  # longs pay positive funding, shorts receive it
    cost = weights.diff().abs().sum(axis=1).fillna(0.0) * COST_PER_SIDE
    total = (price + carry - cost).iloc[lookback:-1]
    curve = (1 + total).cumprod()
    half = len(total) // 2
    ann = lambda x: float(x.mean() * 365 * 100)  # noqa: E731
    return {
        "k": k, "lookback": lookback, "days": len(total),
        "price_pct_yr": ann(price.iloc[lookback:-1]), "funding_pct_yr": ann(carry.iloc[lookback:-1]),
        "cost_pct_yr": -ann(cost.iloc[lookback:-1]), "total_pct_yr": ann(total),
        "first_half_pct_yr": ann(total.iloc[:half]), "second_half_pct_yr": ann(total.iloc[half:]),
        "t_stat": float(total.mean() / total.std() * np.sqrt(len(total))),
        "max_drawdown_pct": float((1 - curve / curve.cummax()).max() * 100),
        "turnover_per_day": float(weights.diff().abs().sum(axis=1).iloc[lookback:-1].mean()),
    }


def run() -> list[dict]:
    close, fund = panel()
    print(f"{close.shape[1]} coins, {close.shape[0]} days")
    spread = fund.reindex(close.index).apply(lambda r: r.nlargest(4).mean() - r.nsmallest(4).mean(), axis=1)
    print(f"Binance: mean daily funding spread between the 4 highest and 4 lowest coins: {spread.mean() * 100:.4f} %/day")
    rows = [backtest(close, fund, k, lb) for k in (2, 4, 6) for lb in (1, 3, 7)]
    cols = ["k", "lookback", "price_pct_yr", "funding_pct_yr", "cost_pct_yr", "total_pct_yr", "first_half_pct_yr",
            "second_half_pct_yr", "t_stat", "max_drawdown_pct", "turnover_per_day"]
    print("Per unit of gross exposure, Binance funding as observed:")
    print("".join(c.rjust(20) for c in cols))
    for r in rows:
        print("".join(f"{r[c]:>20.2f}" for c in cols))
    return rows
