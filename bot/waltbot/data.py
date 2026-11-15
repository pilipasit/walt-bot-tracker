"""Historical candles from Binance's public spot API, cached as CSV.

Walt exposes only the latest rate, so history comes from Binance. Prices there are close
to, but not the same as, Walt's perp prices; stocks, FX and commodities are not available.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .contracts import ROOT

CACHE = ROOT / "data" / "candles"
# api.binance.com refuses some regions (HTTP 451), including US servers such as GitHub's; Binance's
# public market-data mirror serves the same candles there.
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
URL = "{host}/api/v3/klines?symbol={symbol}&interval={interval}&startTime={start}&limit=1000"
_host = [0]
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


@dataclass(frozen=True)
class Candle:
    ts: int  # open time, ms since epoch (UTC)
    o: float
    h: float
    l: float
    c: float
    v: float
    tb: float = 0.0  # taker-buy volume: the part of v bought at the ask (order-flow input)
    n: int = 0  # number of trades in the bar


def data_symbol(base_asset: str, override: str | None = None) -> str:
    return override or f"{base_asset.upper()}USDT"


def _download(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
    out: list[Candle] = []
    step = INTERVAL_MS[interval]
    while start_ms < end_ms:
        for attempt in range(5):
            url = URL.format(host=HOSTS[_host[0]], symbol=symbol, interval=interval, start=start_ms)
            req = urllib.request.Request(url, headers={"User-Agent": "market-data-client"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                if e.code in (403, 451) and _host[0] + 1 < len(HOSTS):
                    _host[0] += 1  # region block: switch to the mirror for the rest of the run
                    continue
                if attempt == 4:
                    raise
                time.sleep(3 * (attempt + 1))
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(3 * (attempt + 1))
        if not rows:
            break
        for r in rows:
            out.append(Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
                              float(r[9]), int(r[8])))
        start_ms = out[-1].ts + step
        if len(rows) < 1000:
            break
        time.sleep(0.2)
    return out


def _path(symbol: str, interval: str) -> Path:
    return CACHE / f"{symbol}_{interval}.csv"


def read_cache(symbol: str, interval: str) -> list[Candle]:
    p = _path(symbol, interval)
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return [Candle(int(r[0]), *(float(x) for x in r[1:6])) for r in csv.reader(f)]


def write_cache(symbol: str, interval: str, candles: list[Candle]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    with open(_path(symbol, interval), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for c in candles:
            w.writerow([c.ts, c.o, c.h, c.l, c.c, c.v])


def get_candles(symbol: str, interval: str, days: float, now_ms: int | None = None) -> list[Candle]:
    """Closed candles covering the last `days` days, using and extending the CSV cache."""
    if interval not in INTERVAL_MS:
        raise ValueError(f"interval must be one of {sorted(INTERVAL_MS)}")
    step = INTERVAL_MS[interval]
    now_ms = now_ms or int(time.time() * 1000)
    start_ms = now_ms - int(days * 86_400_000)
    cached = read_cache(symbol, interval)
    if cached and cached[0].ts <= start_ms + step:
        fresh = _download(symbol, interval, cached[-1].ts + step, now_ms)
        candles = cached + fresh
    else:
        candles = _download(symbol, interval, start_ms, now_ms)
    # Drop the candle that is still forming.
    candles = [c for c in candles if c.ts + step <= now_ms]
    if candles:
        write_cache(symbol, interval, candles)
    return [c for c in candles if c.ts >= start_ms]
