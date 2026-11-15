"""Live trader for Walt Perps. Places real orders only when started by the owner with --live.

Without --live every cycle is a dry run: it reads the real account and prints what it would
do, and calls no prepare or execute tool.

Hard limits (constants below, not configurable):
  * leverage never above HARD_MAX_LEVERAGE;
  * total margin committed never above HARD_MAX_BUDGET USDC;
  * every entry carries a stop-loss; an order without one is never sent;
  * a failed or uncertain order is never retried within a cycle; state is re-read instead.

Decisions are made once per closed bar. Between bars the trader only watches the kill switch
and notices positions that Walt closed (stop-loss or liquidation).
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import strategies
from .contracts import ROOT, Contract, get_contract
from .data import INTERVAL_MS, _download, data_symbol, get_candles

HARD_MAX_LEVERAGE = 5
HARD_MAX_BUDGET = 100.0
MAX_PRICE_GAP = 0.015  # skip an entry if Walt's rate and the data feed disagree by more
MAX_ATTEMPTS_PER_BAR = 2
SIDE = {1: "long", -1: "short"}
QUOTE = ("USDT", "USDC")

DIR = ROOT / "data" / "live"


class UnknownShape(RuntimeError):
    """A Walt response did not have the fields this code expects. Nothing is traded on it."""


def position_side(p: dict) -> int:
    for key in ("side", "direction", "position_side"):
        v = str(p.get(key, "")).lower()
        if v in ("long", "buy"):
            return 1
        if v in ("short", "sell"):
            return -1
    if isinstance(p.get("is_long"), bool):
        return 1 if p["is_long"] else -1
    raise UnknownShape(f"cannot read side from position: {json.dumps(p)[:400]}")


def _f(d: dict, key: str, default: float | None = None) -> float:
    try:
        return float(d[key])
    except (KeyError, TypeError, ValueError):
        if default is None:
            raise UnknownShape(f"missing number '{key}' in {json.dumps(d)[:400]}")
        return default


def market_signal(symbol: str, cfg: dict) -> tuple[int, int, float]:
    """(target, open time of the last closed bar, its close) from the data feed."""
    strat = strategies.build(cfg["strategy"]["name"], cfg["strategy"].get("params"))
    step = INTERVAL_MS[cfg["interval"]]
    candles = get_candles(data_symbol(symbol), cfg["interval"], (strat.warmup + 10) * step / 86_400_000 + 1)
    if len(candles) < strat.warmup:
        raise RuntimeError(f"not enough history for {symbol}")
    target = 0
    for i in range(len(candles)):  # replay so stateful rules reach their current state
        target = strat.target(candles, i)
    # The price used to sanity-check Walt's rate must be current. The last closed bar's close
    # can be hours old, which made the check reject entries after any sharp move.
    now = int(time.time() * 1000)
    try:
        live_price = _download(data_symbol(symbol), "1m", now - 180_000, now)[-1].c
    except (OSError, IndexError):
        live_price = candles[-1].c
    return target, candles[-1].ts, live_price


class Trader:
    def __init__(self, client, cfg: dict, live: bool, state_dir: Path = DIR, signal_fn=market_signal,
                 now_fn=lambda: int(time.time() * 1000), settle_seconds: float = 3.0):
        self.client, self.cfg, self.live = client, cfg, live
        self.dir, self.signal_fn, self.now, self.settle = state_dir, signal_fn, now_fn, settle_seconds
        self.leverage = int(cfg["leverage"])
        self.margin = float(cfg["margin_per_position"])
        self.budget = float(cfg["budget"])
        self.stop_pct = float(cfg["stop_loss_pct"])
        self.contracts: dict[str, Contract] = {s: get_contract(s) for s in cfg["symbols"]}
        if not 1 <= self.leverage <= HARD_MAX_LEVERAGE:
            raise ValueError(f"leverage must be 1..{HARD_MAX_LEVERAGE}")
        if self.budget > HARD_MAX_BUDGET or self.margin * len(self.contracts) > self.budget:
            raise ValueError(f"budget must be <= {HARD_MAX_BUDGET} and cover margin_per_position x symbols")
        if not 0 < self.stop_pct < 1 / self.leverage:
            raise ValueError("stop_loss_pct must be set and closer than the liquidation distance")
        for s, c in self.contracts.items():
            if self.leverage > c.max_leverage or self.margin < c.min_size_quote:
                raise ValueError(f"{s}: leverage or margin outside the contract's limits")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {
            "start_ts": self.now(), "halted": None, "symbols": {}}

    # --- bookkeeping ----------------------------------------------------
    def log(self, event: str, **details) -> None:
        row = {"time": datetime.fromtimestamp(self.now() / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
               "mode": "LIVE" if self.live else "dry-run", "event": event, **details}
        with open(self.dir / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)

    def save(self) -> None:
        if self.live:  # a dry run must not change what the live trader believes
            self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def sym_state(self, symbol: str) -> dict:
        return self.state["symbols"].setdefault(
            symbol, {"last_bar": 0, "held": 0, "blocked": 0, "attempts": 0, "attempt_bar": 0})

    # --- account reads --------------------------------------------------
    async def positions(self) -> dict[int, dict]:
        raw = await self.client.call("perps_list_positions", {"statuses": ["open"]})
        if "positions" not in raw:
            raise UnknownShape(f"no 'positions' in {json.dumps(raw)[:400]}")
        return {int(p["contract_id"]): p for p in raw["positions"]}

    async def available(self) -> float:
        raw = await self.client.call("balance_get")
        if "accounts" not in raw:
            raise UnknownShape(f"no 'accounts' in {json.dumps(raw)[:400]}")
        return sum(_f(a, "balance_available") for a in raw["accounts"] if a.get("currency") in QUOTE)

    async def rate(self, contract: Contract) -> float:
        return _f(await self.client.call("perps_latest_rate", {"marketId": contract.market_id}), "rate")

    async def account_value(self, positions: dict[int, dict], available: float) -> float:
        """Available balance + margin in positions + their unrealized result where it can be read."""
        value = available
        by_id = {c.contract_id: c for c in self.contracts.values()}
        for cid, p in positions.items():
            value += _f(p, "margin_quote", 0.0)
            if cid in by_id and "avg_entry_price" in p and "value_base" in p:
                try:
                    side = position_side(p)
                    value += side * (await self.rate(by_id[cid]) - _f(p, "avg_entry_price")) * abs(_f(p, "value_base"))
                    value -= _f(p, "funding_paid_quote", 0.0)
                except UnknownShape:
                    pass
        return value

    # --- writes ---------------------------------------------------------
    async def _execute(self, preview: dict, what: str, symbol: str) -> bool:
        if not preview.get("ok") or not preview.get("confirmationToken"):
            self.log("preview_rejected", symbol=symbol, action=what, preview=preview)
            return False
        try:
            result = await self.client.call("perps_execute_operation", {"confirmationToken": preview["confirmationToken"]})
            self.log("executed", symbol=symbol, action=what, result=result)
            return True
        except Exception as e:  # outcome unknown: the caller re-reads positions, never retries
            self.log("execute_error", symbol=symbol, action=what, error=str(e)[:500])
            return False

    async def close(self, symbol: str, reason: str) -> bool:
        c = self.contracts[symbol]
        self.log("close", symbol=symbol, reason=reason)
        if not self.live:
            return True
        try:
            preview = await self.client.call("perps_prepare_position_close", {"contractId": c.contract_id})
        except Exception as e:
            self.log("preview_error", symbol=symbol, action="close", error=str(e)[:500])
            return False
        await self._execute(preview, "close", symbol)
        for _ in range(5):
            await asyncio.sleep(self.settle)
            if c.contract_id not in await self.positions():
                self.sym_state(symbol)["held"] = 0
                return True
        self.log("close_not_confirmed", symbol=symbol)
        return False

    async def open(self, symbol: str, side: int, feed_price: float, committed: float, available: float) -> None:
        c = self.contracts[symbol]
        if committed + self.margin > min(self.budget, HARD_MAX_BUDGET) + 1e-9:
            return self.log("skip_open", symbol=symbol, reason="budget cap", committed=committed)
        if available < self.margin:
            return self.log("skip_open", symbol=symbol, reason="not enough available balance", available=available)
        rate = await self.rate(c)
        if abs(rate / feed_price - 1) > MAX_PRICE_GAP:
            return self.log("skip_open", symbol=symbol, reason="Walt rate and data feed disagree", rate=rate, feed=feed_price)
        stop = round(rate * (1 - side * self.stop_pct), c.price_decimals)
        order = {"contractId": c.contract_id, "side": SIDE[side], "type": "market", "leverage": self.leverage,
                 "amount": f"{self.margin:.2f}", "price": f"{rate:.{c.price_decimals}f}",
                 "autoClose": {"stopLoss": {"priceQuote": f"{stop:.{c.price_decimals}f}"}}}
        self.log("open", symbol=symbol, order=order)
        if not self.live:
            return
        try:
            preview = await self.client.call("perps_prepare_order_place", order)
        except Exception as e:
            return self.log("preview_error", symbol=symbol, action="open", error=str(e)[:500])
        if not preview.get("ok") or not preview.get("confirmationToken"):
            return self.log("preview_rejected", symbol=symbol, action="open", preview=preview)
        await self._execute(preview, "open", symbol)
        await asyncio.sleep(self.settle)
        p = (await self.positions()).get(c.contract_id)
        if p is not None:
            self.sym_state(symbol)["held"] = position_side(p)
            self.log("position_confirmed", symbol=symbol, position=p)
        else:
            self.log("position_not_visible_yet", symbol=symbol)
            self.sym_state(symbol)["pending_side"] = side

    # --- one cycle ------------------------------------------------------
    async def halt(self, reason: str, positions: dict[int, dict]) -> None:
        self.log("halt", reason=reason)
        for s, c in self.contracts.items():
            if c.contract_id in positions and self.sym_state(s)["held"] != 0:
                await self.close(s, reason)
        self.state["halted"] = reason
        self.save()

    async def cycle(self) -> None:
        if self.state["halted"]:
            return self.log("halted", reason=self.state["halted"])
        positions = await self.positions()
        available = await self.available()
        value = await self.account_value(positions, available)
        self.log("account", available=available, value=round(value, 4), open_positions=len(positions))
        if self.now() >= self.cfg["end_ts"]:
            return await self.halt("experiment end time reached", positions)
        if value <= self.budget * (1 - float(self.cfg["max_drawdown_pct"])):
            return await self.halt(f"kill switch: account value {value:.2f}", positions)

        for symbol, c in self.contracts.items():
            st = self.sym_state(symbol)
            try:
                actual = position_side(positions[c.contract_id]) if c.contract_id in positions else 0
            except UnknownShape as e:
                self.log("unknown_position_shape", symbol=symbol, error=str(e))
                continue
            if st.get("pending_side") and actual:
                st["held"] = actual
            st.pop("pending_side", None)
            if st["held"] != 0 and actual == 0:  # Walt closed it: stop-loss or liquidation
                self.log("position_gone", symbol=symbol, was=SIDE[st["held"]])
                st["blocked"], st["held"] = st["held"], 0
            if actual != 0 and st["held"] == 0:
                self.log("foreign_position_left_alone", symbol=symbol)
                continue
            try:
                target, bar_ts, feed_price = self.signal_fn(symbol, self.cfg)
            except Exception as e:
                self.log("signal_error", symbol=symbol, error=str(e)[:300])
                continue
            if bar_ts == st["last_bar"]:
                continue  # this bar's decision is already carried out
            if st.get("attempt_bar") != bar_ts:
                st["attempt_bar"], st["attempts"] = bar_ts, 0
            if target != st["blocked"]:
                st["blocked"] = 0  # the signal left the stopped-out side; re-entry is allowed again
            want = 0 if target == st["blocked"] else target
            if want == actual:
                st["last_bar"] = bar_ts
                continue
            if st["attempts"] >= MAX_ATTEMPTS_PER_BAR:
                self.log("giving_up_on_bar", symbol=symbol, bar=bar_ts)
                st["last_bar"] = bar_ts
                continue
            st["attempts"] += 1
            self.log("decision", symbol=symbol, target=target, want=want, actual=actual, bar=bar_ts)
            if actual != 0:
                if not await self.close(symbol, "signal changed"):
                    continue
                if self.live:
                    positions = await self.positions()
                    available = await self.available()
                else:
                    positions = {k: v for k, v in positions.items() if k != c.contract_id}
            if want != 0:
                committed = sum(_f(p, "margin_quote", self.margin) for p in positions.values())
                await self.open(symbol, want, feed_price, committed, available)
                if self.live:
                    positions = await self.positions()
                    available = await self.available()
            if not self.live or st["held"] == want or st.get("pending_side"):
                st["last_bar"] = bar_ts  # done for this bar
        self.save()
        try:  # refresh the data file behind dashboard.html
            from .dashboard import write_data

            write_data()
        except Exception:
            pass


async def run(cfg: dict, live: bool, loop: bool, connect) -> None:
    """Run one cycle, or keep cycling every `poll_minutes` until halted."""
    while True:
        try:
            async with connect() as client:
                trader = Trader(client, cfg, live)
                await trader.cycle()
                if trader.state["halted"]:
                    return
        except Exception as e:
            print(json.dumps({"event": "cycle_error", "error": str(e)[:500]}), flush=True)
            if not loop:
                raise
        if not loop:
            return
        await asyncio.sleep(float(cfg.get("poll_minutes", 10)) * 60)
