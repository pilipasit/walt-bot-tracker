"""Forward paper test of the funding-carry basket on Walt's own funding rates. Sends no orders.

Long the K contracts with the lowest average Walt funding, short the K with the highest,
equal size, 100 USD of total exposure. Updated on every hourly snapshot: funding accrues at
the rates in that snapshot, prices come from Binance spot. Re-ranked once a day.

This measures the one thing the Binance backtest could not: what Walt's funding actually
adds up to over time, and what the price side of the basket does meanwhile.
"""
from __future__ import annotations

import json
import time

from .contracts import ROOT, load_contracts
from .data import _download
from .funding_log import _rows
from .llm_experiment import UNIVERSE

DIR = ROOT / "data" / "carry"
STATE = DIR / "state.json"
REPORT = ROOT / "CARRY_PAPER.md"
K, GROSS, COST = 4, 100.0, 0.0014  # positions per side, USD exposure, cost per unit traded
HOUR, DAY = 3_600_000, 86_400_000


def _price(symbol: str) -> float:
    now = int(time.time() * 1000)
    return _download(f"{symbol}USDT", "1m", now - 180_000, now)[-1].c


def _ranking(since_ms: int) -> list[tuple[str, float]]:
    """Mean Walt funding per contract over snapshots since `since_ms`, lowest first."""
    acc: dict[str, list[float]] = {}
    for r in _rows():
        if int(r["snapshot_ms"]) >= since_ms and r["asset"] in UNIVERSE:
            acc.setdefault(r["asset"], []).append(float(r["funding_rate_hourly"]))
    return sorted(((s, sum(v) / len(v)) for s, v in acc.items()), key=lambda x: x[1])


def _rebalance(state: dict, now: int) -> None:
    ranked = _ranking(now - DAY)
    if len(ranked) < 2 * K + 1:
        return
    target = {s: 1 for s, _ in ranked[:K]} | {s: -1 for s, _ in ranked[-K:]}
    per = GROSS / (2 * K)
    for s in list(state["positions"]):
        p = state["positions"][s]
        if target.get(s) != p["side"]:
            px = _price(s)
            state["price_pnl"] += p["side"] * (px - p["entry"]) * p["size"]
            state["fees"] += p["size"] * px * COST
            del state["positions"][s]
    for s, side in target.items():
        if s not in state["positions"]:
            px = _price(s)
            state["positions"][s] = {"side": side, "size": per / px, "entry": px}
            state["fees"] += per * COST
    state["last_rebalance"] = now
    state["rebalances"].append({"ts": now, "long": [s for s, d in target.items() if d > 0],
                                "short": [s for s, d in target.items() if d < 0],
                                "funding_used": {s: round(f * 100, 4) for s, f in ranked}})


def update(contracts_path: str) -> dict:
    """Accrue funding for the time since the last update, mark prices, rebalance daily."""
    now = int(time.time() * 1000)
    DIR.mkdir(parents=True, exist_ok=True)
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
    else:
        state = {"start": now, "last": now, "last_rebalance": 0, "positions": {}, "price_pnl": 0.0,
                 "funding_pnl": 0.0, "fees": 0.0, "history": [], "rebalances": []}
    rates = {s: c.funding_rate_hourly for s, c in load_contracts(contracts_path).items()}
    hours = (now - state["last"]) / HOUR
    open_pnl = 0.0
    for s, p in state["positions"].items():
        px = _price(s)
        state["funding_pnl"] += -p["side"] * rates.get(s, 0.0) * p["size"] * px * hours  # + funding: longs pay
        open_pnl += p["side"] * (px - p["entry"]) * p["size"]
    state["last"] = now
    if now - state["last_rebalance"] >= DAY:
        _rebalance(state, now)
    equity = GROSS + state["price_pnl"] + open_pnl + state["funding_pnl"] - state["fees"]
    state["history"].append({"ts": now, "equity": round(equity, 4), "price": round(state["price_pnl"] + open_pnl, 4),
                             "funding": round(state["funding_pnl"], 4), "fees": round(state["fees"], 4)})
    STATE.write_text(json.dumps(state), encoding="utf-8")
    _report(state)
    return state["history"][-1]


def _report(state: dict) -> None:
    iso = lambda ts: time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts / 1000))  # noqa: E731
    h = state["history"][-1]
    hours = (h["ts"] - state["start"]) / HOUR
    L = ["# Funding-carry basket on Walt: forward paper test", "",
         "**Simulated. No real orders were placed.** Updated on every hourly snapshot.", "",
         f"- Running for {hours:.1f} hours since {iso(state['start'])}.",
         f"- Basket value: {h['equity']:.2f} USD from {GROSS:.0f} USD of exposure ({h['equity'] - GROSS:+.2f}).",
         f"- Of which: funding {h['funding']:+.3f}, price {h['price']:+.3f}, trading costs -{h['fees']:.3f}.",
         "- Rule: long the 4 contracts with the lowest average Walt funding over the last 24 hours, short the 4 "
         "with the highest, equal size, re-ranked once a day. Prices from Binance spot; cost 0.14 % per unit traded.",
         "- A few hours or days of this says little: funding accrues slowly and the price side is noisy.", "",
         "## Baskets", ""]
    for r in state["rebalances"]:
        L.append(f"- {iso(r['ts'])}: long {', '.join(r['long'])}; short {', '.join(r['short'])}")
    L += ["", "## History", "", "| Time | Value | Funding | Price | Costs |", "| --- | --- | --- | --- | --- |"]
    for x in state["history"]:
        L.append(f"| {iso(x['ts'])} | {x['equity']:.2f} | {x['funding']:+.3f} | {x['price']:+.3f} | -{x['fees']:.3f} |")
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
