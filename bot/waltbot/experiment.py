"""Overnight forward test: a pre-registered panel of rule variants, paper-traded on live prices.

Nothing here sends orders. Each variant is an independent simulated 100 USDC account using
Walt's fee, sizing, funding and liquidation model on Binance spot candles. The process runs
unattended, re-evaluates every 15 minutes, and rewrites REPORT.md on every cycle so a
readable report exists whenever it is opened; the last cycle marks it final.
"""
from __future__ import annotations

import inspect
import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import strategies
from .backtest import make_simulator, summarize
from .contracts import ROOT, get_contract
from .data import INTERVAL_MS, data_symbol, get_candles

DIR = ROOT / "data" / "experiment"
STATE = DIR / "state.json"
RESULTS = DIR / "results.json"
REPORT = ROOT / "REPORT.md"

SYMBOLS = ["BTC", "ETH", "SOL", "XRP"]
BACKTEST_DAYS = {"15m": 90, "1h": 365, "4h": 365}
PRIMARY = ("sma_cross", "4h")  # fixed before the run: highest mean one-year backtest return
WARMUP_BARS = 210
CYCLE_MS = 900_000


def _now() -> int:
    return int(time.time() * 1000)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def variants() -> list[dict]:
    out = []
    for interval in BACKTEST_DAYS:
        for name, cls in strategies.REGISTRY.items():
            params = {"allow_short": True} if "allow_short" in inspect.signature(cls).parameters else {}
            for sym in SYMBOLS:
                out.append({"id": f"{sym}-{interval}-{name}", "symbol": sym, "interval": interval,
                            "strategy": name, "params": params})
    return out


def _cfg(base: dict, v: dict) -> dict:
    return {**base, "symbol": v["symbol"], "interval": v["interval"],
            "strategy": {"name": v["strategy"], "params": v["params"]}}


def _candles(sym: str, interval: str, days: float):
    return get_candles(data_symbol(sym), interval, days)


def baselines(base: dict) -> dict:
    """Historical backtest of every variant, with a 70/30 split, as the reference expectation."""
    out = {}
    for interval, days in BACKTEST_DAYS.items():
        for sym in SYMBOLS:
            contract = get_contract(sym)
            candles = _candles(sym, interval, days)
            cut = int(len(candles) * 0.7)
            for v in variants():
                if v["symbol"] != sym or v["interval"] != interval:
                    continue
                c = _cfg(base, v)
                full = summarize(make_simulator(c, contract).run(candles), candles)
                early = summarize(make_simulator(c, contract).run(candles[:cut]), candles[:cut])
                late = summarize(make_simulator(c, contract, candles[cut].ts).run(candles), candles[cut:])
                out[v["id"]] = {"days": days, "return_pct": full["return_pct"],
                                "in_sample_pct": early["return_pct"], "out_sample_pct": late["return_pct"],
                                "max_dd_pct": full["max_drawdown_pct"], "trips": full["round_trips"]}
            print(f"baseline {sym} {interval} done", flush=True)
    return out


def start(base: dict, hours: float, reset: bool) -> dict:
    DIR.mkdir(parents=True, exist_ok=True)
    if STATE.exists() and not reset:
        return json.loads(STATE.read_text(encoding="utf-8"))
    state = {"config": base, "baselines": baselines(base)}
    # The clock starts after the baselines are computed, so no forward time is lost to them.
    state["start_ts"] = _now()
    state["end_ts"] = state["start_ts"] + int(hours * 3_600_000)
    STATE.write_text(json.dumps(state), encoding="utf-8")
    return state


