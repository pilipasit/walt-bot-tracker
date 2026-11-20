"""Monthly stock selection using price momentum and frequency of positive sessions.

Walt's current US stock list and fully funded SPY benchmark. Portfolio holds monthly
linear positions; full isolated collateral is forfeited at a maintenance breach.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from index_gold_benchmark import history

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
from waltbot.markets import UNIVERSE

START = "2015-01-05"
END = "2026-10-30"
SOURCE = Path(r"C:\Projects\walt-trading-bot\data\research\daily")
SIDECOST = .0012
FUNDING = .0003


def simulate(data, sessions, rule, dynamic, side_cost=SIDECOST, daily_funding=FUNDING):
    positions = {}
    cash = 1.
    curve = []
    rows = []
    last = sessions[0]
    month = None
    for day in sessions:
        monthly_open = day.month != month
        for symbol, trade in list(positions.items()):
            frame = data[symbol]
            bar = frame.loc[day]
            trade["cost"] += (day-last).days * trade["qty"] * trade["last"] * daily_funding
            # Old positions exit at this open on rebalance days.
            low = bar.open if monthly_open else bar.low
            value = trade["margin"] + trade["qty"] * (low-trade["open"]) - trade["cost"]
            breached = value <= trade["qty"] * low * trade["mm"]
            if breached or monthly_open:
                residual = 0. if breached else max(0.,value-trade["qty"]*bar.open*side_cost)
                cash += residual
                rows.append([symbol,day,trade["lev"],residual-trade["margin"],breached])
                del positions[symbol]
        if monthly_open:
            # Symbols without a continuous set of bars over this run are excluded on
            # data quality grounds when assembling the named, current-stock universe.
            candidates = pd.DataFrame({s:f.loc[day,["mom","path","vol"]] for s,f in data.items()}).T.dropna()
            if rule == "unfiltered":
                chosen = candidates.index
                slot = cash / len(chosen) if len(chosen) else 0.
            else:
                positive = candidates[candidates.mom > 0].copy()
                score = positive.mom.rank(pct=True)
                if rule == "persistent":
                    score = .5*score + .5*positive.path.rank(pct=True)
                chosen = score.nlargest(5).index
                slot = cash/5
            for s in chosen:
                f = data[s]
                bar = f.loc[day]
                lev = min(f.attrs["cap"],max(1,min(5,int(.04/bar.vol)))) if dynamic else 1
                qty = slot*lev/bar.open
                trade = dict(qty=qty,open=bar.open,last=bar.open,margin=slot,lev=lev,
                             mm=f.attrs["mm"],cost=slot*lev*side_cost)
                cash -= slot
                if slot+qty*(bar.low-bar.open)-trade["cost"] <= qty*bar.low*trade["mm"]:
                    rows.append([s,day,lev,-slot,True])
                else:
                    positions[s] = trade
        equity = cash
        for s,t in positions.items():
            t["last"] = data[s].at[day,"close"]
            collateral = t["margin"]+t["qty"]*(t["last"]-t["open"])-t["cost"]
            equity += collateral
            if day == sessions[-1]:
                exit_fee = t["qty"]*t["last"]*side_cost
                equity -= exit_fee
                rows.append([s,day,t["lev"],collateral-t["margin"]-exit_fee,False])
        assert equity>0 and cash>=-1e-10
        curve.append(equity)
        last,month = day,day.month
    return pd.Series(curve,index=sessions),rows


def summary(series):
    def period(s,base):
        e=np.r_[base,s]
        return dict(total_pct=100*(e[-1]/base-1),
                    dd_pct=100*(1-e/np.maximum.accumulate(e)).max())
    split = series.loc[:"2021"].iloc[-1]
    return dict(full=period(series,1.),later=period(series.loc["2022":],split),
                years={int(y):period(g,series.shift().fillna(1.).loc[g.index[0]])["total_pct"]
                       for y,g in series.groupby(series.index.year)})


def main():
    spy = history("SPY")
    spy.index = spy.index.tz_localize(None)
    days = spy.loc[START:END].index
    specs = {c["base_asset"]:c for c in json.loads((ROOT/"bot/data/contracts_raw.json").read_text())["contracts"]}
    data={}
    for s,group,_ in UNIVERSE:
        path = SOURCE/f"{s}.csv"
        if group!="Stocks" or not path.exists():
            continue
        f=pd.read_csv(path,index_col=0,parse_dates=True).sort_index()
        if f.index.min() >= pd.Timestamp("2014-01-01") or not days.isin(f.index).all():
            continue
        r=f.close.pct_change()
        f["mom"]=f.close.shift(21)/f.close.shift(252)-1
        f["path"]=np.sign(r).rolling(231).mean().shift(21)
        f["vol"]=r.rolling(60).std()
        f[["mom","path","vol"]]=f[["mom","path","vol"]].shift(1)
        f.attrs.update(cap=int(specs[s]["max_leverage"]),mm=float(specs[s]["margin"]["maintenance_margin_fraction"])/10000)
        data[s]=f
    first=spy.loc[days[0]]
    benchmark=spy.loc[days,"adjusted"]/(first.adjusted*first.open/first.close)
    out=dict(dates=[START,END],stocks=list(data),benchmark=summary(benchmark),runs={})
    for rule, dynamic in [("unfiltered",False),("momentum",False),("persistent",False),("persistent",True)]:
        curve,ledger=simulate(data,days,rule,dynamic)
        key=rule+("_variable" if dynamic else "")
        out["runs"][key]=dict(**summary(curve),trades=len(ledger),liquidations=int(sum(x[4] for x in ledger)),
                              max_leverage=max(x[2] for x in ledger))
    out["sensitivity"] = {}
    for omit in ["NVDA", "TSLA", "MSTR"]:
        reduced = {s:f for s,f in data.items() if s!=omit}
        curve,_ = simulate(reduced, days, "momentum", False)
        out["sensitivity"]["exclude_"+omit] = summary(curve)
    stressed,_ = simulate(data, days, "momentum", False, side_cost=.0024, daily_funding=.0006)
    out["sensitivity"]["double_costs"] = summary(stressed)
    (ROOT/"research/persistent_stock_results.json").write_text(json.dumps(out,indent=2))
    print(out["stocks"])
    for name,v in {"SPY":out["benchmark"],**out["runs"]}.items():print(name,v["full"],v["later"])

if __name__=="__main__":
    main()


