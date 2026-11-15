"""Ledger for the "language model as discretionary trader" experiment. Simulated; sends no orders.

The model (Claude Fable 5.2) decides once an hour. This module does everything that must not
depend on the model's judgment: market data, fills, fees, funding, stops, liquidation,
the tamper-evident log and the report.

  brief    process stops/funding since the last check, mark the account, print the market table
  decide   record a decision (reasoning first, then simulated fills at the Walt rates supplied)
  report   regenerate LLM_EXPERIMENT.md

Every log entry carries the SHA-256 of the previous one, so entries cannot be edited or
reordered afterwards without breaking the chain.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .contracts import ROOT, Contract, load_contracts
from .data import _download

DIR = ROOT / "data" / "llm"
STATE, LOG, CONTRACTS = DIR / "state.json", DIR / "log.jsonl", DIR / "contracts.json"
REPORT = ROOT / "LLM_EXPERIMENT.md"
CONCLUSION = DIR / "conclusion.md"

BUDGET = 100.0
MAX_LEVERAGE = 5
SLIPPAGE = 0.0005
DECISIONS_PLANNED = 24
HOUR = 3_600_000
# Walt crypto contracts that also trade on Binance spot, so intrahour highs and lows exist
# for checking stops. Stocks, FX and commodities on Walt are excluded for that reason.
UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX", "BNB", "SUI", "NEAR", "DOT", "TRX",
            "UNI", "AAVE", "BCH", "APT", "ARB", "OP", "WLD", "ENA", "ZEC"]
SIDE = {"long": 1, "short": -1}
NAME = {1: "long", -1: "short"}


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def contracts() -> dict[str, Contract]:
    return load_contracts(CONTRACTS)


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    raise SystemExit("Experiment not started. Run: python -m waltbot llm start --contracts <snapshot>")


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def append(state: dict, entry: dict) -> dict:
    entry = {"n": state["entries"], "time": iso(now_ms()), "ts": now_ms(), **entry, "prev_hash": state["last_hash"]}
    entry["hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    state["entries"] += 1
    state["last_hash"] = entry["hash"]
    return entry


def read_log() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_chain() -> bool:
    prev = "genesis"
    for e in read_log():
        body = {k: v for k, v in e.items() if k != "hash"}
        if e["prev_hash"] != prev or hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != e["hash"]:
            return False
        prev = e["hash"]
    return True


# --- account mechanics ---------------------------------------------------
def liquidation_price(p: dict, c: Contract) -> float:
    f = c.closeout_margin_fraction
    if p["side"] > 0:
        return (p["entry"] * p["size"] - p["margin"] + p["funding_paid"]) / (p["size"] * (1 - f))
    return (p["margin"] - p["funding_paid"] + p["entry"] * p["size"]) / (p["size"] * (1 + f))


def unrealized(p: dict, price: float) -> float:
    return p["side"] * (price - p["entry"]) * p["size"] - p["funding_paid"]


def close_position(state: dict, symbol: str, price: float, kind: str, c: Contract) -> dict:
    p = state["positions"].pop(symbol)
    if kind == "liquidation":
        pnl, fee = -p["margin"], 0.0
    else:
        fee = p["size"] * price * c.taker_fee_pct
        pnl = max(unrealized(p, price) - fee, -p["margin"])
    state["cash"] += pnl
    state["fees"] += fee
    state["funding"] += p["funding_paid"]
    return {"symbol": symbol, "kind": kind, "side": NAME[p["side"]], "price": price, "size": p["size"],
            "pnl": round(pnl, 4), "fee": round(fee, 4), "entry": p["entry"], "entry_fee": p["entry_fee"]}


def process_since_last_check(state: dict) -> list[dict]:
    """Apply funding and any stop, take-profit or liquidation hit since the last check.

    Uses Binance 5-minute highs and lows scaled by the Walt/Binance price ratio observed at
    entry. If a bar touches both the stop and the take-profit, the stop is assumed first.
    """
    cs, events, now = contracts(), [], now_ms()
    for symbol in list(state["positions"]):
        p, c = state["positions"][symbol], cs[symbol]
        bars = [b for b in _download(f"{symbol}USDT", "5m", p["checked_ts"], now) if b.ts + 300_000 <= now]
        for b in bars:
            hours = 300_000 / HOUR
            p["funding_paid"] += p["side"] * c.funding_rate_hourly * p["size"] * b.o * p["basis"] * hours
            hi, lo, op = b.h * p["basis"], b.l * p["basis"], b.o * p["basis"]
            adverse, favorable = (lo, hi) if p["side"] > 0 else (hi, lo)
            liq = liquidation_price(p, c)
            stop_hit = (adverse - p["stop"]) * p["side"] <= 0
            liq_hit = (adverse - liq) * p["side"] <= 0
            ev = None
            if stop_hit and (not liq_hit or (p["stop"] - liq) * p["side"] > 0):
                level = op if (op - p["stop"]) * p["side"] <= 0 else p["stop"]
                ev = close_position(state, symbol, level * (1 - p["side"] * SLIPPAGE), "stop", c)
            elif liq_hit:
                ev = close_position(state, symbol, liq, "liquidation", c)
            elif p.get("take") and (favorable - p["take"]) * p["side"] >= 0:
                level = op if (op - p["take"]) * p["side"] >= 0 else p["take"]
                ev = close_position(state, symbol, level * (1 - p["side"] * SLIPPAGE), "take", c)
            p["checked_ts"] = b.ts + 300_000
            if ev:
                ev["bar_time"] = iso(b.ts)
                events.append(ev)
                break
    return events


def equity(state: dict, marks: dict[str, float]) -> float:
    return state["cash"] + sum(unrealized(p, marks[s] * p["basis"]) for s, p in state["positions"].items())


# --- market brief --------------------------------------------------------
def _rsi(closes: list[float], n: int = 14) -> float:
    gain = loss = 0.0
    for a, b in zip(closes[-n - 1 : -1], closes[-n:]):
        gain += max(b - a, 0)
        loss += max(a - b, 0)
    return 100 * gain / (gain + loss) if gain + loss else 50.0


def market_table() -> tuple[list[dict], dict[str, float]]:
    cs, rows, marks, now = contracts(), [], {}, now_ms()
    for s in UNIVERSE:
        if s not in cs:
            continue
        try:
            bars = [b for b in _download(f"{s}USDT", "1h", now - 200 * HOUR, now)]
        except OSError:
            continue
        if len(bars) < 170:
            continue
        closes = [b.c for b in bars]
        last = closes[-1]  # includes the hour still forming: the latest traded price
        done = bars[:-1]
        r = lambda k: (last / closes[-1 - k] - 1) * 100  # noqa: E731
        rets = [math.log(b / a) for a, b in zip(closes[-25:-1], closes[-24:])]
        vol = (sum(x * x for x in rets) / len(rets)) ** 0.5 * 100
        hi, lo = max(b.h for b in bars[-24:]), min(b.l for b in bars[-24:])
        vavg = sum(b.v for b in done[-168:]) / 168
        flow = sum(2 * b.tb - b.v for b in done[-4:]) / max(sum(b.v for b in done[-4:]), 1e-9)
        c = cs[s]
        marks[s] = last
        rows.append({"symbol": s, "price": last, "r1h": r(1), "r4h": r(4), "r24h": r(24), "r7d": r(168),
                     "rsi": _rsi(closes[:-1]), "vol1h": vol, "pos24": (last - lo) / (hi - lo) * 100 if hi > lo else 50,
                     "volx": done[-1].v / vavg if vavg else 0, "flow4": flow,
                     "fund": c.funding_rate_hourly * 100, "maxlev": c.max_leverage})
    return rows, marks


def brief(contracts_path: str | None = None) -> None:
    if contracts_path:
        shutil.copyfile(contracts_path, CONTRACTS)
        try:  # keep every snapshot's funding rates: Walt publishes no funding history
            from .funding_log import ingest

            ingest(contracts_path)
        except Exception as e:
            print(f"(funding log not updated: {e})")
        try:  # separate forward paper test of the funding-carry basket; never affects this ledger
            from .carry_paper import update

            c = update(contracts_path)
            print(f"CARRY PAPER basket {c['equity']:.2f} (funding {c['funding']:+.3f}, price {c['price']:+.3f}, costs -{c['fees']:.3f})")
        except Exception as e:
            print(f"(carry paper test not updated: {e})")
    try:  # keep dashboard.html's record of the live bot's trades fresh
        from .dashboard import write_data

        write_data()
    except Exception:
        pass
    state = load_state()
    events = process_since_last_check(state)
    rows, marks = market_table()
    for s in state["positions"]:
        if s not in marks:
            raise SystemExit(f"no market data for open position {s}")
    eq = equity(state, marks)
    for ev in events:
        append(state, {"type": "event", **ev})
    append(state, {"type": "mark", "equity": round(eq, 4), "cash": round(state["cash"], 4),
                   "btc": marks.get("BTC"), "positions": {s: NAME[p["side"]] for s, p in state["positions"].items()}})
    save_state(state)
    write_report()
    cs = contracts()
    print(f"TIME {iso(now_ms())} | decisions made {state['decisions']}/{DECISIONS_PLANNED} | started {iso(state['start_ts'])}")
    print(f"EQUITY {eq:.2f} USD (start {BUDGET:.0f}) | realized cash {state['cash']:.2f} | fees {state['fees']:.2f} | funding {state['funding']:.2f}")
    for ev in events:
        print(f"EVENT {ev['kind']} {ev['symbol']} {ev['side']} @ {ev['price']:.6g} pnl {ev['pnl']:+.2f} (bar {ev['bar_time']})")
    if not state["positions"]:
        print("POSITIONS none")
    for s, p in state["positions"].items():
        mark = marks[s] * p["basis"]
        print(f"POSITION {s} {NAME[p['side']]} {p['leverage']}x margin {p['margin']:.2f} entry {p['entry']:.6g} mark~{mark:.6g} "
              f"uPnL {unrealized(p, mark):+.2f} stop {p['stop']:.6g} take {p.get('take')} liq {liquidation_price(p, cs[s]):.6g} "
              f"opened {iso(p['opened_ts'])}")
    print("MARKET (Binance spot; fund = Walt funding %/h, + means longs pay; volx = last hour volume / 7d avg; "
          "flow4 = taker buy-sell imbalance over 4h; pos24 = place in 24h range, 0 low .. 100 high)")
    print(f"{'sym':<6}{'price':>11}{'r1h%':>7}{'r4h%':>7}{'r24h%':>7}{'r7d%':>7}{'rsi':>5}{'vol1h%':>7}{'pos24':>6}{'volx':>6}{'flow4':>7}{'fund':>9}{'maxlev':>7}")
    for r in sorted(rows, key=lambda r: -r["r24h"]):
        print(f"{r['symbol']:<6}{r['price']:>11.6g}{r['r1h']:>7.2f}{r['r4h']:>7.2f}{r['r24h']:>7.2f}{r['r7d']:>7.1f}{r['rsi']:>5.0f}"
              f"{r['vol1h']:>7.2f}{r['pos24']:>6.0f}{r['volx']:>6.1f}{r['flow4']:>7.2f}{r['fund']:>9.4f}{r['maxlev']:>7}")


# --- decisions -----------------------------------------------------------
def decide(path: str) -> None:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    state, cs = load_state(), contracts()
    if state["decisions"] >= DECISIONS_PLANNED and d.get("orders"):
        closing_only = all(o["action"] == "close" for o in d["orders"])
        if not closing_only:
            raise SystemExit("All planned decisions are used; only closing orders are accepted now.")
    for key in ("observations", "reasoning"):
        if not str(d.get(key, "")).strip():
            raise SystemExit(f"decision needs '{key}'")
    fills = []
    for o in d.get("orders", []):
        s, c = o["symbol"], cs[o["symbol"]]
        rate = float(o["walt_rate"])
        if s not in UNIVERSE:
            raise SystemExit(f"{s} is outside the experiment universe")
        if o["action"] == "close":
            if s not in state["positions"]:
                raise SystemExit(f"no open position in {s}")
            p = state["positions"][s]
            fills.append(close_position(state, s, rate * (1 - p["side"] * SLIPPAGE), "close", c))
            continue
        if o["action"] != "open":
            raise SystemExit("action must be open or close")
        if s in state["positions"]:
            raise SystemExit(f"{s} already has a position (one per contract)")
        side, lev, margin = SIDE[o["side"]], int(o["leverage"]), float(o["margin"])
        if not 1 <= lev <= min(MAX_LEVERAGE, c.max_leverage):
            raise SystemExit(f"leverage must be 1..{min(MAX_LEVERAGE, c.max_leverage)}")
        used = sum(p["margin"] + p["entry_fee"] for p in state["positions"].values())
        if margin < c.min_size_quote or used + margin > state["cash"] + 1e-9:
            raise SystemExit(f"margin {margin} not available (cash {state['cash']:.2f}, in use {used:.2f})")
        notional = margin / (1 / lev + c.taker_fee_pct)
        scale = 10**c.size_decimals
        size = math.floor(notional / (rate * (1 + side * c.price_slippage)) * scale) / scale
        if size < c.min_size_base:
            raise SystemExit("size below the contract minimum")
        fill = rate * (1 + side * SLIPPAGE)
        fee = size * fill * c.taker_fee_pct
        stop = float(o["stop"])
        pos = {"side": side, "size": size, "entry": fill, "margin": margin - fee, "entry_fee": fee, "leverage": lev,
               "stop": stop, "take": float(o["take"]) if o.get("take") else None, "opened_ts": now_ms(),
               "checked_ts": now_ms() // 300_000 * 300_000, "funding_paid": 0.0}
        if (stop - fill) * side >= 0:
            raise SystemExit("stop must be on the losing side of the entry")
        if (stop - liquidation_price(pos, c)) * side <= 0:
            raise SystemExit("stop must be closer than the liquidation price")
        spot = _download(f"{s}USDT", "1m", now_ms() - 180_000, now_ms())[-1].c
        pos["basis"] = rate / spot
        if abs(pos["basis"] - 1) > 0.02:
            raise SystemExit(f"Walt rate {rate} and Binance {spot} differ by more than 2 %: check the rate")
        state["cash"] -= fee
        state["fees"] += fee
        state["positions"][s] = pos
        fills.append({"symbol": s, "kind": "open", "side": o["side"], "price": fill, "size": size, "leverage": lev,
                      "margin": margin, "fee": round(fee, 4), "stop": stop, "take": pos["take"], "walt_rate": rate,
                      "liquidation": round(liquidation_price(pos, c), 6)})
    final = state["decisions"] >= DECISIONS_PLANNED  # the closing step after the last decision
    if not final:
        state["decisions"] += 1
    entry = append(state, {"type": "decision", "decision": "final close" if final else state["decisions"],
                           "observations": d["observations"],
                           "reasoning": d["reasoning"], "orders": d.get("orders", []), "fills": fills})
    save_state(state)
    write_report()
    print(f"decision {entry['decision']} of {DECISIONS_PLANNED} recorded, entry {entry['n']} hash {entry['hash'][:12]}")
    for f in fills:
        print("FILL", json.dumps(f))


def start(contracts_path: str) -> None:
    if STATE.exists():
        raise SystemExit("Already started; delete data/llm to start over.")
    DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(contracts_path, CONTRACTS)
    state = {"start_ts": now_ms(), "cash": BUDGET, "fees": 0.0, "funding": 0.0, "positions": {}, "decisions": 0,
             "entries": 0, "last_hash": "genesis"}
    append(state, {"type": "start", "budget": BUDGET, "max_leverage": MAX_LEVERAGE, "slippage": SLIPPAGE,
                   "decisions_planned": DECISIONS_PLANNED, "universe": UNIVERSE, "model": "Claude Fable 5.2"})
    save_state(state)
    write_report()
    print("started", iso(state["start_ts"]))


# --- report --------------------------------------------------------------
def write_report() -> None:
    log, state = read_log(), load_state() if STATE.exists() else None
    if not log or state is None:
        return
    marks = [e for e in log if e["type"] == "mark"]
    decisions = [e for e in log if e["type"] == "decision"]
    events = [e for e in log if e["type"] == "event"]
    eq = marks[-1]["equity"] if marks else BUDGET
    opens = [f for d in decisions for f in d["fills"] if f["kind"] == "open"]
    exits = [f for d in decisions for f in d["fills"] if f["kind"] != "open"] + events
    done = state["decisions"] >= DECISIONS_PLANNED and not state["positions"]
    L = []
    a = L.append
    a("# A language model as a discretionary perpetual-futures trader: a 24-hour simulated experiment")
    a("")
    a(f"**Status: {'FINAL' if done else 'IN PROGRESS'}.** Last update {log[-1]['time']}. "
      f"Decisions made: {state['decisions']} of {DECISIONS_PLANNED}.")
    a("")
    a("**Simulated trading. No real orders were placed and no money was at risk.**")
    a("")
    a("## Summary")
    a("")
    a(f"- Account: {eq:.2f} USD from {BUDGET:.0f} USD ({(eq / BUDGET - 1) * 100:+.2f} %).")
    a(f"- Positions opened: {len(opens)}; closed: {len(exits)} (of which stop-loss {sum(e['kind'] == 'stop' for e in exits)}, "
      f"liquidation {sum(e['kind'] == 'liquidation' for e in exits)}). Open now: {len(state['positions'])}.")
    a(f"- Fees paid: {state['fees']:.2f} USD. Funding paid on closed positions: {state['funding']:+.2f} USD.")
    if len(marks) > 1 and marks[0].get("btc") and marks[-1].get("btc"):
        a(f"- Bitcoin over the same period: {(marks[-1]['btc'] / marks[0]['btc'] - 1) * 100:+.2f} %. Holding cash: 0.00 %.")
    a(f"- Log integrity (hash chain): {'intact' if verify_chain() else 'BROKEN'}; {len(log)} entries.")
    a("")
    a("## 1. Question")
    a("")
    a("Can a large language model, deciding once an hour from market data, trade leveraged crypto perpetual "
      "futures profitably after costs? Prior rule-based research on the same venue (about 520 configurations, "
      "see `data/research/`) found no rule that survived held-out data, so the working hypothesis is **no**: "
      "the expected result is a loss of roughly the trading costs incurred.")
    a("")
    a("## 2. Protocol (fixed before the first decision)")
    a("")
    a(f"- **Decision-maker:** {log[0].get('model', 'Claude Fable 5.2')}, one decision per hour, {DECISIONS_PLANNED} decisions. "
      "Staying flat or holding is an allowed decision.")
    a(f"- **Account:** {BUDGET:.0f} USD simulated. Leverage at most {MAX_LEVERAGE}x. One position per contract. "
      "Every position must carry a stop-loss closer than its liquidation price.")
    a(f"- **Universe:** {len(UNIVERSE)} crypto contracts listed on Walt Perps that also trade on Binance spot: {', '.join(UNIVERSE)}.")
    a("- **Information available to the model each hour:** a table of price, 1h/4h/24h/7d returns, RSI, volatility, "
      "position in the 24-hour range, relative volume, taker order-flow imbalance and Walt's funding rate for each contract; "
      "the account state; and its own earlier log entries. No news feed.")
    a("- **Order of events:** the model writes its observations and reasoning, then the orders; the entry is hashed and "
      "appended to the log; only then are fills computed. Outcomes are never known when a decision is recorded.")
    a(f"- **Fills:** at Walt's quoted rate at decision time (read through Walt's API), moved {SLIPPAGE:.2%} against the trade. "
      "Sizing by Walt's formula. Taker fee 0.09 % on entry and exit. Funding accrues at the contract's rate from the latest Walt snapshot.")
    a("- **Between decisions:** stops, take-profits and liquidation are checked against Binance 5-minute highs and lows, "
      "scaled by the Walt/Binance price ratio at entry. If one bar touches both stop and take-profit, the stop is assumed first.")
    a("- **End:** after the last decision any open position is closed at the next hourly check.")
    a("")
    a("## 3. Equity by check")
    a("")
    a("| Time | Equity (USD) | Return | BTC price | Positions |")
    a("| --- | --- | --- | --- | --- |")
    for m in marks:
        held = ", ".join(f"{s} {side}" for s, side in m["positions"].items()) or "flat"
        a(f"| {m['time']} | {m['equity']:.2f} | {(m['equity'] / BUDGET - 1) * 100:+.2f} % | {m.get('btc') or ''} | {held} |")
    a("")
    a("## 4. Decision log")
    for d in decisions:
        a("")
        a(f"### Decision {d['decision']} at {d['time']}")
        a("")
        a(f"**Observations.** {d['observations']}")
        a("")
        a(f"**Reasoning.** {d['reasoning']}")
        a("")
        if not d["fills"]:
            a("**Action.** None (no order).")
        for f in d["fills"]:
            if f["kind"] == "open":
                a(f"**Action.** Open {f['side']} {f['symbol']}: {f['leverage']}x, margin {f['margin']:.2f} USD, fill {f['price']:.6g}, "
                  f"size {f['size']:g}, stop {f['stop']:.6g}, take-profit {f['take'] or 'none'}, liquidation {f['liquidation']:.6g}, fee {f['fee']:.4f}.")
            else:
                a(f"**Action.** Close {f['side']} {f['symbol']} at {f['price']:.6g}: result {f['pnl']:+.2f} USD after the exit fee.")
        a("")
        a(f"Log entry {d['n']}, hash `{d['hash'][:16]}`.")
    a("")
    a("## 5. Automatic exits")
    a("")
    if events:
        a("| Time | Contract | Exit | Side | Price | Result (USD) |")
        a("| --- | --- | --- | --- | --- | --- |")
        for e in events:
            a(f"| {e['time']} | {e['symbol']} | {e['kind']} | {e['side']} | {e['price']:.6g} | {e['pnl']:+.2f} |")
    else:
        a("None so far.")
    a("")
    a("## 6. Limitations")
    a("")
    a("- **Simulated fills.** No order reached Walt; real slippage, rejections and outages are not measured.")
    a("- **One run, 24 decisions.** A single day cannot separate skill from luck. A positive or negative result here says little about what a second day would bring.")
    a("- **Stop checks use Binance spot** scaled to Walt's price, not Walt's own intrahour prices.")
    a("- **Funding uses a snapshot rate** refreshed at most hourly; Walt's real funding moves within the hour.")
    a("- **No news or order-book data.** The model sees only the table described in the protocol.")
    a("- **Crypto contracts only.** Walt's stock, FX and commodity contracts are excluded because intrahour data for them was not available.")
    a("- **The decision-maker also wrote the ledger code.** The hash chain shows the log was not edited afterwards; it does not prove the code is free of errors. The code is in `waltbot/llm_experiment.py`.")
    a("")
    if CONCLUSION.exists():
        a("## 7. Conclusion")
        a("")
        a(CONCLUSION.read_text(encoding="utf-8").strip())
        a("")
    REPORT.write_text("\n".join(L), encoding="utf-8")
