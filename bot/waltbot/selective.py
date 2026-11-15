"""Selective rules and filters: fewer, larger trades, using more inputs than price alone.

Extra inputs: volume, taker-buy volume (order flow), time of day, a reference asset (BTC),
and regime filters (trend direction, trending-vs-ranging).

A round trip on Walt costs roughly 0.28 % of notional (two taker fees plus assumed slippage),
so a rule only survives if its average trade moves well beyond that. These rules therefore
wait for unusual conditions instead of trading every wiggle.
"""
from __future__ import annotations

from .data import Candle
from .short_term import SeriesStrategy, ema_series
from .strategies import Strategy, rsi

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


class ExtremeReversion(SeriesStrategy):
    """Fade a close more than `z` standard deviations from its rolling mean; exit at the mean."""

    def __init__(self, period: int = 96, z: float = 3.0, max_hold: int = 32, allow_short: bool = False):
        super().__init__()
        self.period, self.z, self.max_hold, self.allow_short = period, z, max_hold, allow_short
        self.warmup = period

    def series(self, candles):
        n, out, state, held = self.period, [], 0, 0
        s1 = s2 = 0.0
        for i, c in enumerate(candles):
            s1 += c.c
            s2 += c.c * c.c
            if i >= n:
                old = candles[i - n].c
                s1 -= old
                s2 -= old * old
            if i + 1 < n:
                out.append(0)
                continue
            mean = s1 / n
            sd = max(s2 / n - mean * mean, 0.0) ** 0.5
            zc = (c.c - mean) / sd if sd > 0 else 0.0
            if state != 0:
                held += 1
                if (state == 1 and zc >= 0) or (state == -1 and zc <= 0) or held >= self.max_hold:
                    state = 0
            elif zc < -self.z:
                state, held = 1, 0
            elif self.allow_short and zc > self.z:
                state, held = -1, 0
            out.append(state)
        return out


class CapitulationReversal(SeriesStrategy):
    """After a move of `min_move` within `bars` bars on heavy volume, take the other side for `hold` bars."""

    def __init__(self, bars: int = 4, min_move: float = 0.03, vol_mult: float = 2.0, vol_period: int = 96,
                 hold: int = 8, allow_short: bool = False):
        super().__init__()
        self.bars, self.min_move, self.vol_mult, self.vol_period, self.hold = bars, min_move, vol_mult, vol_period, hold
        self.allow_short = allow_short
        self.warmup = vol_period + bars

    def series(self, candles):
        cum = [0.0]
        for c in candles:
            cum.append(cum[-1] + c.v)
        out, state, left = [], 0, 0
        for i, c in enumerate(candles):
            if left > 0:
                left -= 1
                if left == 0:
                    state = 0
            if i >= self.warmup and state == 0:
                move = c.c / candles[i - self.bars].c - 1
                recent = (cum[i + 1] - cum[i + 1 - self.bars]) / self.bars
                usual = (cum[i + 1 - self.bars] - cum[i + 1 - self.bars - self.vol_period]) / self.vol_period
                if usual > 0 and recent >= self.vol_mult * usual:
                    if move <= -self.min_move:
                        state, left = 1, self.hold
                    elif move >= self.min_move and self.allow_short:
                        state, left = -1, self.hold
            out.append(state)
        return out


class FlowImbalance(SeriesStrategy):
    """Follow sustained aggressive buying or selling (taker-buy share of volume) while price agrees."""

    def __init__(self, period: int = 8, thresh: float = 0.2, hold: int = 4, allow_short: bool = False):
        super().__init__()
        self.period, self.thresh, self.hold, self.allow_short = period, thresh, hold, allow_short
        self.warmup = period + 1

    def series(self, candles):
        n = self.period
        cv, ct = [0.0], [0.0]
        for c in candles:
            cv.append(cv[-1] + c.v)
            ct.append(ct[-1] + c.tb)
        out, state, left = [], 0, 0
        for i, c in enumerate(candles):
            if left > 0:
                left -= 1
                if left == 0:
                    state = 0
            if i >= self.warmup and state == 0:
                vol = cv[i + 1] - cv[i + 1 - n]
                buys = ct[i + 1] - ct[i + 1 - n]
                imb = (2 * buys - vol) / vol if vol > 0 and buys > 0 else 0.0
                drift = c.c - candles[i - n].c
                if imb > self.thresh and drift > 0:
                    state, left = 1, self.hold
                elif imb < -self.thresh and drift < 0 and self.allow_short:
                    state, left = -1, self.hold
            out.append(state)
        return out


class BtcLead(SeriesStrategy):
    """When BTC makes a sharp bar that this asset has not matched, follow BTC for `hold` bars."""

    needs_reference = True

    def __init__(self, thresh: float = 0.006, hold: int = 4, allow_short: bool = False):
        super().__init__()
        self.thresh, self.hold, self.allow_short = thresh, hold, allow_short
        self.warmup = 2
        self.reference: dict[int, Candle] = {}  # BTC candles by open time; empty = never trades

    def series(self, candles):
        out, state, left = [], 0, 0
        for c in candles:
            if left > 0:
                left -= 1
                if left == 0:
                    state = 0
            ref = self.reference.get(c.ts)
            if ref is not None and state == 0 and ref.o and c.o:
                lead, own = ref.c / ref.o - 1, c.c / c.o - 1
                if lead >= self.thresh and own < lead * 0.5:
                    state, left = 1, self.hold
                elif lead <= -self.thresh and own > lead * 0.5 and self.allow_short:
                    state, left = -1, self.hold
            out.append(state)
        return out


