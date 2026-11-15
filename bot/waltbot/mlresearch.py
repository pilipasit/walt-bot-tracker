"""Model-based research: combine every available input and trade only predicted moves.

Instead of hand-written rules, a model is fitted to predict the return over the next H hours
from ~30 inputs (price, volatility, volume, order flow, time of day, BTC context, the perp
premium over spot, and the funding rate). It then trades only when the predicted move is
large, with several thresholds giving different trade frequencies.

Split by time, same dates for every coin: fit on the first 60 %, choose on the next 20 %,
judge on the last 20 %. Needs numpy, pandas and scikit-learn (this module only).
"""
from __future__ import annotations

import json
import time
import urllib.request

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from .contracts import get_contract
from .research import DIR, EIGHT, _segment, load

INTERVAL, DAYS, HOUR = "1h", 730, 3_600_000
HORIZONS = (4, 12, 24)
QUANTILES = (0.70, 0.85, 0.93, 0.97, 0.99)  # trade the top 30 % ... 1 % of predicted moves
EXITS = {"stop3": {"stop_loss_pct": 0.03, "take_profit_pct": None},
         "stop6": {"stop_loss_pct": 0.06, "take_profit_pct": None}}
COST = 0.0028  # two taker fees + assumed slippage, as a share of notional


def _get(url: str):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "market-data-client"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except OSError:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))


