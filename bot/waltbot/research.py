"""Grid search over trading rules with a train / validation / test split.

Every (rule, timeframe, exit profile) configuration is simulated on each contract as an
independent 100 USDC account. History is cut 60 / 20 / 20 in time order. Configurations are
selected on train and validation only; the test segment is looked at once, for the selected
configurations, and is the only number that says anything about the future.

Grids:
  short      the 16 registry rules on 5- and 15-minute bars, 4 contracts, 60-120 days
  selective  25 selective / filtered rules on 15-minute and 1-hour bars, 8 contracts, 1-2 years
  swing      registry rules, variants and cross-coin ranking on 4-hour and daily bars, 3-5 years
  xsection   cross-coin momentum / reversal ranking on 1-hour bars, 2 years
"""
from __future__ import annotations

import csv
import dataclasses
import inspect
import json
import math
import time
from multiprocessing import Pool

from . import selective as sel
from . import strategies
from .contracts import ROOT, get_contract
from .data import INTERVAL_MS, Candle, _download, data_symbol
from .engine import SimConfig, Simulator
from .risk import RiskConfig, RiskManager

DIR = ROOT / "data" / "research"
PROFILES = {  # stop-loss / take-profit distance from entry
    "tight": {"stop_loss_pct": 0.006, "take_profit_pct": 0.012},
    "medium": {"stop_loss_pct": 0.012, "take_profit_pct": 0.024},
    "wide": {"stop_loss_pct": 0.03, "take_profit_pct": None},
}


def _registry_rules() -> dict:
    out = {}
    for name, cls in strategies.REGISTRY.items():
        short = "allow_short" in inspect.signature(cls).parameters
        out[name] = (lambda cls=cls, short=short: cls(allow_short=True) if short else cls())
    return out


def _selective_rules() -> dict:
    R = strategies.REGISTRY
    S = True  # shorts allowed everywhere
    rules = {
        "extreme_rev_z2.5": lambda: sel.ExtremeReversion(96, 2.5, allow_short=S),
        "extreme_rev_z3": lambda: sel.ExtremeReversion(96, 3.0, allow_short=S),
        "extreme_rev_z3.5": lambda: sel.ExtremeReversion(96, 3.5, allow_short=S),
        "extreme_rev_z3_p48": lambda: sel.ExtremeReversion(48, 3.0, allow_short=S),
        "capitulation_2pct": lambda: sel.CapitulationReversal(min_move=0.02, allow_short=S),
        "capitulation_3pct": lambda: sel.CapitulationReversal(min_move=0.03, allow_short=S),
        "capitulation_4pct": lambda: sel.CapitulationReversal(min_move=0.04, allow_short=S),
        "flow_0.15": lambda: sel.FlowImbalance(thresh=0.15, allow_short=S),
        "flow_0.25": lambda: sel.FlowImbalance(thresh=0.25, allow_short=S),
        "flow_0.25_hold12": lambda: sel.FlowImbalance(thresh=0.25, hold=12, allow_short=S),
        "btc_lead_0.5pct": lambda: sel.BtcLead(0.005, allow_short=S),
        "btc_lead_0.8pct": lambda: sel.BtcLead(0.008, allow_short=S),
        "session_breakout": lambda: sel.SessionBreakout(allow_short=S),
        "trend_pullback_200": lambda: sel.TrendPullback(200, allow_short=S),
        "trend_pullback_400": lambda: sel.TrendPullback(400, allow_short=S),
    }
    for base in ("ema_cross", "macd", "supertrend", "keltner_breakout", "donchian"):
        rules[f"trend[{base}]"] = lambda base=base: sel.Filtered(R[base](allow_short=S), "trend", 400)
    for base in ("vwap_reversion", "stochastic_reversal", "bollinger_reversion", "streak_reversal"):
        rules[f"range[{base}]"] = lambda base=base: sel.Filtered(R[base](allow_short=S), "range", 48)
    rules["range[extreme_rev_z2.5]"] = lambda: sel.Filtered(sel.ExtremeReversion(96, 2.5, allow_short=S), "range", 48)
    return rules


def _swing_rules() -> dict:
    rules = dict(_registry_rules())
    S = True
    rules.update({
        "extreme_rev_z2.5": lambda: sel.ExtremeReversion(60, 2.5, max_hold=20, allow_short=S),
        "trend_pullback_100": lambda: sel.TrendPullback(100, max_hold=20, allow_short=S),
        "sma_cross_10_50": lambda: strategies.SmaCross(10, 50, allow_short=S),
        "sma_cross_50_200": lambda: strategies.SmaCross(50, 200, allow_short=S),
        "donchian_20_10": lambda: strategies.DonchianBreakout(20, 10, allow_short=S),
        "momentum_30": lambda: strategies.Momentum(30, 0.05, allow_short=S),
        "momentum_90": lambda: strategies.Momentum(90, 0.10, allow_short=S),
    })
    for lb in (6, 30, 90):
        for mode in ("momentum", "reversal"):
            rules[f"xs_{mode}_lb{lb}"] = lambda lb=lb, mode=mode: sel.CrossSectional(lb, 6, mode)
    return rules


