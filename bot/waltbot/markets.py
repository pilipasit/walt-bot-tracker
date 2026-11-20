"""Market analysis feed for the tracker's front page (`index.html`): short-term trends of Walt's main assets.

Every cloud cycle (every 10 minutes) this:
  1. samples Walt's own price for each asset in UNIVERSE (`perps_latest_rate`, the price the owner trades at) and
     keeps 7 days of samples in `data/market/samples.json`;
  2. for crypto (and gold through PAXG) also reads Binance 1-hour candles, so those assets have a week of history
     from the first run — the others build theirs from Walt's samples (about 12 hours before a trend shows);
  3. writes `data/market/markets.json`: per asset the trend, its strength and cleanness, volatility, and the
     contract facts the page needs (max leverage, close-out margin, funding).

The page turns that into leverage scenarios with rule-based stop and take-profit levels. Analysis only: nothing
here places or suggests an order to the bot, and nothing here is financial advice.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from .contracts import ROOT, load_contracts

OUT = ROOT / "data" / "market"
SAMPLES = OUT / "samples.json"
KEEP_MS = 7 * 86_400_000
HOUR = 3_600_000

# (symbol on Walt, group, Binance symbol for 1h backfill or None). "Main assets" across Walt's catalogue.
UNIVERSE = [
    ("BTC", "Crypto", "BTCUSDT"), ("ETH", "Crypto", "ETHUSDT"), ("SOL", "Crypto", "SOLUSDT"),
    ("XRP", "Crypto", "XRPUSDT"), ("BNB", "Crypto", "BNBUSDT"), ("DOGE", "Crypto", "DOGEUSDT"),
    ("HYPE", "Crypto", None), ("LINK", "Crypto", "LINKUSDT"),
    ("XAU", "Metals", "PAXGUSDT"), ("XAG", "Metals", None), ("XPT", "Metals", None), ("XCU", "Metals", None),
    ("WTI", "Energy", None), ("BRENTOIL", "Energy", None), ("NATGAS", "Energy", None),
    ("SPY", "Indices", None), ("US100", "Indices", None), ("US10Y", "Indices", None),
    ("NVDA", "Stocks", None), ("TSLA", "Stocks", None), ("AAPL", "Stocks", None), ("MSFT", "Stocks", None),
    ("AMZN", "Stocks", None), ("GOOGL", "Stocks", None), ("META", "Stocks", None), ("AMD", "Stocks", None),
    ("MSTR", "Stocks", None), ("COIN", "Stocks", None), ("PLTR", "Stocks", None), ("TSM", "Stocks", None),
    ("EURUSD", "FX", None), ("GBPUSD", "FX", None), ("USDJPY", "FX", None),
]
NAMES = {"XAU": "Gold", "XAG": "Silver", "XPT": "Platinum", "XCU": "Copper", "WTI": "WTI crude",
         "BRENTOIL": "Brent crude", "NATGAS": "Natural gas", "SPY": "S&P 500 (SPY)", "US100": "Nasdaq 100",
         "US10Y": "US 10-year yield", "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY"}


def _load() -> dict[str, list[list[float]]]:
    try:
        return json.loads(SAMPLES.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — first run or a damaged file: start over
        return {}


async def sample(client) -> int:
    """Append Walt's current price for every asset Walt lists. Returns how many were read."""
    contracts = load_contracts()
    store, now, n = _load(), int(time.time() * 1000), 0
    for sym, _, _ in UNIVERSE:
        if sym not in contracts:
            continue
        try:
            raw = await client.call("perps_latest_rate", {"marketId": f"{sym}-USD-PERP"})
            px = float(raw.get("rate"))
        except Exception:  # noqa: BLE001 — one asset failing must never stop the others (or the bot)
            continue
        if px > 0:
            store.setdefault(sym, []).append([now, px])
            n += 1
    for sym in store:
        store[sym] = [s for s in store[sym] if s[0] > now - KEEP_MS]
    OUT.mkdir(parents=True, exist_ok=True)
    SAMPLES.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")
    return n