def futures_series(symbol: str, kind: str, start: int, end: int) -> pd.Series:
    """Hourly perp premium over spot ('basis') or the funding rate, from Binance futures, cached."""
    path = DIR / "futures" / f"{symbol}_{kind}_{DAYS}.csv"
    if path.exists() and time.time() - path.stat().st_mtime < 12 * 3600:
        return pd.read_csv(path, index_col=0).iloc[:, 0]
    rows, t = {}, start
    while t < end:
        if kind == "basis":
            data = _get(f"https://fapi.binance.com/fapi/v1/premiumIndexKlines?symbol={symbol}USDT&interval=1h&startTime={t}&limit=1500")
            if not data:
                break
            for r in data:
                rows[int(r[0])] = float(r[4])
            t = int(data[-1][0]) + HOUR
            if len(data) < 1500:
                break
        else:
            data = _get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}USDT&startTime={t}&limit=1000")
            if not data:
                break
            for r in data:
                rows[int(r["fundingTime"]) // HOUR * HOUR] = float(r["fundingRate"])
            t = int(data[-1]["fundingTime"]) + 1
            if len(data) < 1000:
                break
        time.sleep(0.25)
    s = pd.Series(rows, name=kind).sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    s.to_csv(path)
    return s


def frame(symbol: str, btc: pd.DataFrame | None) -> pd.DataFrame:
    """Features known at each bar's close, plus forward returns measured from the next open."""
    cs = load(symbol, INTERVAL, DAYS)
    d = pd.DataFrame({"o": [c.o for c in cs], "h": [c.h for c in cs], "l": [c.l for c in cs], "c": [c.c for c in cs],
                      "v": [c.v for c in cs], "tb": [c.tb for c in cs], "n": [c.n for c in cs]},
                     index=[c.ts for c in cs])
    lc = np.log(d.c)
    f = pd.DataFrame(index=d.index)
    r1 = lc.diff()
    vol = r1.rolling(24).std()
    for k in (1, 4, 12, 24, 72):
        f[f"r{k}"] = lc.diff(k)
        f[f"z{k}"] = lc.diff(k) / (vol * np.sqrt(k))
    f["vol24"] = vol
    f["vol_ratio"] = vol / r1.rolling(168).std()
    f["volume_z"] = np.log((d.v + 1e-9) / (d.v.rolling(168).mean() + 1e-9))
    f["trades_z"] = np.log((d.n + 1) / (d.n.rolling(168).mean() + 1))
    for k in (4, 24):
        vs = d.v.rolling(k).sum()
        f[f"flow{k}"] = (2 * d.tb.rolling(k).sum() - vs) / (vs + 1e-9)
    hi, lo = d.h.rolling(24).max(), d.l.rolling(24).min()
    f["range_pos"] = (d.c - lo) / (hi - lo + 1e-12)
    up, dn = r1.clip(lower=0).rolling(14).sum(), (-r1.clip(upper=0)).rolling(14).sum()
    f["rsi"] = up / (up + dn + 1e-12)
    for k in (50, 200):
        f[f"ema{k}"] = (lc - lc.ewm(span=k, adjust=False).mean()) / (vol * np.sqrt(k))
    hour = (d.index // HOUR) % 24
    f["hour_sin"], f["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    f["weekend"] = (((d.index // 86_400_000) + 3) % 7 >= 5).astype(float)  # day 0 was a Thursday
    if btc is not None:
        for k in (1, 4, 24):
            f[f"btc_r{k}"] = btc[f"r{k}"].reindex(f.index)
        f["rel_btc24"] = f["r24"] - f["btc_r24"]
    else:
        for k in (1, 4, 24):
            f[f"btc_r{k}"] = 0.0
        f["rel_btc24"] = 0.0
    start, end = int(d.index[0]), int(d.index[-1]) + HOUR
    try:
        basis = futures_series(symbol, "basis", start, end).reindex(f.index).ffill()
        fund = futures_series(symbol, "funding", start, end).reindex(f.index).ffill()
    except OSError:
        basis = fund = pd.Series(0.0, index=f.index)
    f["basis"] = basis
    f["basis_chg4"] = basis.diff(4)
    f["basis_z"] = (basis - basis.rolling(168).mean()) / (basis.rolling(168).std() + 1e-9)
    f["funding"] = fund
    lo_ = np.log(d.o)
    for h in HORIZONS:  # enter at the next bar's open, leave at the open h bars later
        f[f"y{h}"] = lo_.shift(-(h + 1)) - lo_.shift(-1)
    f["symbol"] = symbol
    return f


def positions(pred: np.ndarray, thr: float, hold: int) -> list[int]:
    """Enter when |prediction| exceeds thr, hold for `hold` bars, then look again."""
    out, state, left = [], 0, 0
    for p in pred:
        if left > 0:
            left -= 1
            if left == 0:
                state = 0
        if state == 0 and np.isfinite(p) and abs(p) > thr:
            state, left = (1 if p > 0 else -1), hold
        out.append(state)
    return out


def run(base: dict) -> dict:
    btc = frame("BTC", None)
    frames = {"BTC": btc}
    for s in EIGHT:
        if s != "BTC":
            frames[s] = frame(s, btc)
    feats = [c for c in btc.columns if not c.startswith("y") and c != "symbol"]
    ts = sorted(set.intersection(*(set(f.index) for f in frames.values())))
    t_a, t_b = ts[int(len(ts) * 0.6)], ts[int(len(ts) * 0.8)]
    span_days = (ts[-1] - ts[0]) / 86_400_000
    seg_days = {"valid": span_days * 0.2, "test": span_days * 0.2}
    candles = {s: load(s, INTERVAL, DAYS) for s in EIGHT}
    results, ic = [], []
    for h in HORIZONS:
        y = f"y{h}"
        pooled = pd.concat([f.dropna(subset=feats + [y]) for f in frames.values()])
        train = pooled[pooled.index < t_a - (h + 1) * HOUR]  # purge: no target reaching into validation
        scaler = StandardScaler().fit(train[feats])
        models = {
            "ridge": Ridge(alpha=100.0).fit(scaler.transform(train[feats]), train[y]),
            "boosted_trees": HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=200,
                                                           l2_regularization=1.0, random_state=0).fit(train[feats], train[y]),
        }
        for mname, model in models.items():
            preds = {}
            for s, f in frames.items():
                x = f[feats].copy()
                ok = x.notna().all(axis=1)
                p = pd.Series(np.nan, index=f.index)
                if mname == "ridge":
                    p[ok] = model.predict(scaler.transform(x[ok]))
                else:
                    p[ok] = model.predict(x[ok])
                preds[s] = p
            allp = pd.concat([pd.DataFrame({"p": preds[s], "y": frames[s][y]}) for s in EIGHT]).dropna()
            for seg, lo, hi in (("valid", t_a, t_b), ("test", t_b, ts[-1] + 1)):
                part = allp[(allp.index >= lo) & (allp.index < hi)]
                ic.append({"model": mname, "horizon": h, "segment": seg,
                           "ic": round(float(np.corrcoef(part.p, part.y)[0, 1]), 4)})
            valid_abs = allp[(allp.index >= t_a) & (allp.index < t_b)].p.abs()
            for q in QUANTILES:
                thr = float(valid_abs.quantile(q))
                gross = {}
                for seg, lo, hi in (("valid", t_a, t_b), ("test", t_b, ts[-1] + 1)):
                    part = allp[(allp.index >= lo) & (allp.index < hi) & (allp.p.abs() > thr)]
                    gross[seg] = float((np.sign(part.p) * part.y).mean()) if len(part) else 0.0
                for ename, exits in EXITS.items():
                    risk = {**base["risk"], **exits}
                    per = {"valid": [], "test": []}
                    for s in EIGHT:
                        cs = candles[s]
                        series = [0] * len(cs)
                        pos = positions(preds[s].reindex([c.ts for c in cs]).to_numpy(), thr, h)
                        series[: len(pos)] = pos
                        a = next(i for i, c in enumerate(cs) if c.ts >= t_a)
                        b = next(i for i, c in enumerate(cs) if c.ts >= t_b)
                        contract = get_contract(s)
                        per["valid"].append(_segment(contract, base, risk, series[:b], cs[:b], INTERVAL, cs[a].ts))
                        per["test"].append(_segment(contract, base, risk, series, cs, INTERVAL, cs[b].ts))
                    row = {"config": f"{mname}/h{h}/top{round((1 - q) * 100)}%/{ename}", "model": mname, "horizon": h,
                           "quantile": q, "exit": ename}
                    for seg in ("valid", "test"):
                        row[seg] = round(sum(r["ret"] for r in per[seg]) / len(EIGHT), 2)
                        row[f"{seg}_trades_per_day"] = round(sum(r["trades"] for r in per[seg]) / seg_days[seg], 2)
                        row[f"{seg}_symbols_pos"] = sum(r["ret"] > 0 for r in per[seg])
                        row[f"{seg}_gross_per_trade_pct"] = round(gross[seg] * 100, 3)
                    results.append(row)
            print(f"done {mname} h{h}", flush=True)
    selected = sorted([r for r in results if r["valid"] > 0 and r["valid_symbols_pos"] >= 5], key=lambda r: -r["valid"])
    out = {"features": feats, "ic": ic, "results": results, "selected": [r["config"] for r in selected],
           "cost_per_trade_pct": COST * 100}
    (DIR / "results_model.json").write_text(json.dumps(out), encoding="utf-8")
    return out


def table(rows: list[dict]) -> str:
    # The *_gross_per_trade_pct fields stay in the JSON but are not shown: they average overlapping
    # bars that cluster on a few volatile days, and overstate what non-overlapping trades capture.
    cols = ["config", "valid", "test", "valid_trades_per_day", "test_trades_per_day", "valid_symbols_pos",
            "test_symbols_pos"]
    if not rows:
        return "(none)"
    w = [max(len(c), *(len(str(r[c])) for r in rows)) + 2 for c in cols]
    return "\n".join(["".join(c.ljust(x) for c, x in zip(cols, w))]
                     + ["".join(str(r[c]).ljust(x) for c, x in zip(cols, w)) for r in rows])