def _xsection_rules() -> dict:
    rules = {}
    for lb in (4, 24, 72, 168):
        for hold in (4, 24):
            for mode in ("momentum", "reversal"):
                rules[f"xs_{mode}_lb{lb}_h{hold}"] = lambda lb=lb, hold=hold, mode=mode: sel.CrossSectional(lb, hold, mode)
    for lb in (24, 168):
        rules[f"xs_momentum_lb{lb}_h24_top1"] = lambda lb=lb: sel.CrossSectional(lb, 24, "momentum", k=1)
        rules[f"xs_reversal_lb{lb}_h24_top1"] = lambda lb=lb: sel.CrossSectional(lb, 24, "reversal", k=1)
    return rules


EIGHT = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX"]
SWING_PROFILES = {
    "stop3": {"stop_loss_pct": 0.03, "take_profit_pct": None},
    "stop6": {"stop_loss_pct": 0.06, "take_profit_pct": None},
    "stop10_take20": {"stop_loss_pct": 0.10, "take_profit_pct": 0.20},
}
XS_PROFILES = {
    "stop3": {"stop_loss_pct": 0.03, "take_profit_pct": None},
    "stop8": {"stop_loss_pct": 0.08, "take_profit_pct": None},
}

GRIDS = {
    "swing": {"symbols": EIGHT, "days": {"4h": 1095, "1d": 1825}, "rules": _swing_rules, "profiles": SWING_PROFILES},
    "xsection": {"symbols": EIGHT, "days": {"1h": 730}, "rules": _xsection_rules, "profiles": XS_PROFILES},
    "short": {"symbols": ["BTC", "ETH", "SOL", "XRP"], "days": {"5m": 60, "15m": 120}, "rules": _registry_rules},
    "selective": {"symbols": ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX"],
                  "days": {"15m": 365, "1h": 730}, "rules": _selective_rules},
}


class Precomputed(strategies.Strategy):
    def __init__(self, series: list[int]):
        self.series = series

    def target(self, candles, i):
        return self.series[i]


def load(symbol: str, interval: str, days: int) -> list[Candle]:
    """Own candle cache, separate from the one a running experiment may be writing."""
    path = DIR / "candles" / f"{symbol}_{interval}_{days}_v2.csv"
    if path.exists() and time.time() - path.stat().st_mtime < 12 * 3600:
        with open(path, newline="", encoding="utf-8") as f:
            return [Candle(int(r[0]), *(float(x) for x in r[1:7]), int(r[7])) for r in csv.reader(f)]
    now = int(time.time() * 1000)
    step = INTERVAL_MS[interval]
    candles = [c for c in _download(data_symbol(symbol), interval, now - days * 86_400_000, now) if c.ts + step <= now]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for c in candles:
            w.writerow([c.ts, c.o, c.h, c.l, c.c, c.v, c.tb, c.n])
    return candles


def _segment(contract, base, risk, series, candles, interval, trade_from) -> dict:
    eq = float(base["start_equity"])
    sim = Simulator(contract, Precomputed(series), RiskManager(RiskConfig(**risk), contract, eq),
                    SimConfig(**base.get("sim", {})), eq, INTERVAL_MS[interval], trade_from)
    res = sim.run(candles)
    peak, dd = eq, 0.0
    for _, v in res.curve:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    final = res.curve[-1][1]
    return {"ret": round((final / eq - 1) * 100, 3), "dd": round(dd * 100, 2),
            "trades": sum(e.kind == "open" for e in res.events), "fees": round(res.fees, 2),
            "killed": sim.risk.killed}