def _hourly_from_samples(samples: list[list[float]]) -> list[tuple[int, float]]:
    """Last price in each clock hour."""
    by_hour: dict[int, float] = {}
    for ts, px in samples:
        by_hour[int(ts) // HOUR * HOUR] = px
    return sorted(by_hour.items())


def _hourly_binance(symbol: str) -> list[tuple[int, float]]:
    from .data import _download

    now = int(time.time() * 1000)
    try:
        return [(c.ts, c.c) for c in _download(symbol, "1h", now - KEEP_MS, now)]
    except Exception:  # noqa: BLE001
        return []


def _ema(xs: list[float], n: int) -> float:
    k, e = 2 / (n + 1), xs[0]
    for x in xs[1:]:
        e = x * k + e * (1 - k)
    return e


def analyse(closes: list[float]) -> dict | None:
    """Short-term trend of an hourly close series (oldest first). None if there is not enough history."""
    if len(closes) < 12:
        return None
    logs = [math.log(c) for c in closes]
    win = logs[-24:]                                   # the trend window: the last 24 hours (or what exists)
    n = len(win)
    xm, ym = (n - 1) / 2, sum(win) / n
    sxx = sum((i - xm) ** 2 for i in range(n))
    slope = sum((i - xm) * (y - ym) for i, y in enumerate(win)) / sxx      # log-return per hour
    resid = [y - (ym + slope * (i - xm)) for i, y in enumerate(win)]
    ss_tot = sum((y - ym) ** 2 for y in win) or 1e-12
    r2 = max(0.0, 1 - sum(r * r for r in resid) / ss_tot)
    rets = [b - a for a, b in zip(logs[-49:], logs[-48:])]
    vol = (sum(r * r for r in rets) / max(1, len(rets))) ** 0.5 or 1e-9   # hourly volatility (log)
    atr = sum(abs(r) for r in rets[-24:]) / max(1, len(rets[-24:]))      # mean hourly move
    band = (sum(r * r for r in resid) / n) ** 0.5                         # channel half-width (log)
    fast, slow = _ema(closes[-48:], 12), _ema(closes[-48:], 48 if len(closes) >= 48 else len(closes))
    snr = slope / vol                                  # drift per hour in units of hourly noise
    score = abs(snr) * r2                              # strong AND clean = a narrow trend
    if r2 >= 0.45 and abs(snr) >= 0.08 and (slope > 0) == (fast > slow):
        trend = "up" if slope > 0 else "down"
    else:
        trend = "sideways"
    pct = lambda h: (closes[-1] / closes[-1 - h] - 1) * 100 if len(closes) > h else None  # noqa: E731
    return {
        "price": closes[-1], "trend": trend, "score": round(score, 4), "r2": round(r2, 3),
        "slope_pct_h": round(slope * 100, 4), "vol_pct_h": round(vol * 100, 4), "atr_pct_h": round(atr * 100, 4),
        "band_pct": round(band * 100, 4), "ema_fast": fast, "ema_slow": slow,
        "chg_1h": pct(1), "chg_4h": pct(4), "chg_24h": pct(24), "chg_7d": pct(167),
        "hours": len(closes), "spark": [round(c, 8) for c in closes[-48:]],
    }


def build() -> Path:
    contracts = load_contracts()
    store = _load()
    assets = []
    for sym, group, binance in UNIVERSE:
        c = contracts.get(sym)
        if c is None:
            continue
        hourly = _hourly_binance(binance) if binance else []
        source = "Binance 1h" + (" (PAXG as the gold proxy)" if sym == "XAU" else "")
        walt = store.get(sym, [])
        if len(hourly) < 12:
            hourly, source = _hourly_from_samples(walt), "Walt price, sampled every 10 min"
        closes = [p for _, p in hourly]
        a = analyse(closes)
        last_walt = walt[-1] if walt else None
        assets.append({
            "symbol": sym, "name": NAMES.get(sym, sym), "group": group, "source": source,
            "walt_price": last_walt[1] if last_walt else None, "walt_at": last_walt[0] if last_walt else None,
            "max_leverage": c.max_leverage, "closeout": c.closeout_margin_fraction,
            "funding_h": c.funding_rate_hourly, "analysis": a,
        })
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "markets.json"
    path.write_text(json.dumps({"updated": int(time.time() * 1000), "fee_pct": 0.07, "assets": assets},
                               separators=(",", ":")), encoding="utf-8")
    return path
