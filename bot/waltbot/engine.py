"""Bar-by-bar simulator of one Walt Perps contract. Used by both backtest and paper mode.

Rules mirrored from Walt's agent guide:
  * one position per contract, market orders only;
  * order `amount` is margin in USDC; notional = amount / (1/leverage + taker_fee);
    the entry fee comes out of the margin;
  * base size = notional / worst-case price (price widened by the contract's slippage),
    floored to the contract's size decimals;
  * hourly funding, positive rate = longs pay shorts.

Assumptions that are NOT from Walt (see README "Model limits"):
  * signals are taken at bar close and filled at the next bar's open, moved against us
    by `fill_slippage`;
  * funding uses one constant rate for the whole run;
  * liquidation happens when equity in the position falls to the closeout margin
    fraction of its notional, and costs the whole remaining margin;
  * if a bar touches both stop-loss and take-profit, the stop is assumed to fill first.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .contracts import Contract
from .data import Candle
from .risk import RiskManager
from .strategies import Strategy

HOUR_MS = 3_600_000


@dataclass
class SimConfig:
    fill_slippage: float = 0.0005  # assumed adverse move on every market fill
    funding_rate_hourly: float | None = None  # None = use the contract snapshot value
    adverse_funding: bool = False  # stress test: charge |rate| against every position, long or short


@dataclass
class Position:
    side: int  # +1 long, -1 short
    size: float  # base units
    entry: float
    margin: float  # margin left after the entry fee
    leverage: int
    opened_ts: int
    stop: float | None
    take: float | None
    funding_paid: float = 0.0

    def unrealized(self, price: float) -> float:
        return self.side * (price - self.entry) * self.size - self.funding_paid


@dataclass
class Event:
    ts: int
    kind: str  # open | close | stop | take | liquidation | skip
    side: int
    price: float
    size: float
    margin: float
    leverage: int
    pnl: float = 0.0  # realized on exits, net of exit fee and funding (entry fee not included)
    fee: float = 0.0
    note: str = ""
    stop: float | None = None
    take: float | None = None


@dataclass
class Result:
    start_equity: float
    equity: float
    events: list[Event] = field(default_factory=list)
    curve: list[tuple[int, float]] = field(default_factory=list)  # (ts, mark-to-market equity)
    position: Position | None = None
    fees: float = 0.0
    funding: float = 0.0


class Simulator:
    def __init__(
        self,
        contract: Contract,
        strategy: Strategy,
        risk: RiskManager,
        cfg: SimConfig,
        start_equity: float,
        interval_ms: int,
        trade_from_ts: int = 0,
    ):
        self.contract, self.strategy, self.risk, self.cfg = contract, strategy, risk, cfg
        self.interval_ms = interval_ms
        self.trade_from_ts = trade_from_ts
        self.funding_rate = (
            contract.funding_rate_hourly if cfg.funding_rate_hourly is None else cfg.funding_rate_hourly
        )
        self.res = Result(start_equity=start_equity, equity=start_equity)
        self.pos: Position | None = None
        self.pending = 0  # target decided at the previous close
        self.blocked = 0  # side stopped out; no re-entry until the signal leaves that side

    # --- order handling -------------------------------------------------
    def _open(self, ts: int, side: int, ref_price: float) -> None:
        margin, reason = self.risk.margin_for_entry(self.res.equity)
        lev = self.risk.cfg.leverage
        if margin <= 0:
            self.res.events.append(Event(ts, "skip", side, ref_price, 0, 0, lev, note=reason))
            return
        fee_pct = self.contract.taker_fee_pct
        notional = margin / (1 / lev + fee_pct)
        worst = ref_price * (1 + side * self.contract.price_slippage)
        scale = 10**self.contract.size_decimals
        size = math.floor(notional / worst * scale) / scale
        if size < self.contract.min_size_base or size <= 0:
            self.res.events.append(
                Event(ts, "skip", side, ref_price, size, margin, lev, note="size below contract minimum")
            )
            return
        fill = ref_price * (1 + side * self.cfg.fill_slippage)
        fee = size * fill * fee_pct
        rc = self.risk.cfg
        stop = fill * (1 - side * rc.stop_loss_pct) if rc.stop_loss_pct else None
        take = fill * (1 + side * rc.take_profit_pct) if rc.take_profit_pct else None
        self.res.equity -= fee
        self.res.fees += fee
        self.pos = Position(side, size, fill, margin - fee, lev, ts, stop, take)
        self.res.events.append(
            Event(ts, "open", side, fill, size, margin, lev, fee=fee, stop=stop, take=take)
        )

    def _close(self, ts: int, price: float, kind: str) -> None:
        p = self.pos
        if kind == "liquidation":
            pnl, fee = -p.margin, 0.0
        else:
            fee = p.size * price * self.contract.taker_fee_pct
            pnl = p.unrealized(price) - fee
            pnl = max(pnl, -p.margin)  # a position cannot lose more than its margin
        self.res.equity += pnl
        self.res.fees += fee
        self.res.funding += p.funding_paid
        self.res.events.append(Event(ts, kind, p.side, price, p.size, p.margin, p.leverage, pnl=pnl, fee=fee))
        self.pos = None

    def liquidation_price(self, p: Position) -> float:
        c = self.contract.closeout_margin_fraction
        if p.side > 0:
            return (p.entry * p.size - p.margin + p.funding_paid) / (p.size * (1 - c))
        return (p.margin - p.funding_paid + p.entry * p.size) / (p.size * (1 + c))

    def _intrabar(self, bar: Candle) -> None:
        """Check stop, liquidation and take-profit against the bar's range."""
        p = self.pos
        slip = self.cfg.fill_slippage
        liq = self.liquidation_price(p)
        adverse = bar.l if p.side > 0 else bar.h
        favorable = bar.h if p.side > 0 else bar.l
        stop_hit = p.stop is not None and (adverse - p.stop) * p.side <= 0
        liq_hit = (adverse - liq) * p.side <= 0
        if stop_hit and (not liq_hit or (p.stop - liq) * p.side > 0):
            # A gap through the stop fills at the open, not at the stop price.
            level = bar.o if (bar.o - p.stop) * p.side <= 0 else p.stop
            fill = level * (1 - p.side * slip)
            if (fill - liq) * p.side <= 0:
                self._close(bar.ts, liq, "liquidation")
            else:
                self._close(bar.ts, fill, "stop")
            self.blocked = p.side
        elif liq_hit:
            self._close(bar.ts, liq, "liquidation")
            self.blocked = p.side
        elif p.take is not None and (favorable - p.take) * p.side >= 0:
            level = bar.o if (bar.o - p.take) * p.side >= 0 else p.take
            self._close(bar.ts, level * (1 - p.side * slip), "take")
            self.blocked = p.side

    # --- main loop ------------------------------------------------------
    def run(self, candles: list[Candle]) -> Result:
        hours = self.interval_ms / HOUR_MS
        for i, bar in enumerate(candles):
            # 1. Act on the target decided at the previous close, at this bar's open.
            have = self.pos.side if self.pos else 0
            if self.pending != have:
                if self.pos:
                    self._close(bar.ts, bar.o * (1 - have * self.cfg.fill_slippage), "close")
                if self.pending != 0 and self.pending != self.blocked:
                    self._open(bar.ts, self.pending, bar.o)
            # 2. Funding, then intrabar exits.
            if self.pos:
                rate = abs(self.funding_rate) if self.cfg.adverse_funding else self.pos.side * self.funding_rate
                self.pos.funding_paid += rate * self.pos.size * bar.o * hours
                self._intrabar(bar)
            # 3. Mark to market at the close, update limits, decide the next target.
            mtm = self.res.equity + (self.pos.unrealized(bar.c) if self.pos else 0.0)
            self.res.curve.append((bar.ts, mtm))
            self.risk.update(bar.ts, mtm)
            target = self.strategy.target(candles, i)
            if target != self.blocked:
                self.blocked = 0
            tradable = i + 1 >= self.strategy.warmup and bar.ts + self.interval_ms >= self.trade_from_ts
            self.pending = target if tradable and not self.risk.halted else 0
        self.res.position = self.pos
        return self.res
