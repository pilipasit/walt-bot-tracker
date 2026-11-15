"""Short-term rules for 5- and 15-minute bars.

Each computes its whole signal series in one causal pass (every value uses only bars up to
and including its own), which keeps intraday backtests fast.
"""
from __future__ import annotations

from .data import Candle
from .strategies import Strategy, rsi


def ema_series(values: list[float], n: int) -> list[float]:
    k = 2.0 / (n + 1)
    out, e = [], values[0] if values else 0.0
    for v in values:
        e += k * (v - e)
        out.append(e)
    return out


def atr_series(candles: list[Candle], n: int) -> list[float]:
    trs, prev = [], None
    for c in candles:
        tr = c.h - c.l if prev is None else max(c.h - c.l, abs(c.h - prev), abs(c.l - prev))
        trs.append(tr)
        prev = c.c
    return ema_series(trs, n)


class SeriesStrategy(Strategy):
    """Strategy defined by series(candles) -> one target per bar. Results are cached per list."""

    allow_short = False

    def __init__(self):
        self._key = None
        self._series: list[int] = []

    def series(self, candles: list[Candle]) -> list[int]:
        raise NotImplementedError

    def target(self, candles, i):
        key = (id(candles), len(candles))
        if key != self._key:
            self._series, self._key = self.series(candles), key
        return self._series[i]

    def _side(self, x: float) -> int:
        if x > 0:
            return 1
        return -1 if x < 0 and self.allow_short else 0


class EmaCross(SeriesStrategy):
    """Fast EMA above slow EMA = long, below = short."""

    def __init__(self, fast: int = 9, slow: int = 21, allow_short: bool = False):
        super().__init__()
        self.fast, self.slow, self.allow_short = fast, slow, allow_short
        self.warmup = slow * 3

    def series(self, candles):
        closes = [c.c for c in candles]
        f, s = ema_series(closes, self.fast), ema_series(closes, self.slow)
        return [self._side(f[i] - s[i]) if i >= self.warmup else 0 for i in range(len(closes))]


