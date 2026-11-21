"""Long-term view of the tracker's assets (`longterm.html`): for investing horizons (months to years), not Walt day trades.

Once a day (the file is rebuilt when older than 20 hours) this reads up to 20 years of daily closes from Yahoo Finance
for every asset in markets.UNIVERSE and writes `data/market/longterm.json` with, per asset:
  * returns over 1 month to 5 years (3 and 5 years annualised);
  * the long-term trend: price vs its 50- and 200-day averages, the 200-day average's direction, the last 50/200
    cross, and the 10-month-average rule (month-end close above or below its 10-month average);
  * how far the trend is from flipping: the move that would put the price on the other side of its 200-day average,
    in % and in "normal months" (daily volatility x sqrt 21), and, if the 50- and 200-day averages are converging,
    the days to a cross at the last 10 days' pace;
  * valuation against its own history (price-based, the only measure that works for coins, metals, FX and stocks
    alike): the price relative to its 200-week average and where that ratio ranks in the asset's own history
    (percentile), the drawdown from the all-time high, and where the price sits in its 5-year range;
  * what followed in the past: 12-month forward returns from month-ends when the same asset group sat in the same
    valuation fifth (percentiles are computed from data available at the time, so no look-ahead), with the share
    of positive outcomes. Survivorship applies: the asset list is today's survivors.
Fundamentals (P/E, sales) are not included: the free sources need sign-in cookies. Theses come from
`data/market/theses.json`, written by hand and dated.
Analysis only; nothing here is financial advice.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .markets import NAMES, OUT, UNIVERSE, YAHOO

LONGTERM = OUT / "longterm.json"
MAX_AGE_S = 20 * 3600
# Yahoo symbols with long daily history (overrides the hourly ones in markets.YAHOO where a longer series exists).
DAILY = {"US100": "^NDX", "HYPE": "HYPE32196-USD", "SUI": "SUI20947-USD"}
DAY_MS = 86_400_000

# Wider long-term universe (not tradable on Walt): S&P 100 and Nasdaq-100 leaders plus the main ETFs, so the page can
# rank candidates beyond Walt's list. Names come from Yahoo. Walt symbols already in markets.UNIVERSE are skipped.
EXTRA_STOCKS = """BRK-B JPM LLY V UNH XOM MA JNJ PG HD COST ABBV WMT NFLX CRM BAC CVX KO MRK PEP TMO LIN ADBE ACN MCD
CSCO ABT WFC IBM GE DHR QCOM TXN INTU PM CAT AMGN VZ DIS NOW ISRG GS MS CMCSA NEE PFE RTX SPGI UNP T LOW HON AXP
BKNG COP UBER BLK PLD LMT SCHW SBUX MDT DE BMY GILD C MO ADP CB MDLZ TMUS SO DUK MMM USB BA CL NKE CVS TGT EMR GM F
COF FDX MET AIG ASML PANW CRWD SNPS CDNS MELI SHOP ANET LRCX AMAT KLAC MRVL ARM NET DDOG ABNB PYPL NVO SAP""".split()
EXTRA_ETFS = """QQQ VTI VT VXUS EFA EEM IWM DIA TLT IEF BND TIP GLD SLV XLK XLF XLE XLV XLI XLY XLP XLU XLRE XLB XLC SMH
VNQ SCHD VIG ARKK IBIT""".split()


def ysym(sym: str, group: str) -> str:
    if sym in DAILY:
        return DAILY[sym]
    if sym in YAHOO:
        return YAHOO[sym]
    return f"{sym}-USD" if group == "Crypto" else sym


def fetch_daily(symbol: str) -> tuple[list[tuple[int, float]], str | None]:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           "?interval=1d&range=20y&includePrePost=false")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (walt-bot-tracker)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)["chart"]["result"][0]
    ts, closes = res.get("timestamp") or [], res["indicators"]["quote"][0].get("close") or []
    out, seen = [], set()
    for t, c in zip(ts, closes):
        d = t // 86400
        if c and c > 0 and d not in seen:
            seen.add(d)
            out.append((t * 1000, float(c)))
    meta = res.get("meta") or {}
    return out, meta.get("longName") or meta.get("shortName")


def sma(xs: list[float], n: int, end: int | None = None) -> float | None:
    end = len(xs) if end is None else end
    return sum(xs[end - n:end]) / n if end >= n else None


def month_ends(rows: list[tuple[int, float]]) -> list[int]:
    """Indexes of the last trading day of each calendar month."""
    idx = []
    for i in range(len(rows)):
        if i + 1 == len(rows) or time.gmtime(rows[i][0] / 1000)[:2] != time.gmtime(rows[i + 1][0] / 1000)[:2]:
            idx.append(i)
    return idx


def analyse(rows: list[tuple[int, float]], group: str) -> dict | None:
    if len(rows) < 260:
        return None
    c = [x[1] for x in rows]
    n, last = len(c), c[-1]
    per_year = 365 if group == "Crypto" else 252           # crypto trades every day
    per_month = per_year / 12
    w200 = int(per_year * 200 / 52)                         # 200 weeks of trading days

    def ret(days):
        k = int(round(days))
        return (last / c[-1 - k] - 1) * 100 if n > k else None

    def ann(years):
        k = int(per_year * years)
        return ((last / c[-1 - k]) ** (1 / years) - 1) * 100 if n > k else None

    logr = [math.log(c[i] / c[i - 1]) for i in range(max(1, n - 60), n)]
    mu = sum(logr) / len(logr)
    vol_d = math.sqrt(sum((x - mu) ** 2 for x in logr) / (len(logr) - 1)) * 100
    s50, s200 = sma(c, 50), sma(c, 200)
    s200_prev = sma(c, 200, n - 20)
    slope200 = (s200 / s200_prev - 1) * 100 if s200 and s200_prev else None

    # last 50/200 cross
    cross = None
    for i in range(n - 1, 200, -1):
        a, b = sma(c, 50, i + 1) - sma(c, 200, i + 1), sma(c, 50, i) - sma(c, 200, i)
        if (a > 0) != (b > 0):
            cross = {"type": "golden" if a > 0 else "death", "date": time.strftime("%Y-%m-%d", time.gmtime(rows[i][0] / 1000)),
                     "days_ago": n - 1 - i}
            break
        if n - i > per_year * 3:
            break
    gap = (s50 / s200 - 1) * 100
    gap_prev = (sma(c, 50, n - 10) / sma(c, 200, n - 10) - 1) * 100
    pace = (gap - gap_prev) / 10
    days_to_cross = round(-gap / pace) if pace and (gap > 0) != (pace > 0) and -gap / pace < 120 else None

    # 10-month rule on month-end closes
    me = month_ends(rows)
    mc = [c[i] for i in me]
    ten_m = sum(mc[-11:-1]) / 10 if len(mc) >= 11 else None     # average of the last 10 completed month-ends
    ten_m_state = None if ten_m is None else ("above" if last > ten_m else "below")

    above = last > s200
    rising = (slope200 or 0) > 0
    regime = ("uptrend" if above and rising else "downtrend" if not above and not rising
              else "turning up" if above else "turning down")
    to_flip = (s200 / last - 1) * 100
    month_move = vol_d * math.sqrt(per_month)

    # valuation vs own history: price / 200-week average, percentile among all past daily values
    val = None
    if n > w200 + per_year * 3:  # rank only against at least 3 years of ratio history
        csum = [0.0]
        for x in c:
            csum.append(csum[-1] + x)
        ratios = [c[i] / ((csum[i + 1] - csum[i + 1 - w200]) / w200) for i in range(w200 - 1, n)]
        r_now = ratios[-1]
        pct = sum(1 for r in ratios if r <= r_now) / len(ratios) * 100
        val = {"vs_200w_pct": round((r_now - 1) * 100, 1), "percentile": round(pct), "history_years": round(len(ratios) / per_year, 1)}
    ath = max(c)
    k5 = int(per_year * 5)
    lo5, hi5 = min(c[-k5:]), max(c[-k5:])
    return {
        "price": last, "as_of": time.strftime("%Y-%m-%d", time.gmtime(rows[-1][0] / 1000)),
        "years": round(n / per_year, 1),
        "ret": {"1m": ret(per_month), "3m": ret(per_month * 3), "6m": ret(per_month * 6), "1y": ret(per_year),
                "3y_ann": ann(3), "5y_ann": ann(5)},
        "trend": {"regime": regime, "vs_50d_pct": (last / s50 - 1) * 100, "vs_200d_pct": (last / s200 - 1) * 100,
                  "sma200_slope_20d_pct": slope200, "sma50_vs_200_pct": gap, "last_cross": cross,
                  "days_to_cross": days_to_cross, "next_cross": "death" if gap > 0 else "golden", "ten_month": ten_m_state, "ten_month_avg": ten_m},
        "flip": {"move_pct": to_flip, "normal_months": abs(to_flip) / month_move if month_move else None,
                 "level": s200, "month_move_pct": month_move},
        "valuation": val, "from_ath_pct": (last / ath - 1) * 100,
        "range5y_pos": (last - lo5) / (hi5 - lo5) * 100 if hi5 > lo5 else None,
    }


def evidence(series: dict[str, tuple[str, list[tuple[int, float]]]]) -> dict:
    """12-month forward returns from month-ends, bucketed by the valuation fifth known at that time, per group."""
    out: dict[str, dict] = {}
    for sym, (group, rows) in series.items():
        c = [x[1] for x in rows]
        per_year = 365 if group == "Crypto" else 252
        w200 = int(per_year * 200 / 52)
        if len(c) < w200 + per_year * 4 or sym == "US10Y":  # a yield, not a price
            continue
        csum = [0.0]
        for x in c:
            csum.append(csum[-1] + x)
        ratio = {i: c[i] / ((csum[i + 1] - csum[i + 1 - w200]) / w200) for i in range(w200 - 1, len(c))}
        hist: list[float] = []
        last_i = w200 - 1
        for i in month_ends(rows):
            if i < w200 - 1:
                continue
            hist.extend(ratio[j] for j in range(last_i, i + 1))  # ratios known up to this month-end
            last_i = i + 1
            if len(hist) < per_year * 2 or i + per_year >= len(c):
                continue
            pct = sum(1 for r in hist if r <= ratio[i]) / len(hist) * 100
            fifth = min(4, int(pct // 20))
            fwd = (c[i + per_year] / c[i] - 1) * 100
            for key in (group, "All"):
                out.setdefault(key, {}).setdefault(fifth, []).append(fwd)
    res = {}
    for key, b in out.items():
        res[key] = {}
        for fifth in range(5):
            v = sorted(b.get(fifth, []))
            if len(v) < 12:
                continue
            res[key][str(fifth)] = {"n": len(v), "median": round(v[len(v) // 2], 1),
                                    "pos_share": round(sum(1 for x in v if x > 0) / len(v), 2),
                                    "p10": round(v[len(v) // 10], 1), "p90": round(v[len(v) * 9 // 10], 1)}
    return res


def build(force: bool = False) -> Path | None:
    if not force and LONGTERM.exists() and time.time() - LONGTERM.stat().st_mtime < MAX_AGE_S:
        return None
    theses = {}
    tp = OUT / "theses.json"
    if tp.exists():
        theses = json.loads(tp.read_text(encoding="utf-8"))
    walt = {sym for sym, _, _ in UNIVERSE}
    todo = [(sym, group, True) for sym, group, _ in UNIVERSE]
    todo += [(t, "Stocks", False) for t in EXTRA_STOCKS if t not in walt]
    todo += [(t, "ETFs", False) for t in EXTRA_ETFS if t not in walt]

    def one(item):
        sym, group, on_walt = item
        y = ysym(sym, group) if on_walt else sym
        for attempt in range(3):
            try:
                rows, name = fetch_daily(y)
                return item, y, rows, name, None
            except Exception as e:  # noqa: BLE001
                err = str(e)[:120]
                time.sleep(2 * (attempt + 1))
        return item, y, None, None, err

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(one, todo))
    assets, series, errors = [], {}, {}
    for (sym, group, on_walt), y, rows, name, err in results:
        if err:
            errors[sym] = err
            continue
        a = analyse(rows, group)
        if a is None:
            errors[sym] = f"only {len(rows)} days of data"
            continue
        series[sym] = (group, rows)
        label = NAMES.get(sym) or (name if not on_walt or group != "Crypto" else sym) or sym
        assets.append({"symbol": sym, "name": label, "group": group, "walt": on_walt,
                       "source": f"Yahoo Finance daily ({y})", **a, "thesis": theses.get(sym)})
    OUT.mkdir(parents=True, exist_ok=True)
    data = {"updated": int(time.time() * 1000), "assets": assets, "evidence": evidence(series), "errors": errors,
            "theses_updated": theses.get("_updated")}
    LONGTERM.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False, default=lambda o: None), encoding="utf-8")
    return LONGTERM


if __name__ == "__main__":
    p = build(force=True)
    d = json.loads(p.read_text(encoding="utf-8"))
    print(len(d["assets"]), "assets; errors:", d["errors"])
    for a in d["assets"]:
        v = a["valuation"] or {}
        t = a["trend"]
        print(f"{a['symbol']:8s} {a['years']:5.1f}y {t['regime']:12s} vs200d {t['vs_200d_pct']:+6.1f}% flip {a['flip']['normal_months'] or 0:4.1f}mo "
              f"val pct {v.get('percentile', '-'):>4} ath {a['from_ath_pct']:+6.1f}% 10m {t['ten_month']}")
    print(json.dumps(d["evidence"], indent=0)[:2500])
