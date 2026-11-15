"""Build a simulator from a config dict, run it, and summarize the result."""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

from . import strategies
from .contracts import Contract, get_contract
from .data import INTERVAL_MS, Candle, data_symbol, get_candles
from .engine import Result, SimConfig, Simulator
from .risk import RiskConfig, RiskManager

EXITS = ("close", "stop", "take", "liquidation")


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_simulator(cfg: dict, contract: Contract, trade_from_ts: int = 0) -> Simulator:
    strat = strategies.build(cfg["strategy"]["name"], cfg["strategy"].get("params"))
    risk_cfg = RiskConfig(**cfg.get("risk", {}))
    equity = float(cfg["start_equity"])
    return Simulator(
        contract,
        strat,
        RiskManager(risk_cfg, contract, equity),
        SimConfig(**cfg.get("sim", {})),
        equity,
        INTERVAL_MS[cfg["interval"]],
        trade_from_ts,
    )


def load_candles(cfg: dict, contract: Contract, days: float | None = None) -> list[Candle]:
    symbol = data_symbol(contract.base_asset, cfg.get("data_symbol"))
    return get_candles(symbol, cfg["interval"], days or cfg["days"])


def summarize(res: Result, candles: list[Candle]) -> dict:
    exits = [e for e in res.events if e.kind in EXITS]
    opens = [e for e in res.events if e.kind == "open"]
    # Net result per round trip = exit pnl minus the entry fee of the matching open.
    nets = [x.pnl - o.fee for o, x in zip(opens, exits)]
    peak, max_dd = res.start_equity, 0.0
    for _, eq in res.curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, 1 - eq / peak)
    final = res.curve[-1][1] if res.curve else res.equity
    return {
        "bars": len(candles),
        "from": _day(candles[0].ts) if candles else None,
        "to": _day(candles[-1].ts) if candles else None,
        "start_equity": res.start_equity,
        "final_equity": round(final, 2),
        "return_pct": round((final / res.start_equity - 1) * 100, 2),
        "buy_hold_pct": round((candles[-1].c / candles[0].o - 1) * 100, 2) if candles else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "round_trips": len(exits),
        "win_rate_pct": round(100 * sum(n > 0 for n in nets) / len(nets), 1) if nets else None,
        "stops": sum(e.kind == "stop" for e in exits),
        "liquidations": sum(e.kind == "liquidation" for e in exits),
        "skipped_entries": sum(e.kind == "skip" for e in res.events),
        "fees_paid": round(res.fees, 2),
        "funding_paid": round(res.funding, 2),
        "open_position": bool(res.position),
    }


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def run_backtest(cfg: dict) -> tuple[dict, Result]:
    contract = get_contract(cfg["symbol"])
    candles = load_candles(cfg, contract)
    if not candles:
        raise RuntimeError("No candles returned; check the symbol and data_symbol in the config")
    res = make_simulator(cfg, contract).run(candles)
    return summarize(res, candles), res


def compare(cfg: dict, symbols: list[str], split: float = 0.7, allow_short: bool = False) -> list[dict]:
    """Run every registered strategy with its default parameters on each symbol.

    Each run is reported for the whole period and separately for the first `split` share
    (in-sample) and the rest (out-of-sample). A strategy that only looks good in one of the
    two halves is probably fitted to noise.
    """
    rows = []
    for symbol in symbols:
        contract = get_contract(symbol)
        candles = load_candles({**cfg, "data_symbol": None}, contract)
        if not candles:
            continue
        cut = int(len(candles) * split)
        for name in strategies.REGISTRY:
            params = {}
            if allow_short and "allow_short" in inspect.signature(strategies.REGISTRY[name]).parameters:
                params["allow_short"] = True
            c = {**cfg, "symbol": symbol, "strategy": {"name": name, "params": params}}
            full = summarize(make_simulator(c, contract).run(candles), candles)
            early = summarize(make_simulator(c, contract).run(candles[:cut]), candles[:cut])
            late_res = make_simulator(c, contract, trade_from_ts=candles[cut].ts).run(candles)
            late = summarize(late_res, candles[cut:])
            rows.append({
                "symbol": symbol,
                "strategy": name,
                "return_pct": full["return_pct"],
                "in_sample_pct": early["return_pct"],
                "out_sample_pct": late["return_pct"],
                "max_dd_pct": full["max_drawdown_pct"],
                "trips": full["round_trips"],
                "win_pct": full["win_rate_pct"],
                "fees": full["fees_paid"],
                "funding": full["funding_paid"],
                "liq": full["liquidations"],
                "buy_hold_pct": full["buy_hold_pct"],
            })
    return rows
