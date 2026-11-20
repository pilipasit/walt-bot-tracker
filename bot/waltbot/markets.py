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
    ("HYPE", "Crypto", None), ("LINK", "Crypto", "LINKUSDT"), ("AVAX", "Crypto", "AVAXUSDT"),
    ("ADA", "Crypto", "ADAUSDT"), ("SUI", "Crypto", "SUIUSDT"), ("TRX", "Crypto", "TRXUSDT"), ("XMR", "Crypto", None),
    ("XAU", "Metals", "PAXGUSDT"), ("XAG", "Metals", None), ("XPT", "Metals", None), ("XCU", "Metals", None),
    ("WTI", "Energy", None), ("BRENTOIL", "Energy", None), ("NATGAS", "Energy", None),
    ("SPY", "Indices", None), ("US100", "Indices", None), ("US10Y", "Indices", None),
    ("NVDA", "Stocks", None), ("TSLA", "Stocks", None), ("AAPL", "Stocks", None), ("MSFT", "Stocks", None),
    ("AMZN", "Stocks", None), ("GOOGL", "Stocks", None), ("META", "Stocks", None), ("AMD", "Stocks", None),
    ("MSTR", "Stocks", None), ("COIN", "Stocks", None), ("PLTR", "Stocks", None), ("TSM", "Stocks", None),
    ("AVGO", "Stocks", None), ("MU", "Stocks", None), ("ORCL", "Stocks", None), ("INTC", "Stocks", None),
    ("HOOD", "Stocks", None), ("BABA", "Stocks", None),
    ("EURUSD", "FX", None), ("GBPUSD", "FX", None), ("USDJPY", "FX", None), ("USDCHF", "FX", None),
    ("USDCAD", "FX", None),
]
# Yahoo Finance hourly candles as the history for everything Binance doesn't cover. Unofficial data, used for this
# personal analysis page only; futures stand in for spot commodities (levels still use Walt's own price).
YAHOO = {"XAU": "GC=F", "XAG": "SI=F", "XPT": "PL=F", "XCU": "HG=F", "WTI": "CL=F", "BRENTOIL": "BZ=F",
         "NATGAS": "NG=F", "SPY": "SPY", "US100": "NQ=F", "US10Y": "^TNX", "EURUSD": "EURUSD=X",
         "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X", "USDCHF": "CHF=X", "USDCAD": "CAD=X", "HYPE": "HYPE32196-USD",
         "XMR": "XMR-USD"}
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
    # PAXG at the same moment: the page moves Walt's gold by PAXG's live change SINCE THIS SAMPLE (PAXG itself sits
    # ~0.2% away from Walt's gold, so only its change is used, never its level).
    try:
        import urllib.request
        for host in ("https://data-api.binance.vision", "https://api.binance.com"):
            try:
                t = json.load(urllib.request.urlopen(f"{host}/api/v3/ticker/price?symbol=PAXGUSDT", timeout=10))
                store.setdefault("_PAXG", []).append([now, float(t["price"])])
                break
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    for sym in store:
        store[sym] = [s for s in store[sym] if s[0] > now - KEEP_MS]
    OUT.mkdir(parents=True, exist_ok=True)
    SAMPLES.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")
    # live.json: just the latest Walt price per asset (+ PAXG at that moment) for the page's 1-minute refresh.
    latest = {k: v[-1] for k, v in store.items() if v and not k.startswith("_")}
    paxg = store.get("_PAXG", [])
    (OUT / "live.json").write_text(json.dumps({"updated": now, "prices": latest,
                                              "paxg": paxg[-1] if paxg and paxg[-1][0] > now - 120_000 else None},
                                             separators=(",", ":")), encoding="utf-8")
    return n