def cycle(state: dict, final: bool = False) -> dict:
    base, start_ts = state["config"], state["start_ts"]
    equity0 = float(base["start_equity"])
    rows, prices, cache = [], {}, {}
    for v in variants():
        key = (v["symbol"], v["interval"])
        step = INTERVAL_MS[v["interval"]]
        if key not in cache:
            days = (WARMUP_BARS * step + (_now() - start_ts)) / 86_400_000 + 1
            cache[key] = _candles(v["symbol"], v["interval"], days)
        candles = cache[key]
        contract = get_contract(v["symbol"])
        sim = make_simulator(_cfg(base, v), contract, trade_from_ts=start_ts)
        res = sim.run(candles)
        window = [(ts, eq) for ts, eq in res.curve if ts + step >= start_ts]
        peak, dd = equity0, 0.0
        for _, eq in window:
            peak = max(peak, eq)
            dd = max(dd, 1 - eq / peak)
        events = [e for e in res.events if e.kind != "skip"]
        exits = [e for e in events if e.kind != "open"]
        pos = res.position
        final_eq = window[-1][1] if window else equity0
        rows.append({
            "id": v["id"], "symbol": v["symbol"], "interval": v["interval"], "strategy": v["strategy"],
            "bars": len(window), "return_pct": round((final_eq / equity0 - 1) * 100, 3),
            "max_dd_pct": round(dd * 100, 3), "entries": sum(e.kind == "open" for e in events),
            "exits": len(exits), "stops": sum(e.kind == "stop" for e in exits),
            "liquidations": sum(e.kind == "liquidation" for e in exits),
            "fees": round(res.fees, 4), "funding": round(res.funding + (pos.funding_paid if pos else 0.0), 4),
            "position": {1: "long", -1: "short"}[pos.side] if pos else "flat",
            "events": [{"time": _iso(e.ts), "kind": e.kind, "side": {1: "long", -1: "short"}[e.side],
                        "price": e.price, "pnl": round(e.pnl, 4)} for e in events],
        })
        if v["interval"] == "15m" and v["symbol"] not in prices:
            before = [c for c in candles if c.ts + step <= start_ts]
            p0 = before[-1].c if before else candles[0].o
            prices[v["symbol"]] = {"start": p0, "last": candles[-1].c,
                                   "change_pct": round((candles[-1].c / p0 - 1) * 100, 3)}
    out = {"updated_ts": _now(), "final": final, "rows": rows, "prices": prices}
    RESULTS.write_text(json.dumps(out), encoding="utf-8")
    REPORT.write_text(render(state, out), encoding="utf-8")
    return out


