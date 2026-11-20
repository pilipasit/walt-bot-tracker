"""Position-level daily audit. Intraday margin checks use conservative simultaneous extremes."""
import json
import runpy
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
context=runpy.run_path(str(ROOT/"research/broad_daily.py"))
mapping=context["mapping"]
index=context["index"]
symbols=list(mapping)
frames={name:frame.reindex(index) for name,(_,frame) in mapping.items()}
opening=np.column_stack([frames[s].open.to_numpy() for s in symbols])
high=np.column_stack([frames[s].high.to_numpy() for s in symbols])
low=np.column_stack([frames[s].low.to_numpy() for s in symbols])
marked=pd.DataFrame(opening).ffill().to_numpy()
vol=context["vol"].reindex(columns=symbols)

candle_names = ["strong_candle_ls", "failed_breakout_ls", "compression_breakout_ls", "strong_candle_long"]
for name in candle_names:
    context["signals"][name] = {}
for symbol, (_, frame) in mapping.items():
    span = (frame.high - frame.low).replace(0, np.nan)
    body = (frame.close - frame.open) / span
    location = (frame.close - frame.low) / span
    daily_range = span / frame.close
    quiet = daily_range.rolling(5).mean() < daily_range.rolling(60).mean() * .7
    prior_high = frame.high.rolling(20).max().shift(1)
    prior_low = frame.low.rolling(20).min().shift(1)
    strong = pd.Series(np.where((body > .6) & (location > .8), 1,
                      np.where((body < -.6) & (location < .2), -1, 0)), index=frame.index)
    failed = pd.Series(np.where((frame.low < prior_low) & (frame.close > prior_low), 1,
                      np.where((frame.high > prior_high) & (frame.close < prior_high), -1, 0)), index=frame.index)
    breakout = pd.Series(np.where(quiet.shift(1, fill_value=False) & (frame.close > prior_high), 1,
                        np.where(quiet.shift(1, fill_value=False) & (frame.close < prior_low), -1, 0)), index=frame.index)
    for name, value in [("strong_candle_ls", strong), ("strong_candle_long", strong.clip(lower=0)),
                        ("failed_breakout_ls", failed), ("compression_breakout_ls", breakout)]:
        # A trigger remains eligible for five observed bars.
        signal = value.replace(0, np.nan).ffill(limit=4).fillna(0)
        context["signals"][name][symbol] = signal.reindex(index).ffill(limit=4)

results=[]
for name in candle_names:
 signal=pd.DataFrame(context["signals"][name]).reindex(columns=symbols)
 risk=signal.div(vol).replace([np.inf,-np.inf],np.nan).fillna(0)
 weight=risk.div(risk.abs().sum(axis=1).replace(0,np.nan),axis=0).fillna(0)
 # Bound any single asset to 10% of unit gross exposure, leaving unused capacity in cash.
 weight=weight.clip(-.1,.1).shift(1).fillna(0).to_numpy()
 for leverage in [.5,1,2]:
  quantity=np.zeros(len(symbols)); previous=np.nan_to_num(marked[0],nan=0)
  equity=1.; peak=1.; dd=0.; fees=0.; funding=0.; entries=0; exits=0; curve=[]; liquidation=None; maxgross=0.
  for i,date in enumerate(index):
   if date<pd.Timestamp("2015-01-01"):
    previous=np.nan_to_num(marked[i],nan=0);continue
   price=np.nan_to_num(marked[i],nan=0)
   equity+=float(np.dot(quantity,price-previous))
   charge=.0003*float(np.dot(np.abs(quantity),previous))
   equity-=charge;funding+=charge
   if equity<=0:
    equity=0;liquidation=str(date);break
   tradable=np.isfinite(opening[i])&(opening[i]>0)
   desired=quantity.copy()
   desired[tradable]=weight[i,tradable]*leverage*equity/price[tradable]
   frozen=float(np.dot(np.abs(desired[~tradable]),price[~tradable]))
   available=max(0,leverage*equity-frozen)
   needed=float(np.dot(np.abs(desired[tradable]),price[tradable]))
   if needed>available and needed:desired[tradable]*=available/needed
   cost=.0012*float(np.dot(np.abs(desired-quantity),price))
   fees+=cost;equity-=cost
   entries+=int(np.sum((desired!=0)&((quantity==0)|(np.sign(desired)!=np.sign(quantity)))))
   exits+=int(np.sum((quantity!=0)&((desired==0)|(np.sign(desired)!=np.sign(quantity)))))
   quantity=desired
   adverse=np.where(quantity>=0,low[i],high[i])
   adverse=np.where(np.isfinite(adverse),adverse,price)
   worst=equity+float(np.dot(quantity,adverse-price))
   gross=float(np.dot(np.abs(quantity),price))
   maxgross=max(maxgross,gross/max(equity,1e-12))
   peak=max(peak,equity);dd=max(dd,1-worst/peak)
   if worst<=.05*gross and gross>0:
    equity=max(0,worst-.0012*float(np.dot(np.abs(quantity),adverse)))
    liquidation=str(date)
    quantity[:]=0
    curve.append((str(date),equity))
    break
   curve.append((str(date),equity));previous=price
  residual=.0012*float(np.dot(np.abs(quantity),previous))
  equity=max(0,equity-residual);fees+=residual
  series=pd.Series({pd.Timestamp(t):value for t,value in curve})
  annual={}
  baseline=1
  for year,part in series.groupby(series.index.year):
   annual[str(year)]=float((part.iloc[-1]/baseline-1)*100) if baseline else None
   baseline=part.iloc[-1]
  results.append(dict(rule=name,target_gross=leverage,return_pct=(equity-1)*100,
                      conservative_drawdown_pct=min(100,dd*100),margin_stress_breach=liquidation,
                      entries=entries,exits=exits,fees_pct_initial=fees*100,
                      funding_pct_initial=funding*100,max_realized_gross=maxgross,annual=annual))
report=dict(results=results,assumptions="Unit initial equity, linear contracts, 0.12% per notional traded, adverse 0.03% daily funding on gross. Signals known at previous close. Prices proxy daily OHLC. Simultaneous adverse highs/lows are a conservative stress bound, not actual historical margin liquidation. 5% gross maintenance is hypothetical. Present-day universe and gaps remain limitations.")
(ROOT/"research/candle_portfolio_results.json").write_text(json.dumps(report,indent=2))
for row in results:print(row["rule"],row["target_gross"],round(row["return_pct"],1),round(row["conservative_drawdown_pct"],1),row["margin_stress_breach"],row["entries"])

