"""Paper trading: no orders are sent anywhere.

Each run replays the simulator from the paper start time over fresh candles, logs the
events that are new since the last run, and prints an order ticket when the strategy
wants a change at the next bar. The ticket lists the fields Walt's order form needs, so
the owner can decide whether to place it by hand.
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone

from .backtest import load_candles, make_simulator
from .contracts import ROOT, get_contract
from .data import INTERVAL_MS

STATE = ROOT / "data" / "paper_state.json"
LOG = ROOT / "data" / "paper_log.csv"
SIDE = {1: "long", -1: "short", 0: "flat"}


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def run_paper(cfg: dict, reset: bool = False) -> dict:
    contract = get_contract(cfg["symbol"])
    step = INTERVAL_MS[cfg["interval"]]
    key = json.dumps(cfg, sort_keys=True)
    state = {}
    if STATE.exists() and not reset:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        if state.get("config") != key:
            raise RuntimeError("Config changed since paper trading started; rerun with --reset")
    if not state:
        state = {"config": key, "start_ts": int(time.time() * 1000), "seen": 0}
        if LOG.exists():
            LOG.unlink()

    sim = make_simulator(cfg, contract, trade_from_ts=state["start_ts"])
    warmup_days = (sim.strategy.warmup + 5) * step / 86_400_000
    age_days = (time.time() * 1000 - state["start_ts"]) / 86_400_000
    candles = load_candles(cfg, contract, days=warmup_days + age_days + 1)
    res = sim.run(candles)

    new = res.events[state["seen"] :]
    if new:
        first = not LOG.exists()
        with open(LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if first:
                w.writerow(["time_utc", "event", "side", "price", "size", "margin", "leverage", "pnl", "fee", "note"])
            for e in new:
                w.writerow([_iso(e.ts), e.kind, SIDE[e.side], e.price, e.size, round(e.margin, 4),
                            e.leverage, round(e.pnl, 4), round(e.fee, 4), e.note])
    state["seen"] = len(res.events)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state), encoding="utf-8")

    have = res.position.side if res.position else 0
    last = candles[-1]
    report = {
        "as_of_bar_close": _iso(last.ts + step),
        "last_close": last.c,
        "paper_equity": round(res.curve[-1][1], 2),
        "position": SIDE[have],
        "new_events": [f"{_iso(e.ts)} {e.kind} {SIDE[e.side]} @ {e.price:.6g} pnl {e.pnl:.2f} {e.note}".strip() for e in new],
        "halted": sim.risk.halted,
        "ticket": None,
    }
    if res.position:
        report["liquidation_price"] = round(sim.liquidation_price(res.position), 6)
        report["stop"] = res.position.stop
    if sim.pending != have:
        report["ticket"] = _ticket(sim, contract, have, last.c)
    return report


def _ticket(sim, contract, have: int, price: float) -> dict:
    """What the strategy would do at the next bar's open, in Walt's order terms."""
    ticket: dict = {"market": contract.market_id, "contractId": contract.contract_id}
    if have != 0:
        ticket["close_position"] = SIDE[have]
    if sim.pending != 0 and sim.pending != sim.blocked:
        margin, reason = sim.risk.margin_for_entry(sim.res.equity)
        if margin <= 0:
            ticket["open"] = f"blocked: {reason}"
        else:
            rc = sim.risk.cfg
            side = sim.pending
            ticket["open"] = {
                "side": SIDE[side],
                "type": "market",
                "leverage": rc.leverage,
                "amount_margin_usdc": round(margin, 2),
                "reference_price": price,
                "stopLoss": round(price * (1 - side * rc.stop_loss_pct), 6) if rc.stop_loss_pct else None,
                "takeProfit": round(price * (1 + side * rc.take_profit_pct), 6) if rc.take_profit_pct else None,
                "note": "reference price is Binance spot; check Walt's own rate before placing",
            }
    return ticket