class SessionBreakout(SeriesStrategy):
    """Trade the first close outside the 00:00-08:00 UTC range; flat by `exit_hour`. One trade a day."""

    def __init__(self, range_end: int = 8, exit_hour: int = 20, allow_short: bool = False):
        super().__init__()
        self.range_end, self.exit_hour, self.allow_short = range_end, exit_hour, allow_short
        self.warmup = 1

    def series(self, candles):
        out, state, day, hi, lo, traded = [], 0, None, None, None, False
        for c in candles:
            d, hour = c.ts // DAY_MS, (c.ts % DAY_MS) // HOUR_MS
            if d != day:
                day, hi, lo, traded, state = d, None, None, False, 0
            if hour < self.range_end:
                hi = c.h if hi is None else max(hi, c.h)
                lo = c.l if lo is None else min(lo, c.l)
            elif hour >= self.exit_hour:
                state = 0
            elif state == 0 and not traded and hi is not None:
                if c.c > hi:
                    state, traded = 1, True
                elif c.c < lo and self.allow_short:
                    state, traded = -1, True
            out.append(state)
        return out


class TrendPullback(SeriesStrategy):
    """Buy RSI dips while price is above a rising long EMA; sell RSI pops below a falling one."""

    def __init__(self, trend: int = 200, period: int = 14, low: float = 35, exit_level: float = 55,
                 max_hold: int = 48, allow_short: bool = False):
        super().__init__()
        self.trend, self.period, self.low, self.exit_level, self.max_hold = trend, period, low, exit_level, max_hold
        self.allow_short = allow_short
        self.warmup = trend * 2

    def series(self, candles):
        ema = ema_series([c.c for c in candles], self.trend)
        out, state, held = [], 0, 0
        for i, c in enumerate(candles):
            if i < self.warmup:
                out.append(0)
                continue
            r = rsi(candles, i, self.period)
            if state != 0:
                held += 1
                if (state == 1 and r >= self.exit_level) or (state == -1 and r <= 100 - self.exit_level) \
                        or held >= self.max_hold:
                    state = 0
            else:
                rising = ema[i] > ema[i - 10]
                if c.c > ema[i] and rising and r < self.low:
                    state, held = 1, 0
                elif self.allow_short and c.c < ema[i] and not rising and r > 100 - self.low:
                    state, held = -1, 0
            out.append(state)
        return out


class Filtered(SeriesStrategy):
    """Wrap a rule with a regime filter; the rule is flat whenever the filter says no.

    mode "trend": longs only above the `period` EMA, shorts only below it.
    mode "range": trade only while Kaufman's efficiency ratio over `period` bars is below
                  `max_er`, i.e. while price is going sideways rather than trending.
    """

    def __init__(self, base: Strategy, mode: str, period: int, max_er: float = 0.25):
        super().__init__()
        if mode not in ("trend", "range"):
            raise ValueError("mode must be 'trend' or 'range'")
        self.base, self.mode, self.period, self.max_er = base, mode, period, max_er
        self.warmup = max(base.warmup, period + 1)

    def series(self, candles):
        raw = [self.base.target(candles, i) for i in range(len(candles))]
        closes = [c.c for c in candles]
        out = []
        if self.mode == "trend":
            ema = ema_series(closes, self.period)
            for i, t in enumerate(raw):
                ok = i >= self.warmup and ((t > 0 and closes[i] > ema[i]) or (t < 0 and closes[i] < ema[i]))
                out.append(t if ok else 0)
            return out
        path = [0.0]
        for i in range(1, len(closes)):
            path.append(path[-1] + abs(closes[i] - closes[i - 1]))
        n = self.period
        for i, t in enumerate(raw):
            if i < self.warmup or t == 0:
                out.append(0)
                continue
            travelled = path[i] - path[i - n]
            er = abs(closes[i] - closes[i - n]) / travelled if travelled > 0 else 1.0
            out.append(t if er < self.max_er else 0)
        return out


class CrossSectional(SeriesStrategy):
    """Rank a basket of coins by their return over `lookback` bars and trade the extremes.

    mode "momentum": long the `k` strongest, short the `k` weakest.
    mode "reversal": the opposite.
    Positions are re-ranked every `hold` bars. With equal accounts per coin the basket is
    roughly market-neutral, so it does not depend on the market's overall direction.
    """

    needs_universe = True

    def __init__(self, lookback: int = 24, hold: int = 24, mode: str = "momentum", k: int = 2,
                 allow_short: bool = True):
        super().__init__()
        if mode not in ("momentum", "reversal"):
            raise ValueError("mode must be 'momentum' or 'reversal'")
        self.lookback, self.hold, self.mode, self.k, self.allow_short = lookback, hold, mode, k, allow_short
        self.warmup = lookback + 1
        self.symbol = ""
        self.universe: dict[str, dict[int, float]] = {}  # symbol -> {bar open time: close}

    def series(self, candles):
        if len(candles) < 2 or self.symbol not in self.universe:
            return [0] * len(candles)
        step = candles[1].ts - candles[0].ts
        out, state = [], 0
        for i, c in enumerate(candles):
            if i >= self.warmup and (c.ts // step) % self.hold == 0:
                past = c.ts - self.lookback * step
                rets = {}
                for sym, closes in self.universe.items():
                    now, then = closes.get(c.ts), closes.get(past)
                    if now and then:
                        rets[sym] = now / then - 1
                state = 0
                if self.symbol in rets and len(rets) >= 2 * self.k + 1:
                    ranked = sorted(rets, key=rets.get)
                    weak, strong = ranked[: self.k], ranked[-self.k :]
                    sign = 1 if self.mode == "momentum" else -1
                    if self.symbol in strong:
                        state = sign
                    elif self.symbol in weak:
                        state = -sign
                    if state < 0 and not self.allow_short:
                        state = 0
            out.append(state)
        return out
