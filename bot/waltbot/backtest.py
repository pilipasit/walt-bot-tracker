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

# Markets-page trend rule backtest. The existing strategy simulator above remains independent.
_MARKET_HOUR = 3_600_000
_MARKET_HORIZONS = (4, 12, 24)
_MARKET_BUCKETS = ('0-0.1', '0.1-0.2', '0.2-0.4', '0.4+')


def market_signal(closes: list[float], t: int) -> dict:
    """Build a signal using only closes observable at t."""
    from .markets import analyse, _window_trend

    history = closes[:t + 1]
    signal = analyse(history)
    windows = [_window_trend(history, n) for n in (72, 168)]
    signal['aligned'] = signal['trend'] != 'sideways' and all(
        w and w['trend'] == signal['trend'] for w in windows)
    return signal


def market_trade(candles: list, t: int, direction: int, stop_pct: float) -> dict | None:
    """Return a completed trade, or None when the data ends before its outcome is known."""
    entry, start = candles[t][4], candles[t][0]
    stop = entry * (1 - direction * stop_pct / 100)
    target = entry * (1 + direction * 2 * stop_pct / 100)
    deadline = start + 72 * _MARKET_HOUR
    last = None
    for candle in candles[t + 1:]:
        if candle[0] > deadline:
            break
        last = candle
        stopped = candle[3] <= stop if direction == 1 else candle[2] >= stop
        hit = candle[2] >= target if direction == 1 else candle[3] <= target
        hours = (candle[0] - start) / _MARKET_HOUR
        if stopped:
            return {'outcome': 'stop', 'pnl_pct': -stop_pct - 0.14, 'hours': hours}
        if hit:
            return {'outcome': 'tp', 'pnl_pct': 2 * stop_pct - 0.14, 'hours': hours}
    if candles[-1][0] < deadline:
        return None
    # A market closure can leave no candle exactly at the deadline. Exit at the last
    # observed close at or before it, and count the full elapsed holding period.
    exit_price = last[4] if last else entry
    return {'outcome': 'timeout', 'pnl_pct': direction * (exit_price / entry - 1) * 100 - 0.14,
            'hours': 72}


def _market_summary(rows: list[dict]) -> dict:
    import statistics

    episodes = [r for r in rows if r['episode']]
    def trade_stats(selected, key):
        trades = [r[key] for r in selected if r[key] is not None]
        n = len(trades)
        return {'completed': n, 'tp_rate': round(sum(x['outcome'] == 'tp' for x in trades) / n, 4) if n else None,
                'stop_rate': round(sum(x['outcome'] == 'stop' for x in trades) / n, 4) if n else None,
                'timeout_rate': round(sum(x['outcome'] == 'timeout' for x in trades) / n, 4) if n else None,
                'avg_pnl_pct': round(statistics.mean(x['pnl_pct'] for x in trades), 4) if n else None,
                'median_exit_hours': round(statistics.median(x['hours'] for x in trades), 2) if n else None}
    # Exact expectation of a fair random direction: both possible trades have equal weight.
    # Compare only entries for which both hypothetical outcomes are observable.
    paired = [r for r in episodes if r['long'] is not None and r['short'] is not None]
    baseline = [{'trade': r[k]} for r in paired for k in ('long', 'short')]
    signal_stats = trade_stats(rows, 'trade')
    paired_stats = trade_stats(paired, 'trade')
    baseline_stats = trade_stats(baseline, 'trade')
    result = {'signal_hours': len(rows), 'episodes': len(episodes),
              'signals': {k: signal_stats[k] for k in ('completed', 'tp_rate', 'stop_rate', 'timeout_rate', 'avg_pnl_pct')},
              'episode': trade_stats(episodes, 'trade'),
              'paired_tp_rate': paired_stats['tp_rate'], 'paired_episodes': len(paired),
              'random_tp_rate': baseline_stats['tp_rate']}
    result['forward'] = {}
    for h in _MARKET_HORIZONS:
        values = [r['forward'][h] for r in rows if r['forward'][h] is not None]
        result['forward'][str(h) + 'h'] = {
            'count': len(values), 'share': round(sum(v > 0 for v in values) / len(values), 4) if values else None}
    return result


def _market_groups(rows: list[dict]) -> dict:
    return {'all': _market_summary(rows),
            'quality': {b: _market_summary([r for r in rows if r['bucket'] == b]) for b in _MARKET_BUCKETS},
            'alignment': {str(a).lower(): _market_summary([r for r in rows if r['aligned'] == a])
                          for a in (True, False)}}


def run(data_dir: str | Path) -> dict:
    """Backtest all candle files, retaining aggregate statistics only."""
    all_rows, assets = [], {}
    files = sorted(Path(data_dir).glob('*.json'))
    if not files:
        raise ValueError(f'No JSON candle files found in {data_dir}')
    for path in files:
        data = json.loads(path.read_text(encoding='utf-8'))
        candles = data['candles']
        if any(c[0] >= d[0] for c, d in zip(candles, candles[1:])):
            raise ValueError(f'{path}: candle timestamps must be strictly increasing')
        closes = [c[4] for c in candles]
        by_time = {c[0]: c[4] for c in candles}
        rows, previous, previous_time = [], None, None
        for t in range(47, len(candles)):
            signal = market_signal(closes, t)
            trend = signal['trend']
            ts = candles[t][0]
            if trend not in ('up', 'down'):
                previous, previous_time = trend, ts
                continue
            direction = 1 if trend == 'up' else -1
            stop = 2.5 * signal['atr_pct_h'] + signal['band_pct']
            long = market_trade(candles, t, 1, stop)
            short = market_trade(candles, t, -1, stop)
            score = signal['score']
            bucket = _MARKET_BUCKETS[0 if score < .1 else 1 if score < .2 else 2 if score < .4 else 3]
            forward = {}
            for h in _MARKET_HORIZONS:
                price = by_time.get(ts + h * _MARKET_HOUR)
                forward[h] = direction * (price / closes[t] - 1) * 100 if price is not None else None
            rows.append({'episode': trend != previous or previous_time != ts - _MARKET_HOUR,
                         'bucket': bucket, 'aligned': signal['aligned'], 'forward': forward,
                         'long': long, 'short': short, 'trade': long if direction == 1 else short})
            previous, previous_time = trend, ts
        assets[data['symbol']] = {'group': data['group'], **_market_groups(rows)}
        all_rows.extend(rows)
    return {'method': {'history_min_candles': 48, 'trade_horizon_elapsed_hours': 72, 'fee_per_side_pct': 0.07,
                       'episodes': 'Consecutive hourly candles with the same directional trend; gaps break episodes.',
                       'bucket_assignment': 'Each signal hour by its own score/alignment; episodes by their first hour.',
                       'forward_returns': 'Exact elapsed-hour close; missing timestamps excluded. Signal hours, not episodes.',
                       'censoring': 'Unresolved trades at end of data excluded; baseline and paired rule use only entries with both directions resolved.',
                       'baseline': 'Equal-weight long and short outcomes at each eligible episode entry.',
                       'timeout': 'At 72 elapsed hours, exit at the last close at or before the deadline.'},
            'overall': _market_groups(all_rows), 'assets': assets}