def run_market(args) -> list[dict]:
    symbol, interval, base, grid_name = args
    grid = GRIDS[grid_name]
    days = grid["days"][interval]
    contract = get_contract(symbol)
    if base.get("zero_cost"):  # diagnostic: is there any edge before fees, slippage and funding?
        contract = dataclasses.replace(contract, taker_fee_pct=0.0, funding_rate_hourly=0.0)
    candles = load(symbol, interval, days)
    reference = {c.ts: c for c in load("BTC", interval, days)} if symbol != "BTC" else {}
    n = len(candles)
    a, b = int(n * 0.6), int(n * 0.8)
    span = (candles[-1].ts - candles[0].ts) / 86_400_000
    rows, universe = [], None
    profiles = grid.get("profiles", PROFILES)
    for name, factory in grid["rules"]().items():
        strat = factory()
        if getattr(strat, "needs_reference", False):
            strat.reference = reference
        if getattr(strat, "needs_universe", False):
            if universe is None:
                universe = {s: {c.ts: c.c for c in load(s, interval, days)} for s in grid["symbols"]}
            strat.symbol, strat.universe = symbol, universe
        series = [strat.target(candles, i) for i in range(n)]
        for i in range(min(strat.warmup, n)):
            series[i] = 0
        for profile, exits in profiles.items():
            risk = {**base["risk"], **exits}
            rows.append({
                "symbol": symbol, "interval": interval, "strategy": name, "profile": profile,
                "train": _segment(contract, base, risk, series[:a], candles[:a], interval, 0),
                "valid": _segment(contract, base, risk, series[:b], candles[:b], interval, candles[a].ts),
                "test": _segment(contract, base, risk, series, candles, interval, candles[b].ts),
                "days": {"train": span * 0.6, "valid": span * 0.2, "test": span * 0.2},
            })
    return rows


def aggregate(rows: list[dict]) -> list[dict]:
    """One line per configuration: means across the contracts, trade counts summed across them."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["strategy"], r["interval"], r["profile"]), []).append(r)
    out = []
    for (name, interval, profile), g in groups.items():
        m = lambda seg, k: sum(r[seg][k] for r in g) / len(g)  # noqa: E731
        per_day = lambda seg: sum(r[seg]["trades"] for r in g) / g[0]["days"][seg]  # noqa: E731
        out.append({
            "config": f"{name}/{interval}/{profile}", "strategy": name, "interval": interval, "profile": profile,
            "train": round(m("train", "ret"), 2), "valid": round(m("valid", "ret"), 2), "test": round(m("test", "ret"), 2),
            "train_trades": round(m("train", "trades"), 1), "test_trades": round(m("test", "trades"), 1),
            "train_trades_per_day": round(per_day("train"), 2),
            "test_trades_per_day": round(per_day("test"), 2),
            "test_dd": round(m("test", "dd"), 2),
            "symbols_pos_trainvalid": sum(r["train"]["ret"] + r["valid"]["ret"] > 0 for r in g),
            "symbols_pos_test": sum(r["test"]["ret"] > 0 for r in g),
            "n_symbols": len(g),
        })
    return out


def select(agg: list[dict], min_trades_per_day: float = 0.0) -> list[dict]:
    """Selection rule, fixed before the test column is read.

    Eligible: positive mean return in train AND in validation, positive train+validation on at
    least 60 % of the contracts, at least 20 trades per contract in train, and (optionally) a
    minimum number of trades per day across all contracts. Ranked by the smaller of the train
    and validation means, so one lucky segment cannot carry a configuration.
    """
    ok = [c for c in agg if c["train"] > 0 and c["valid"] > 0
          and c["symbols_pos_trainvalid"] >= math.ceil(0.6 * c["n_symbols"])
          and c["train_trades"] >= 20 and c["train_trades_per_day"] >= min_trades_per_day]
    return sorted(ok, key=lambda c: -min(c["train"], c["valid"]))


def run(base: dict, grid_name: str = "short", min_trades_per_day: float = 0.0) -> dict:
    grid = GRIDS[grid_name]
    DIR.mkdir(parents=True, exist_ok=True)
    tasks = [(s, i, base, grid_name) for i in grid["days"] for s in grid["symbols"]]
    for s, i, _, _ in tasks:  # download one at a time; the workers then read the cache
        load(s, i, grid["days"][i])
        load("BTC", i, grid["days"][i])
    with Pool(min(8, len(tasks))) as pool:
        rows = [r for part in pool.map(run_market, tasks, chunksize=1) for r in part]
    agg = aggregate(rows)
    chosen = select(agg, min_trades_per_day)
    out = {"generated": int(time.time() * 1000), "grid": grid_name, "rows": rows, "configs": agg,
           "selected": [c["config"] for c in chosen]}
    name = f"results_{grid_name}{'_zero_cost' if base.get('zero_cost') else ''}.json"
    (DIR / name).write_text(json.dumps(out), encoding="utf-8")
    return out


def table(configs: list[dict]) -> str:
    cols = ["config", "train", "valid", "test", "train_trades_per_day", "test_trades_per_day",
            "test_dd", "symbols_pos_trainvalid", "symbols_pos_test"]
    if not configs:
        return "(none)"
    w = [max(len(c), *(len(str(r[c])) for r in configs)) + 2 for c in cols]
    lines = ["".join(c.ljust(x) for c, x in zip(cols, w))]
    lines += ["".join(str(r[c]).ljust(x) for c, x in zip(cols, w)) for r in configs]
    return "\n".join(lines)
