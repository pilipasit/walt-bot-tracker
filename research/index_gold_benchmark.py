"""Monthly risk budgets for a stock-index/gold portfolio versus dividend-adjusted SPY."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "bot/data/research/index_gold_benchmark"
SYMBOLS = ["SPY", "QQQ", "GLD"]
BUDGET = np.array([0.45, 0.40, 0.15])
FEE = 0.0012
DAILY_FUNDING = 0.0003


def history(symbol):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}-2013-20261119.csv"
    if not path.exists():
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"period1": 1356998400, "period2": 1795132800, "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response.raise_for_status()
        raw = response.json()["chart"]["result"][0]
        frame = pd.DataFrame(raw["indicators"]["quote"][0],
                             index=pd.to_datetime(raw["timestamp"], unit="s", utc=True).normalize())
        frame["adjusted"] = raw["indicators"]["adjclose"][0]["adjclose"]
        frame.dropna().to_csv(path)
    return pd.read_csv(path, index_col=0, parse_dates=True)


def metrics(equity):
    # Curves include a starting valuation at the initial open.
    multiple = equity.iloc[-1] / equity.iloc[0]
    years = (equity.index[-1]-equity.index[0]).days/365.25
    return {"total_pct": 100*(multiple-1), "cagr_pct": 100*(multiple**(1/years)-1),
            "close_dd_pct": 100*(1-equity/equity.cummax()).max()}


def main():
    frames = {s:history(s) for s in SYMBOLS}
    close = pd.DataFrame({s:f.close for s,f in frames.items()}).dropna()
    opening = pd.DataFrame({s:f.open for s,f in frames.items()}).loc[close.index]
    adjusted = pd.DataFrame({s:f.adjusted for s,f in frames.items()}).loc[close.index]
    vol = close.pct_change().rolling(63).std()*np.sqrt(252)
    positive = close > close.shift(126)
    dates = close.loc["2015":].index
    start = dates[0]-pd.Timedelta(hours=1)
    first_open_adj = opening.loc[dates[0],"SPY"] * adjusted.loc[dates[0],"SPY"]/close.loc[dates[0],"SPY"]
    benchmark = pd.concat([pd.Series([1.], index=[start]), adjusted.loc[dates,"SPY"]/first_open_adj])
    curves = {"SPY dividend adjusted": benchmark}
    stats = {}
    for mode in ["basket price only","trend selective","risk scaled long","risk scaled long short"]:
        quantity = np.zeros(3)
        equity = 1.
        marks = opening.loc[dates[0]].values
        last_day = dates[0]
        values = [1.]
        entries = 0
        for date in dates:
            i = close.index.get_loc(date)
            op = opening.loc[date].values
            equity += np.dot(quantity, op-marks)
            equity -= DAILY_FUNDING*np.dot(abs(quantity),marks)*(date-last_day).days
            if date == dates[0] or date.month != last_day.month:
                bull = positive.iloc[i-1].values
                allocation = BUDGET.copy()
                if mode != "basket price only":
                    side = np.where(bull,1., -1. if mode.endswith("short") else 0.)
                    allocation *= side
                if mode.startswith("risk scaled"):
                    allocation *= np.clip(.25/vol.iloc[i-1].values, .25, 3.)
                target = allocation * equity/op
                traded = np.dot(abs(target-quantity),op)
                entries += int(((target!=0)&(np.sign(target)!=np.sign(quantity))).sum())
                equity -= traded*FEE
                quantity = target
            marks = close.loc[date].values
            equity += np.dot(quantity, marks-op)
            assert equity > 0
            values.append(equity)
            last_day = date
        values[-1] -= np.dot(abs(quantity),marks)*FEE
        curves[mode] = pd.Series(values,index=[start,*dates])
        stats[mode] = entries
    out={}
    for name,curve in curves.items():
        earlier = curve.loc[:"2021"]
        later = curve.loc[earlier.index[-1]:]
        annual = curve.groupby(curve.index.year).last()
        year_ret = annual.pct_change()
        year_ret.iloc[0] = annual.iloc[0]-1
        out[name] = dict(full=metrics(curve), through_2021=metrics(earlier), since_2022=metrics(later),
                         annual_pct=(100*year_ret).to_dict(),entries=stats.get(name))
    path = ROOT/"research/index_gold_benchmark.json"
    path.write_text(json.dumps(out,indent=2))
    for name,r in out.items():
        print(name, r["full"], r["since_2022"])

if __name__ == "__main__":
    main()
