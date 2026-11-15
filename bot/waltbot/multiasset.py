"""Long-history, multi-asset research on daily bars.

Data: Yahoo Finance daily history (decades for stocks, indices, commodities, FX; crypto from
2014) and the crypto Fear & Greed index (from 2018). Each asset maps to a Walt Perps contract.

Backtests are vectorised: a signal known at day t's close sets the position held from day
t+1's open to day t+2's open (open-to-open returns), so nothing uses information before it
exists. Costs: Walt's taker fee plus 0.05 % slippage on every unit traded, and funding per
calendar day held under three scenarios (none, Walt-typical with longs paying, adverse).
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from .contracts import ROOT

CACHE = ROOT / "data" / "research" / "daily"
UA = {"User-Agent": "Mozilla/5.0"}

# Walt contract -> Yahoo ticker, asset class
ASSETS = {
    # crypto
    "BTC": ("BTC-USD", "crypto"), "ETH": ("ETH-USD", "crypto"), "XRP": ("XRP-USD", "crypto"), "SOL": ("SOL-USD", "crypto"),
    "DOGE": ("DOGE-USD", "crypto"), "ADA": ("ADA-USD", "crypto"), "LINK": ("LINK-USD", "crypto"), "BNB": ("BNB-USD", "crypto"),
    "LTC_proxy_BCH": ("BCH-USD", "crypto"), "TRX": ("TRX-USD", "crypto"), "XLM": ("XLM-USD", "crypto"), "DOT": ("DOT-USD", "crypto"),
    # indices
    "SPY": ("SPY", "index"), "US100": ("QQQ", "index"),
    # US stocks on Walt
    "NVDA": ("NVDA", "stock"), "TSLA": ("TSLA", "stock"), "AAPL": ("AAPL", "stock"), "AMZN": ("AMZN", "stock"),
    "GOOGL": ("GOOGL", "stock"), "META": ("META", "stock"), "AMD": ("AMD", "stock"), "INTC": ("INTC", "stock"),
    "ORCL": ("ORCL", "stock"), "MU": ("MU", "stock"), "AVGO": ("AVGO", "stock"), "TSM": ("TSM", "stock"),
    "MRVL": ("MRVL", "stock"), "IBM": ("IBM", "stock"), "DELL": ("DELL", "stock"), "COIN": ("COIN", "stock"),
    "MSTR": ("MSTR", "stock"), "PLTR": ("PLTR", "stock"), "HOOD": ("HOOD", "stock"), "GME": ("GME", "stock"),
    "BABA": ("BABA", "stock"), "MRNA": ("MRNA", "stock"),
    # commodities
    "XAU": ("GC=F", "commodity"), "XAG": ("SI=F", "commodity"), "WTI": ("CL=F", "commodity"), "BRENTOIL": ("BZ=F", "commodity"),
    "NATGAS": ("NG=F", "commodity"), "XCU": ("HG=F", "commodity"), "XPT": ("PL=F", "commodity"), "XPD": ("PA=F", "commodity"),
    # FX and rates
    "EURUSD": ("EURUSD=X", "fx"), "GBPUSD": ("GBPUSD=X", "fx"), "USDJPY": ("JPY=X", "fx"), "USDCHF": ("CHF=X", "fx"),
    "USDCAD": ("CAD=X", "fx"), "US10Y": ("^TNX", "rates"),
}
EXTRA = {"VIX": "^VIX"}
FEE = {"crypto": 0.0009, "stock": 0.0008, "index": 0.0008, "commodity": 0.0008, "fx": 0.0008, "rates": 0.0008}
SLIP = 0.0005
FUND = {"none": 0.0, "walt_typical": 0.000032, "adverse": 0.000032}  # per hour; typical = longs pay, shorts receive


def yahoo(ticker: str) -> pd.DataFrame:
    path = CACHE / f"{ticker.replace('^', '_').replace('=', '_')}.csv"
    if path.exists() and time.time() - path.stat().st_mtime < 24 * 3600:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1=0&period2={int(time.time())}&interval=1d"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                j = json.load(r)
            break
        except OSError:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    res = j["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"]},
                      index=pd.to_datetime(res["timestamp"], unit="s").normalize())
    df = df[~df.index.duplicated(keep="last")].dropna(subset=["open", "close"])
    df = df[(df.open > 0) & (df.close > 0)]
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    time.sleep(0.4)
    return df


def fear_greed() -> pd.Series:
    path = CACHE / "fear_greed.csv"
    if path.exists() and time.time() - path.stat().st_mtime < 24 * 3600:
        return pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
    with urllib.request.urlopen(urllib.request.Request("https://api.alternative.me/fng/?limit=0&format=json", headers=UA), timeout=30) as r:
        j = json.load(r)
    s = pd.Series({pd.to_datetime(int(x["timestamp"]), unit="s").normalize(): int(x["value"]) for x in j["data"]}, name="fng").sort_index()
    CACHE.mkdir(parents=True, exist_ok=True)
    s.to_csv(path)
    return s


def load_all() -> dict[str, pd.DataFrame]:
    out = {}
    for name, (tk, cls) in ASSETS.items():
        try:
            df = yahoo(tk)
        except Exception as e:  # noqa: BLE001
            print("skip", name, e)
            continue
        if len(df) > 400:
            df.attrs["cls"] = cls
            out[name] = df
    return out


# ---------------------------------------------------------------- backtest
def backtest(df: pd.DataFrame, pos: pd.Series, cls: str, funding: str = "walt_typical") -> pd.Series:
    """Daily net returns of holding `pos` (decided at each close, -1..1) from the next open."""
    pos = pos.reindex(df.index).fillna(0.0).clip(-1, 1)
    held = pos.shift(1).fillna(0.0)                       # position during day t+1 (open t+1 -> open t+2)
    oo = df.open.shift(-1) / df.open - 1                  # return from today's open to tomorrow's open
    gross = held * oo
    trade = held.diff().abs().fillna(held.abs())
    cost = trade * (FEE[cls] + SLIP)
    days = (df.index.to_series().shift(-1) - df.index.to_series()).dt.days.fillna(1).clip(lower=1)
    rate = FUND[funding] * 24 * days
    if funding == "walt_typical":
        fund = held * rate                                 # longs pay, shorts receive
    elif funding == "adverse":
        fund = held.abs() * rate
    else:
        fund = 0.0 * held
    return (gross - cost - fund).dropna()


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 60 or r.std() == 0:
        return {"cagr": 0.0, "sharpe": 0.0, "maxdd": 0.0, "t": 0.0, "exposure_days": 0, "years": len(r) / 252}
    eq = (1 + r).cumprod()
    years = (r.index[-1] - r.index[0]).days / 365.25
    ann = 252 if r.index.to_series().diff().dt.days.median() <= 1 else 365
    cagr = eq.iloc[-1] ** (1 / max(years, 1e-9)) - 1 if eq.iloc[-1] > 0 else -1.0
    return {"cagr": round(cagr * 100, 2), "sharpe": round(r.mean() / r.std() * math.sqrt(ann), 2),
            "maxdd": round((1 - eq / eq.cummax()).max() * 100, 1), "t": round(r.mean() / r.std() * math.sqrt(len(r)), 2),
            "years": round(years, 1)}


PERIODS = [("to 2014", None, "2014-12-31"), ("2015-19", "2015-01-01", "2019-12-31"),
           ("2020-22", "2020-01-01", "2022-12-31"), ("2023-now", "2023-01-01", None)]


def evaluate(df, pos, cls, name, rule, funding="walt_typical", bench=True) -> dict:
    r = backtest(df, pos, cls, funding)
    row = {"asset": name, "class": cls, "rule": rule, "funding": funding, **stats(r)}
    for label, a, b in PERIODS:
        part = r.loc[a:b]
        row[label] = round(((1 + part).prod() ** (252 / max(len(part), 1)) - 1) * 100, 1) if len(part) > 120 else None
    if bench:
        row["buyhold_cagr"] = stats(backtest(df, pd.Series(1.0, index=df.index), cls, "none"))["cagr"]
    row["trades_per_year"] = round(pos.reindex(df.index).fillna(0).diff().abs().sum() / max(row["years"], 0.1) / 2, 1)
    row["time_in_market"] = round(float((pos.reindex(df.index).fillna(0) != 0).mean()) * 100, 0)
    return row


# ---------------------------------------------------------------- rules (signal at close t)
def rsi(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def hold_after(trigger: pd.Series, days: int, side: float = 1.0) -> pd.Series:
    """Position `side` for `days` bars after each trigger (re-triggers extend)."""
    out = np.zeros(len(trigger))
    left = 0
    for i, t in enumerate(trigger.fillna(False).to_numpy()):
        if t:
            left = days
        if left > 0:
            out[i] = side
            left -= 1
    return pd.Series(out, index=trigger.index)


def rules(df: pd.DataFrame, vix: pd.Series | None, fng: pd.Series | None) -> dict[str, pd.Series]:
    c, v = df.close, df.volume.fillna(0)
    r1 = c.pct_change()
    vol60 = r1.rolling(60).std()
    sma = lambda n: c.rolling(n).mean()  # noqa: E731
    R = {}
    # 1. trend following
    for lb in (63, 126, 252):
        s = np.sign(c / c.shift(lb) - 1)
        R[f"tsmom_{lb}d_ls"] = s
        R[f"tsmom_{lb}d_long"] = s.clip(lower=0)
    R["sma50_200_ls"] = np.sign(sma(50) - sma(200))
    R["sma50_200_long"] = (sma(50) > sma(200)).astype(float)
    R["sma20_100_ls"] = np.sign(sma(20) - sma(100))
    R["price_above_sma200_long"] = (c > sma(200)).astype(float)
    # volatility-scaled trend (target ~ equal risk; capped at 1x)
    R["tsmom_252d_volscaled"] = (np.sign(c / c.shift(252) - 1) * (0.15 / (vol60 * math.sqrt(252))).clip(upper=1.0))
    # 2. panic / oversold reversal
    rsi2 = rsi(c, 2)
    pos = np.zeros(len(c)); state = 0
    for i in range(len(c)):
        if state == 0 and rsi2.iloc[i] < 10 and c.iloc[i] > (sma(200).iloc[i] if not np.isnan(sma(200).iloc[i]) else np.inf):
            state = 1
        elif state == 1 and c.iloc[i] > sma(5).iloc[i]:
            state = 0
        pos[i] = state
    R["rsi2_dip_in_uptrend"] = pd.Series(pos, index=c.index)
    big_drop = r1 < -2.5 * vol60
    R["buy_2.5sigma_drop_hold5"] = hold_after(big_drop, 5, 1.0)
    R["buy_3_down_days_hold3"] = hold_after((r1 < 0) & (r1.shift(1) < 0) & (r1.shift(2) < 0), 3, 1.0)
    # 3. shock days (earnings/news proxy): big move on heavy volume
    shock = (r1.abs() > 3 * vol60) & (v > 2 * v.rolling(60).mean())
    R["shock_follow_hold20"] = hold_after(shock & (r1 > 0), 20, 1.0) - hold_after(shock & (r1 < 0), 20, 1.0)
    R["shock_fade_hold5"] = hold_after(shock & (r1 < 0), 5, 1.0) - hold_after(shock & (r1 > 0), 5, 1.0)
    # 4. calendar: turn of the month (last trading day + first 3)
    idx = c.index
    month = pd.Series(idx.month, index=idx)
    last_day = month != month.shift(-1)
    first3 = (month != month.shift(1)) | (month != month.shift(2)) | (month != month.shift(3))
    # position held from next open, so signal one day earlier
    R["turn_of_month_long"] = (last_day.shift(-1, fill_value=False) | last_day | first3.shift(-1, fill_value=False)).astype(float)
    # 5. market fear (VIX) for equities, Fear & Greed for crypto
    if vix is not None:
        vx = vix.reindex(idx).ffill()
        R["vix_above_30_hold20"] = hold_after(vx > 30, 20, 1.0)
        R["vix_spike_1.3x_hold10"] = hold_after(vx > 1.3 * vx.rolling(20).mean(), 10, 1.0)
    if fng is not None:
        f = fng.reindex(idx).ffill()
        pos = np.zeros(len(c)); state = 0
        for i in range(len(c)):
            fi = f.iloc[i]
            if np.isnan(fi):
                continue
            if state == 0 and fi < 20:
                state = 1
            elif state == 1 and fi > 50:
                state = 0
            pos[i] = state
        R["fng_extreme_fear_long"] = pd.Series(pos, index=idx)
        R["fng_contrarian_ls"] = ((f < 25).astype(float) - (f > 75).astype(float))
    return R


def cross_sectional(data: dict[str, pd.DataFrame], names: list[str], lookback: int, hold: int, k: int, mode: str) -> dict[str, pd.Series]:
    """Every `hold` days rank `names` by `lookback`-day return; long the top k, short the bottom k (0.5/k each side)."""
    closes = pd.DataFrame({n: data[n].close for n in names}).dropna(how="all")
    mom = closes / closes.shift(lookback) - 1
    w = pd.DataFrame(0.0, index=closes.index, columns=names)
    for i in range(lookback, len(closes), hold):
        row = mom.iloc[i].dropna()
        if len(row) < 2 * k + 1:
            continue
        top, bot = row.nlargest(k).index, row.nsmallest(k).index
        sign = 1 if mode == "momentum" else -1
        block = slice(i, min(i + hold, len(closes)))
        w.iloc[block, [names.index(t) for t in top]] = sign
        w.iloc[block, [names.index(b) for b in bot]] = -sign
    return {n: w[n] for n in names}