class Macd(SeriesStrategy):
    """MACD histogram above zero = long, below = short."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9, allow_short: bool = False):
        super().__init__()
        self.fast, self.slow, self.signal, self.allow_short = fast, slow, signal, allow_short
        self.warmup = slow * 3 + signal

    def series(self, candles):
        closes = [c.c for c in candles]
        f, s = ema_series(closes, self.fast), ema_series(closes, self.slow)
        macd = [a - b for a, b in zip(f, s)]
        sig = ema_series(macd, self.signal)
        return [self._side(macd[i] - sig[i]) if i >= self.warmup else 0 for i in range(len(closes))]


class RsiMomentum(SeriesStrategy):
    """Trend use of RSI: long while RSI is above `upper`, short while below 100 - upper."""

    def __init__(self, period: int = 14, upper: float = 60, allow_short: bool = False):
        super().__init__()
        self.period, self.upper, self.allow_short = period, upper, allow_short
        self.warmup = period + 1

    def series(self, candles):
        out = []
        for i in range(len(candles)):
            if i < self.warmup:
                out.append(0)
                continue
            r = rsi(candles, i, self.period)
            out.append(1 if r > self.upper else -1 if self.allow_short and r < 100 - self.upper else 0)
        return out


class Supertrend(SeriesStrategy):
    """ATR trailing band: long while price holds above the band, short while below."""

    def __init__(self, period: int = 10, mult: float = 3.0, allow_short: bool = False):
        super().__init__()
        self.period, self.mult, self.allow_short = period, mult, allow_short
        self.warmup = period * 3

    def series(self, candles):
        atr = atr_series(candles, self.period)
        out, trend, upper, lower = [], 1, None, None
        for i, c in enumerate(candles):
            mid = (c.h + c.l) / 2
            bu, bl = mid + self.mult * atr[i], mid - self.mult * atr[i]
            if upper is None:
                upper, lower = bu, bl
            else:
                prev_close = candles[i - 1].c
                upper = bu if bu < upper or prev_close > upper else upper
                lower = bl if bl > lower or prev_close < lower else lower
                if trend == 1 and c.c < lower:
                    trend = -1
                elif trend == -1 and c.c > upper:
                    trend = 1
            out.append(self._side(trend) if i >= self.warmup else 0)
        return out


class KeltnerBreakout(SeriesStrategy):
    """Enter when the close leaves the EMA +/- mult x ATR channel; exit back at the EMA."""

    def __init__(self, period: int = 20, mult: float = 1.5, allow_short: bool = False):
        super().__init__()
        self.period, self.mult, self.allow_short = period, mult, allow_short
        self.warmup = period * 3

    def series(self, candles):
        mid = ema_series([c.c for c in candles], self.period)
        atr = atr_series(candles, self.period)
        out, state = [], 0
        for i, c in enumerate(candles):
            if i >= self.warmup:
                if state == 1 and c.c < mid[i]:
                    state = 0
                elif state == -1 and c.c > mid[i]:
                    state = 0
                if state == 0:
                    if c.c > mid[i] + self.mult * atr[i]:
                        state = 1
                    elif self.allow_short and c.c < mid[i] - self.mult * atr[i]:
                        state = -1
            out.append(state)
        return out


class VwapReversion(SeriesStrategy):
    """Fade stretches from the rolling volume-weighted price; exit when price returns to it."""

    def __init__(self, period: int = 48, z: float = 2.0, allow_short: bool = False):
        super().__init__()
        self.period, self.z, self.allow_short = period, z, allow_short
        self.warmup = period * 2

    def series(self, candles):
        n = self.period
        pv, vv = [0.0], [0.0]
        for c in candles:
            pv.append(pv[-1] + (c.h + c.l + c.c) / 3 * c.v)
            vv.append(vv[-1] + c.v)
        devs, out, state = [], [], 0
        for i, c in enumerate(candles):
            lo = max(0, i + 1 - n)
            vol = vv[i + 1] - vv[lo]
            vwap = (pv[i + 1] - pv[lo]) / vol if vol > 0 else c.c
            devs.append(c.c - vwap)
            if i >= self.warmup:
                window = devs[i + 1 - n : i + 1]
                sd = (sum(d * d for d in window) / n) ** 0.5
                d = devs[i]
                if state == 1 and d >= 0:
                    state = 0
                elif state == -1 and d <= 0:
                    state = 0
                if state == 0 and sd > 0:
                    if d < -self.z * sd:
                        state = 1
                    elif self.allow_short and d > self.z * sd:
                        state = -1
            out.append(state)
        return out


class StochasticReversal(SeriesStrategy):
    """Buy when %K turns up out of the oversold zone, exit at the midline; mirrored for shorts."""

    def __init__(self, period: int = 14, low: float = 20, allow_short: bool = False):
        super().__init__()
        self.period, self.low, self.allow_short = period, low, allow_short
        self.warmup = period + 2

    def series(self, candles):
        n, ks, out, state = self.period, [], [], 0
        for i, c in enumerate(candles):
            w = candles[max(0, i + 1 - n) : i + 1]
            hi, lo = max(x.h for x in w), min(x.l for x in w)
            k = 100 * (c.c - lo) / (hi - lo) if hi > lo else 50.0
            ks.append(k)
            if i >= self.warmup:
                prev = ks[i - 1]
                if state == 1 and k >= 50:
                    state = 0
                elif state == -1 and k <= 50:
                    state = 0
                if state == 0:
                    if prev < self.low <= k:
                        state = 1
                    elif self.allow_short and prev > 100 - self.low >= k:
                        state = -1
            out.append(state)
        return out


class VolumeSpike(SeriesStrategy):
    """Follow a bar that moves at least `min_move` on `vol_mult` x average volume, for `hold` bars."""

    def __init__(self, period: int = 48, vol_mult: float = 3.0, min_move: float = 0.003, hold: int = 6,
                 allow_short: bool = False):
        super().__init__()
        self.period, self.vol_mult, self.min_move, self.hold = period, vol_mult, min_move, hold
        self.allow_short = allow_short
        self.warmup = period + 1

    def series(self, candles):
        n, out, state, left, vsum = self.period, [], 0, 0, 0.0
        for i, c in enumerate(candles):
            if i >= self.warmup:
                avg = vsum / n
                move = c.c / c.o - 1 if c.o else 0.0
                if left > 0:
                    left -= 1
                    if left == 0:
                        state = 0
                if avg > 0 and c.v > self.vol_mult * avg and abs(move) >= self.min_move:
                    side = 1 if move > 0 else (-1 if self.allow_short else 0)
                    if side:
                        state, left = side, self.hold
            out.append(state)
            vsum += c.v
            if i >= n:
                vsum -= candles[i - n].v
        return out


class StreakReversal(SeriesStrategy):
    """After `n` closes in a row in one direction, take the other side for `hold` bars."""

    def __init__(self, n: int = 4, hold: int = 3, allow_short: bool = False):
        super().__init__()
        self.n, self.hold, self.allow_short = n, hold, allow_short
        self.warmup = n + 1

    def series(self, candles):
        out, state, left, run = [], 0, 0, 0
        for i, c in enumerate(candles):
            if i > 0:
                d = c.c - candles[i - 1].c
                if d > 0:
                    run = run + 1 if run > 0 else 1
                elif d < 0:
                    run = run - 1 if run < 0 else -1
                else:
                    run = 0
            if left > 0:
                left -= 1
                if left == 0:
                    state = 0
            if i >= self.warmup and state == 0:
                if run <= -self.n:
                    state, left = 1, self.hold
                elif run >= self.n and self.allow_short:
                    state, left = -1, self.hold
            out.append(state)
        return out


class SqueezeBreakout(SeriesStrategy):
    """After Bollinger bandwidth sits in its lowest quintile, follow the first close outside the band."""

    def __init__(self, period: int = 20, width: float = 2.0, lookback: int = 100, allow_short: bool = False):
        super().__init__()
        self.period, self.width, self.lookback, self.allow_short = period, width, lookback, allow_short
        self.warmup = period + lookback

    def series(self, candles):
        n, out, state, bws = self.period, [], 0, []
        s1 = s2 = 0.0
        for i, c in enumerate(candles):
            s1 += c.c
            s2 += c.c * c.c
            if i >= n:
                old = candles[i - n].c
                s1 -= old
                s2 -= old * old
            if i + 1 < n:
                bws.append(0.0)
                out.append(0)
                continue
            mean = s1 / n
            sd = max(s2 / n - mean * mean, 0.0) ** 0.5
            bws.append(sd / mean if mean else 0.0)
            if i >= self.warmup:
                if state == 1 and c.c < mean:
                    state = 0
                elif state == -1 and c.c > mean:
                    state = 0
                if state == 0:
                    past = sorted(bws[i - self.lookback : i])
                    squeezed = bws[i - 1] <= past[len(past) // 5]
                    if squeezed and c.c > mean + self.width * sd:
                        state = 1
                    elif squeezed and self.allow_short and c.c < mean - self.width * sd:
                        state = -1
            out.append(state)
        return out


SHORT_TERM = {
    "ema_cross": EmaCross,
    "macd": Macd,
    "rsi_momentum": RsiMomentum,
    "supertrend": Supertrend,
    "keltner_breakout": KeltnerBreakout,
    "vwap_reversion": VwapReversion,
    "stochastic_reversal": StochasticReversal,
    "volume_spike": VolumeSpike,
    "streak_reversal": StreakReversal,
    "squeeze_breakout": SqueezeBreakout,
}