# --- report ---------------------------------------------------------------
def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def render(state: dict, out: dict) -> str:
    base, bl, rows = state["config"], state["baselines"], out["rows"]
    risk = base["risk"]
    hours = (min(out["updated_ts"], state["end_ts"]) - state["start_ts"]) / 3_600_000
    planned = (state["end_ts"] - state["start_ts"]) / 3_600_000
    primary = [r for r in rows if (r["strategy"], r["interval"]) == PRIMARY]
    traded = [r for r in rows if r["entries"] > 0]
    pos = [r for r in traded if r["return_pct"] > 0]
    neg = [r for r in traded if r["return_pct"] < 0]
    p_ret = _mean(r["return_pct"] for r in primary)
    p_entries = sum(r["entries"] for r in primary)
    p_exits = sum(r["exits"] for r in primary)
    all_ret = _mean(r["return_pct"] for r in rows)
    status = "FINAL" if out["final"] else "INTERIM (run still in progress)"
    verdict = "positive" if p_ret > 0 else "negative" if p_ret < 0 else "zero (no change in equity)"
    best = max(rows, key=lambda r: r["return_pct"])
    worst = min(rows, key=lambda r: r["return_pct"])
    L = []
    a = L.append
    a("# Overnight forward test of rule-based perpetual-futures strategies under Walt Perps costs")
    a("")
    a(f"**Status: {status}.** Generated {_iso(out['updated_ts'])}. This file is rewritten by the bot on every cycle.")
    a("")
    a("**Nature of the run: simulated (paper) trading on live market prices. No real orders were placed and no money was at risk.**")
    a("")
    a("## Abstract")
    a("")
    a(f"We forward-tested {len(rows)} pre-registered variants of six textbook trading rules on four crypto "
      f"perpetual contracts (BTC, ETH, SOL, XRP) for {hours:.1f} hours (planned: {planned:.1f}), each as an independent "
      f"simulated {base['start_equity']} USDC account at {risk['leverage']}x leverage with Walt Perps' fee, sizing, funding "
      f"and liquidation rules. The primary variant, fixed before the run, was a 20/100 moving-average crossover on "
      f"4-hour bars with long and short positions. Its mean return across the four contracts was **{p_ret:+.3f} %** "
      f"({verdict}), from {p_entries} entries and {p_exits} exits. Across all variants the mean return was {all_ret:+.3f} %; "
      f"{len(pos)} variants that traded were positive and {len(neg)} negative. "
      f"A window this short contains too few trades to separate skill from chance; see Limitations.")
    a("")
    a("## 1. Question and hypotheses")
    a("")
    a("Can a simple, fully mechanical rule earn a positive return net of costs on Walt Perps at 3x leverage?")
    a("")
    a("- **H0:** the primary rule's net return over the test window is zero or negative.")
    a("- **H1:** the primary rule's net return over the test window is positive.")
    a("- Secondary: do the forward results of the wider panel resemble their historical backtests?")
    a("")
    a("## 2. Pre-registration")
    a("")
    a(f"Everything in this section was fixed in code before the clock started at **{_iso(state['start_ts'])}**; "
      f"the planned end was {_iso(state['end_ts'])}.")
    a("")
    a("- **Primary variant:** `sma_cross` (fast 20, slow 100, long and short) on 4-hour bars, on BTC, ETH, SOL and XRP. "
      "Selection rule: among the twelve rule/timeframe pairs on 1-hour and 4-hour bars, the pair with the highest "
      "mean one-year backtest return across the four contracts under the risk settings below.")
    a("- **Panel:** all six rules (`sma_cross`, `donchian`, `momentum`, `rsi_reversion`, `bollinger_reversion`, "
      "`trend_rsi`) with default parameters on 15-minute, 1-hour and 4-hour bars, shorts enabled where the rule supports them.")
    a(f"- **Risk settings (every variant):** leverage {risk['leverage']}x; margin per trade {risk['margin_fraction']:.0%} of equity, "
      f"capped at {risk['max_margin_quote']} USDC; stop-loss {risk['stop_loss_pct']:.0%} from entry; daily loss limit "
      f"{risk['daily_loss_limit_pct']:.0%}; kill switch at {risk['max_drawdown_pct']:.0%} drawdown.")
    a("- **Entry at start:** a variant whose rule already signals a side when the clock starts enters on the first bar that closes after the start.")
    a("- **Outcome measure:** mark-to-market equity change per account, net of fees and funding, open positions valued at the last close.")
    a("")
    a("## 3. Methods")
    a("")
    a("- **Prices:** Binance spot candles (public API), closed bars only. Walt exposes no price history.")
    a("- **Execution model:** signals are computed on a bar's close and filled at the next bar's open, moved "
      f"{base['sim']['fill_slippage']:.2%} against the trade. Market orders only, one position per contract.")
    a("- **Costs:** Walt taker fee per contract (0.09 % for these four at snapshot time), charged on entry and exit; "
      "hourly funding at each contract's rate from the 2026-11-14 snapshot, held constant.")
    a("- **Sizing:** Walt's formula: notional = margin / (1/leverage + fee); base size from the slippage-widened price, floored to the contract step.")
    a("- **Liquidation:** when position equity falls to the contract's closeout margin fraction of notional; the remaining margin is lost.")
    a("- **Software:** `waltbot` (this repository), 21 unit tests passing at start, including a no-look-ahead test for every rule.")
    a("")
    a("## 4. Results")
    a("")
    a("### 4.1 Market during the window")
    a("")
    a("| Contract | Price at start | Last price | Change |")
    a("| --- | --- | --- | --- |")
    for s, p in out["prices"].items():
        a(f"| {s} | {p['start']:.6g} | {p['last']:.6g} | {p['change_pct']:+.3f} % |")
    a("")
    a("### 4.2 Primary variant (`sma_cross`, 4-hour bars)")
    a("")
    a("| Contract | Return | Max drawdown | Entries | Exits | Fees (USDC) | Funding (USDC) | Position at end | 1-year backtest |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in primary:
        a(f"| {r['symbol']} | {r['return_pct']:+.3f} % | {r['max_dd_pct']:.3f} % | {r['entries']} | {r['exits']} | "
          f"{r['fees']:.4f} | {r['funding']:+.4f} | {r['position']} | {bl[r['id']]['return_pct']:+.2f} % |")
    a(f"| **Mean** | **{p_ret:+.3f} %** | | {p_entries} | {p_exits} | | | | "
      f"{_mean(bl[r['id']]['return_pct'] for r in primary):+.2f} % |")
    a("")
    a(f"**Result on the primary hypothesis: {verdict}.** "
      + ("H0 is not rejected." if p_ret <= 0 else
         "The sign favours H1, but with this few trades the result is not statistically distinguishable from chance."))
    a("")
    a("### 4.3 Panel summary by rule and timeframe (mean over the four contracts)")
    a("")
    a("| Rule | Timeframe | Mean return | Entries | Exits | Stops | Mean 1-year backtest* |")
    a("| --- | --- | --- | --- | --- | --- | --- |")
    for interval in BACKTEST_DAYS:
        for name in strategies.REGISTRY:
            g = [r for r in rows if r["strategy"] == name and r["interval"] == interval]
            a(f"| `{name}` | {interval} | {_mean(r['return_pct'] for r in g):+.3f} % | {sum(r['entries'] for r in g)} | "
              f"{sum(r['exits'] for r in g)} | {sum(r['stops'] for r in g)} | "
              f"{_mean(bl[r['id']]['return_pct'] for r in g):+.2f} % |")
    a("")
    a("\\* 15-minute variants were backtested over 90 days, the others over 365 days.")
    a("")
    a(f"- Variants that placed at least one trade: {len(traded)} of {len(rows)}; positive {len(pos)}, negative {len(neg)}.")
    a(f"- Best variant: `{best['id']}` {best['return_pct']:+.3f} %. Worst: `{worst['id']}` {worst['return_pct']:+.3f} %.")
    a(f"- Liquidations: {sum(r['liquidations'] for r in rows)}. Stop-loss exits: {sum(r['stops'] for r in rows)}.")
    a("")
    a("### 4.4 All variants")
    a("")
    a("| Variant | Return | Max DD | Entries | Exits | Fees | Funding | Position | Backtest full | Backtest in-sample | Backtest out-of-sample |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in sorted(rows, key=lambda r: -r["return_pct"]):
        b = bl[r["id"]]
        a(f"| `{r['id']}` | {r['return_pct']:+.3f} % | {r['max_dd_pct']:.3f} % | {r['entries']} | {r['exits']} | {r['fees']:.4f} | "
          f"{r['funding']:+.4f} | {r['position']} | {b['return_pct']:+.2f} % | {b['in_sample_pct']:+.2f} % | {b['out_sample_pct']:+.2f} % |")
    a("")
    a("### 4.5 Trade log of the primary variant")
    a("")
    log = [(e, r["symbol"]) for r in primary for e in r["events"]]
    if log:
        a("| Time | Contract | Event | Side | Price | Realized PnL (USDC) |")
        a("| --- | --- | --- | --- | --- | --- |")
        for e, s in sorted(log, key=lambda x: x[0]["time"]):
            a(f"| {e['time']} | {s} | {e['kind']} | {e['side']} | {e['price']:.6g} | {e['pnl']:+.4f} |")
    else:
        a("No trades were placed by the primary variant in the window.")
    a("")
    a("## 5. Limitations")
    a("")
    a("- **Simulated, not real.** No order reached Walt. Real fills, Walt's own perp price, its real funding payments and its 1 % worst-case slippage band can all differ from the model.")
    a(f"- **Sample size.** {hours:.1f} hours holds at most a few bars on the 4-hour timeframe. The primary variant made {p_entries} entries; "
      "no conclusion about profitability can be drawn from that. The rule trades roughly 25 times per year per contract in backtest, so a meaningful test needs months.")
    a("- **Selection bias.** The primary variant was chosen as the best of 12 rule/timeframe pairs on one year of history, using both halves of that year. Its backtest figures are therefore optimistic.")
    a("- **Single regime.** The backtest year was a steady decline (BTC about -31 %, XRP about -58 %), which favours any rule that can hold shorts. A rising or sideways market was not tested.")
    a("- **Multiple comparisons.** With 72 variants, some will be positive by chance alone; the best variant in section 4.3 should not be read as a discovery.")
    a("- **Constant funding.** Funding is modelled at one snapshot rate; real rates change hourly and can flip sign.")
    a("- **Correlated contracts.** The four contracts move together, so they are closer to one observation than four.")
    a("")
    a("## 6. Conclusion")
    a("")
    a(f"Over {hours:.1f} hours of simulated trading, the pre-registered primary rule returned {p_ret:+.3f} % on average "
      f"({verdict}); the 72-variant panel averaged {all_ret:+.3f} %. Historical backtests of the same rules were mostly "
      "negative on 15-minute and 1-hour bars, where fees and stop-outs dominate, and mixed-to-positive on 4-hour bars in a "
      "falling market. The overnight window is consistent with those backtests but far too short to confirm or refute them. "
      "A real-money replication would need a funded account, an owner-authorized executor, and a duration of several months.")
    a("")
    a("## Reproduction")
    a("")
    a("```")
    a("python -m waltbot experiment --hours 8 --reset")
    a("```")
    a("")
    a("Raw per-variant results, including every simulated trade: `data/experiment/results.json`. "
      "Pre-registration and backtest baselines: `data/experiment/state.json`.")
    a("")
    return "\n".join(L)


def _stay_awake() -> None:
    """Ask Windows not to idle-sleep while this process runs. Changes no system setting."""
    try:
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # CONTINUOUS | SYSTEM_REQUIRED
    except Exception:
        pass


def run(base: dict, hours: float, reset: bool = False) -> None:
    _stay_awake()
    state = start(base, hours, reset)
    print(f"experiment started {_iso(state['start_ts'])}, ends {_iso(state['end_ts'])}", flush=True)
    while True:
        final = _now() >= state["end_ts"]
        try:
            out = cycle(state, final)
            prim = [r for r in out["rows"] if (r["strategy"], r["interval"]) == PRIMARY]
            print(f"{_iso(out['updated_ts'])} cycle ok; primary mean {_mean(r['return_pct'] for r in prim):+.3f} %", flush=True)
            if final:
                print("experiment finished; REPORT.md is final", flush=True)
                return
        except Exception:
            traceback.print_exc()
            if final and _now() > state["end_ts"] + 3_600_000:
                return
        nxt = min((_now() // CYCLE_MS + 1) * CYCLE_MS + 20_000, max(state["end_ts"] + 20_000, _now() + 60_000))
        time.sleep(max(5, (nxt - _now()) / 1000))