def _hourly_from_samples(samples: list[list[float]]) -> list[tuple[int, float, float, float, float]]:
    """Hourly candles (ts, open, high, low, close) built from Walt's 10-minute samples."""
    by_hour: dict[int, list[float]] = {}
    for ts, px in samples:
        by_hour.setdefault(int(ts) // HOUR * HOUR, []).append(px)
    return [(h, v[0], max(v), min(v), v[-1]) for h, v in sorted(by_hour.items())]


def _hourly_binance(symbol: str) -> list[tuple[int, float]]:
    from .data import _download

    now = int(time.time() * 1000)
    try:
        return [(c.ts, c.o, c.h, c.l, c.c) for c in _download(symbol, "1h", now - KEEP_MS, now)]
    except Exception:  # noqa: BLE001
        return []


def _hourly_yahoo(symbol: str) -> list[tuple]:
    import urllib.parse
    import urllib.request

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1h&range=1mo"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.load(urllib.request.urlopen(req, timeout=20))["chart"]["result"][0]
        q = d["indicators"]["quote"][0]
        rows = [(int(t) * 1000, o, h, l, c) for t, o, h, l, c in zip(d["timestamp"], q["open"], q["high"], q["low"], q["close"])
                if None not in (o, h, l, c)]
        return rows[-168:]
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


def _maintenance() -> dict[str, float]:
    """Walt liquidates at the MAINTENANCE margin (checked against Walt's own liquidation price, 20.11.2026)."""
    raw = json.loads((ROOT / "data" / "contracts_raw.json").read_text(encoding="utf-8"))
    return {c["base_asset"]: float((c.get("margin") or {}).get("maintenance_margin_fraction", 0)) / 10_000
            for c in raw.get("contracts", [])}


def _ema_series(xs: list[float], n: int) -> list[float]:
    k, e, out = 2 / (n + 1), xs[0], []
    for x in xs:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def _chart(hourly: list[tuple]) -> dict:
    """What the page draws: the last 72 hourly candles, the 12h/48h averages, the 24h trend channel, and every hour
    in the window where the trend call (up / down / sideways) changed — computed as it would have been AT that hour."""
    closes = [h[4] for h in hourly]
    show = 72
    start = max(0, len(hourly) - show)
    e12, e48 = _ema_series(closes, 12), _ema_series(closes, 48)
    flips, prev = [], None
    for i in range(max(12, start), len(closes)):
        a = analyse(closes[: i + 1])
        t = a["trend"] if a else None
        if prev is not None and t != prev:
            flips.append({"i": i - start, "from": prev, "to": t, "price": closes[i]})
        prev = t
    # trend channel over the last 24 bars: fitted line ± 2 residual standard deviations (log scale → prices)
    n = min(24, len(closes))
    logs = [math.log(c) for c in closes[-n:]]
    xm, ym = (n - 1) / 2, sum(logs) / n
    sxx = sum((i - xm) ** 2 for i in range(n)) or 1
    b = sum((i - xm) * (y - ym) for i, y in enumerate(logs)) / sxx
    sd = (sum((y - (ym + b * (i - xm))) ** 2 for i, y in enumerate(logs)) / n) ** 0.5
    first = len(closes) - n - start
    line = lambda i, k: round(math.exp(ym + b * (i - xm) + k * sd), 10)  # noqa: E731
    return {
        "candles": [[h[0], *(round(v, 10) for v in h[1:5])] for h in hourly[start:]],
        "ema_fast": [round(v, 10) for v in e12[start:]], "ema_slow": [round(v, 10) for v in e48[start:]],
        "channel": {"from": first, "mid": [line(0, 0), line(n - 1, 0)], "hi": [line(0, 2), line(n - 1, 2)],
                    "lo": [line(0, -2), line(n - 1, -2)]},
        "flips": flips,
    }


def _window_trend(closes: list[float], n: int) -> dict | None:
    """Direction of a longer window: least-squares slope on log prices, called only when the fit is clean (R² ≥ 0.4)."""
    if len(closes) < max(12, n * 2 // 3):
        return None
    logs = [math.log(c) for c in closes[-n:]]
    k = len(logs)
    xm, ym = (k - 1) / 2, sum(logs) / k
    sxx = sum((i - xm) ** 2 for i in range(k)) or 1
    b = sum((i - xm) * (y - ym) for i, y in enumerate(logs)) / sxx
    ss = sum((y - ym) ** 2 for y in logs) or 1e-12
    r2 = max(0.0, 1 - sum((y - (ym + b * (i - xm))) ** 2 for i, y in enumerate(logs)) / ss)
    move = (math.exp(b * (k - 1)) - 1) * 100
    return {"trend": ("up" if b > 0 else "down") if r2 >= 0.4 else "sideways", "r2": round(r2, 3),
            "move_pct": round(move, 2)}


def _proxy_ref(store: dict, last_walt) -> float | None:
    """PAXG price recorded with Walt's latest gold sample (within 2 minutes of it), or None."""
    if not last_walt:
        return None
    near = [px for ts, px in store.get("_PAXG", []) if abs(ts - last_walt[0]) < 120_000]
    return near[-1] if near else None


def build() -> Path:
    contracts = load_contracts()
    maint = _maintenance()
    store = _load()
    assets = []
    for sym, group, binance in UNIVERSE:
        c = contracts.get(sym)
        if c is None:
            continue
        hourly = _hourly_binance(binance) if binance and sym != "XAU" else []
        source = "Binance 1h"
        ysym = YAHOO.get(sym, sym if group == "Stocks" else None)  # US stocks use their own ticker
        if len(hourly) < 12 and ysym:
            hourly, source = _hourly_yahoo(ysym), f"Yahoo Finance 1h ({ysym})"
        walt = store.get(sym, [])
        if len(hourly) < 12:
            hourly, source = _hourly_from_samples(walt), "Walt price, sampled every 10 min"
        closes = [h[4] for h in hourly]
        a = analyse(closes)
        if a:
            a["chart"] = _chart(hourly)
            # Multi-timeframe view: the 24 h trend (above) checked against 3-day and 7-day windows.
            tf = {"24h": {"trend": a["trend"], "r2": a["r2"], "move_pct": a["chg_24h"]},
                  "3d": _window_trend(closes, 72), "7d": _window_trend(closes, 168)}
            a["tf"] = tf
            dirs = [v["trend"] for v in tf.values() if v]
            a["aligned"] = len(dirs) == 3 and dirs[0] != "sideways" and len(set(dirs)) == 1
        last_walt = walt[-1] if walt else None
        # Levels are computed from the price the owner actually trades at: Walt's, when a recent sample exists.
        if a and last_walt and last_walt[0] > time.time() * 1000 - 30 * 60_000:
            a["price"] = last_walt[1]
        assets.append({
            "symbol": sym, "name": NAMES.get(sym, sym), "group": group, "source": source,
            "walt_price": last_walt[1] if last_walt else None, "walt_at": last_walt[0] if last_walt else None,
            "max_leverage": c.max_leverage, "closeout": c.closeout_margin_fraction, "maintenance": maint.get(sym),
            "binance": binance if binance and sym != "XAU" else None,  # live stream on the page (PAXG != Walt gold)
            # Gold has no direct live feed; the page moves Walt's last gold price by PAXG's live % change.
            "proxy": "PAXGUSDT" if sym == "XAU" else None,
            "proxy_ref": _proxy_ref(store, last_walt) if sym == "XAU" else None,
            "funding_h": c.funding_rate_hourly, "analysis": a,
        })
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "markets.json"
    path.write_text(json.dumps({"updated": int(time.time() * 1000), "fee_pct": 0.07, "assets": assets},
                               separators=(",", ":")), encoding="utf-8")
    return path
