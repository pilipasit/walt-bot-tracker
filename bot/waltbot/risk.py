"""Position sizing and loss limits. Every limit here is checked before a new entry."""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import Contract

DAY_MS = 86_400_000


@dataclass
class RiskConfig:
    margin_fraction: float = 0.10  # share of account equity committed as margin per trade
    max_margin_quote: float | None = None  # hard cap on margin per trade, in USDC
    leverage: int = 2
    stop_loss_pct: float | None = 0.03  # distance from entry price; None = no stop
    take_profit_pct: float | None = None
    daily_loss_limit_pct: float = 0.05  # flatten and stop for the UTC day after this loss
    max_drawdown_pct: float = 0.25  # flatten and stop for good after this drawdown from peak


class RiskManager:
    def __init__(self, cfg: RiskConfig, contract: Contract, start_equity: float):
        if not 1 <= cfg.leverage <= contract.max_leverage:
            raise ValueError(
                f"leverage {cfg.leverage} outside 1..{contract.max_leverage} for {contract.base_asset}"
            )
        self.cfg, self.contract = cfg, contract
        self.peak = start_equity
        self.day = None
        self.day_start_equity = start_equity
        self.killed = False  # max drawdown hit: no more trading
        self.day_halted = False  # daily loss limit hit: no more trading today

    def update(self, ts: int, equity: float) -> None:
        """Feed mark-to-market equity at each bar close."""
        day = ts // DAY_MS
        if day != self.day:
            self.day, self.day_start_equity, self.day_halted = day, equity, False
        self.peak = max(self.peak, equity)
        if equity <= self.peak * (1 - self.cfg.max_drawdown_pct):
            self.killed = True
        if equity <= self.day_start_equity * (1 - self.cfg.daily_loss_limit_pct):
            self.day_halted = True

    @property
    def halted(self) -> bool:
        return self.killed or self.day_halted

    def margin_for_entry(self, equity: float) -> tuple[float, str]:
        """Margin (USDC) for a new position, or (0, reason) when no trade is allowed."""
        if self.halted:
            return 0.0, "kill switch" if self.killed else "daily loss limit"
        margin = equity * self.cfg.margin_fraction
        if self.cfg.max_margin_quote is not None:
            margin = min(margin, self.cfg.max_margin_quote)
        margin = min(margin, self.contract.max_size_quote)
        if margin < self.contract.min_size_quote:
            return 0.0, f"margin {margin:.2f} below contract minimum {self.contract.min_size_quote}"
        return margin, ""
