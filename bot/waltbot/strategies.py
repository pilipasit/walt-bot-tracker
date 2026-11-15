"""Strategy interface and six textbook examples.

The examples exist to exercise the engine. They are not recommendations; which strategy
and parameters to trade is the owner's decision (see QUESTIONS.md).

A strategy sees candles[0..i] (all closed) and returns the position it wants to hold from
the next bar's open: +1 long, -1 short, 0 flat.
"""
from __future__ import annotations

from .data import Candle


class Strategy:
    warmup = 0  # bars needed before the first signal

    def target(self, candles: list[Candle], i: int) -> int:
        raise NotImplementedError


class SmaCross(Strategy):
    """Long while the fast average is above the slow one; short (optional) while below."""

    def __init__(self, fast: int = 20, slow: int = 100, allow_short: bool = False):
        if fast >= slow:
            raise ValueError("fast must be smaller than slow")
        self.fast, self.slow, self.allow_short = fast, slow, allow_short
        self.warmup = slow

    def target(self, candles, i):
        if i + 1 < self.slow:
            return 0
        closes = [c.c for c in candles[i + 1 - self.slow : i + 1]]
        slow = sum(closes) / self.slow
        fast = sum(closes[-self.fast :]) / self.fast
        if fast > slow:
            return 1
        return -1 if self.allow_short and fast < slow else 0


class Stateful(Strategy):
    """Base for strategies that hold a position until an exit rule fires.

    Subclasses implement step(candles, i, state) -> new state. The state resets when the
    strategy is replayed from an earlier bar.
    """

    def __init__(self):
        self._state = 0
        self._last_i = -1

    def target(self, candles, i):
        if i <= self._last_i:
            self._state = 0
        self._last_i = i
        if i + 1 >= self.warmup:
            self._state = self.step(candles, i, self._state)
        return self._state

    def step(self, candles: list[Candle], i: int, state: int) -> int:
        raise NotImplementedError


class DonchianBreakout(Stateful):
    """Enter on a close beyond the prior `entry` bars' range; leave on the `exit` bars' range."""

    def __init__(self, entry: int = 48, exit: int = 24, allow_short: bool = False):
        super().__init__()
        self.entry, self.exit, self.allow_short = entry, exit, allow_short
        self.warmup = entry + 1

    def step(self, candles, i, state):
        close = candles[i].c
        prior_entry = candles[i - self.entry : i]
        prior_exit = candles[i - self.exit : i]
        if state == 1 and close < min(c.l for c in prior_exit):
            state = 0
        elif state == -1 and close > max(c.h for c in prior_exit):
            state = 0
        if state == 0:
            if close > max(c.h for c in prior_entry):
                state = 1
            elif self.allow_short and close < min(c.l for c in prior_entry):
                state = -1
        return state


class Momentum(Strategy):
    """Long while the return over `lookback` bars is above `threshold`; short below -threshold."""

    def __init__(self, lookback: int = 168, threshold: float = 0.02, allow_short: bool = False):
        self.lookback, self.threshold, self.allow_short = lookback, threshold, allow_short
        self.warmup = lookback + 1

    def target(self, candles, i):
        if i < self.lookback:
            return 0
        ret = candles[i].c / candles[i - self.lookback].c - 1
        if ret > self.threshold:
            return 1
        return -1 if self.allow_short and ret < -self.threshold else 0


def rsi(candles: list[Candle], i: int, period: int) -> float:
    """RSI from simple averages of the last `period` close-to-close changes (Cutler's RSI)."""
    gain = loss = 0.0
    for k in range(i - period + 1, i + 1):
        d = candles[k].c - candles[k - 1].c
        if d > 0:
            gain += d
        else:
            loss -= d
    if gain + loss == 0:
        return 50.0
    return 100.0 * gain / (gain + loss)


class RsiReversion(Stateful):
    """Mean reversion: buy when RSI is oversold, leave when it recovers to `exit_level`.

    With allow_short, also sells when RSI is overbought (100 - oversold) and covers at
    100 - exit_level.
    """

    def __init__(self, period: int = 14, oversold: float = 30, exit_level: float = 50, allow_short: bool = False):
        super().__init__()
        self.period, self.oversold, self.exit_level, self.allow_short = period, oversold, exit_level, allow_short
        self.warmup = period + 1

    def step(self, candles, i, state):
        r = rsi(candles, i, self.period)
        if state == 1 and r >= self.exit_level:
            state = 0
        elif state == -1 and r <= 100 - self.exit_level:
            state = 0
        if state == 0:
            if r < self.oversold:
                state = 1
            elif self.allow_short and r > 100 - self.oversold:
                state = -1
        return state


class BollingerReversion(Stateful):
    """Mean reversion: buy a close below the lower band, leave at the moving average."""

    def __init__(self, period: int = 20, width: float = 2.0, allow_short: bool = False):
        super().__init__()
        self.period, self.width, self.allow_short = period, width, allow_short
        self.warmup = period

    def step(self, candles, i, state):
        closes = [c.c for c in candles[i + 1 - self.period : i + 1]]
        mean = sum(closes) / self.period
        sd = (sum((x - mean) ** 2 for x in closes) / self.period) ** 0.5
        close = closes[-1]
        if state == 1 and close >= mean:
            state = 0
        elif state == -1 and close <= mean:
            state = 0
        if state == 0 and sd > 0:
            if close < mean - self.width * sd:
                state = 1
            elif self.allow_short and close > mean + self.width * sd:
                state = -1
        return state


class TrendFilteredRsi(Stateful):
    """RSI dip-buying, taken only while price is above its long moving average."""

    def __init__(self, period: int = 14, oversold: float = 35, exit_level: float = 55, trend: int = 200):
        super().__init__()
        self.period, self.oversold, self.exit_level, self.trend = period, oversold, exit_level, trend
        self.warmup = max(trend, period + 1)

    def step(self, candles, i, state):
        r = rsi(candles, i, self.period)
        if state == 1 and r >= self.exit_level:
            return 0
        if state == 0 and r < self.oversold:
            avg = sum(c.c for c in candles[i + 1 - self.trend : i + 1]) / self.trend
            if candles[i].c > avg:
                return 1
        return state


REGISTRY = {
    "sma_cross": SmaCross,
    "donchian": DonchianBreakout,
    "momentum": Momentum,
    "rsi_reversion": RsiReversion,
    "bollinger_reversion": BollingerReversion,
    "trend_rsi": TrendFilteredRsi,
}


from .short_term import SHORT_TERM  # noqa: E402  (needs Strategy and rsi defined above)

REGISTRY.update(SHORT_TERM)


def build(name: str, params: dict | None = None) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy '{name}'. Available: {sorted(REGISTRY)}")
    return REGISTRY[name](**(params or {}))
