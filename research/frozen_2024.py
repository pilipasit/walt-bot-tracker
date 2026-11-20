"""Evaluate previously frozen selective rules on additional Binance 2024 hourly data."""
import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "bot/data/research/archive_2024"
CANDIDATES = [
    dict(name="trend72", hold=72, strength=1, cap=.02, mode="trend"),
    dict(name="strong_move_long24", hold=24, strength=2, cap=.02, mode="long"),
    dict(name="low_vol_long120", hold=120, strength=0, cap=.005, mode="long"),
]


def download(job):
    symbol, month = job
    path = CACHE / f"{symbol}-2024-{month:02}.csv"
    url = (f"https://data.binance.vision/data/spot/monthly/klines/{symbol}USDT/1h/"
           f"{symbol}USDT-1h-2024-{month:02}.zip")
    if not path.exists():
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            path.write_bytes(archive.read(archive.namelist()[0]))
    return dict(symbol=symbol, month=month, url=url,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def evaluate(frame, candidate):
    close = frame.close
    vol = np.log(close).diff().rolling(72).std()
    strength = np.log(close).diff(120) / (vol * np.sqrt(120))
    hold = candidate["hold"]
    rows = []
    for i in range(168, len(frame)-hold-1, hold):
        if abs(strength.iloc[i]) < candidate["strength"] or vol.iloc[i] > candidate["cap"]:
            continue
        if not (frame.ts.iloc[i-168:i+hold+2].diff().dropna() == 3600000).all():
            continue
        entry = frame.iloc[i+1]
        exit_bar = frame.iloc[i+hold+1]
        side = np.sign(strength.iloc[i]) if candidate["mode"] == "trend" else 1
        gross = side * (exit_bar.open/entry.open - 1)*100
        days = (exit_bar.ts-entry.ts)/86400000
        rows.append(dict(entry=pd.to_datetime(entry.ts, unit="ms", utc=True),
                         symbol=frame.attrs["symbol"], gross=gross,
                         net=gross-.24, stress=gross-.34-.03*days))
    return rows


def summarize(rows):
    data = pd.DataFrame(rows)
    weeks = data.entry.dt.strftime("%Y-%W")
    sums = data.groupby(weeks).net.sum().to_numpy()
    counts = data.groupby(weeks).net.count().to_numpy()
    rng = np.random.default_rng(42)
    samples = rng.integers(0, len(sums), size=(5000, len(sums)))
    means = sums[samples].sum(axis=1)/counts[samples].sum(axis=1)
    return dict(n=len(data), mean=float(data.net.mean()), stress=float(data.stress.mean()),
                median=float(data.net.median()), win_rate=float((data.net>0).mean()),
                weekly_block_95ci=np.quantile(means,[.025,.975]).tolist(),
                without_top5=float(data.net.sort_values().iloc[:-5].mean()),
                symbols=data.groupby("symbol").net.agg(["count","mean"]).to_dict("index"))


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = [(s,m) for s in ["BTC","ETH","SOL","XRP"] for m in range(1,13)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        manifest = list(pool.map(download, jobs))
    results = {c["name"]: [] for c in CANDIDATES}
    for symbol in ["BTC","ETH","SOL","XRP"]:
        parts = [pd.read_csv(CACHE/f"{symbol}-2024-{m:02}.csv",header=None).iloc[:,:6] for m in range(1,13)]
        frame = pd.concat(parts,ignore_index=True)
        frame.columns = ["ts","open","high","low","close","volume"]
        assert frame.ts.is_monotonic_increasing and not frame.ts.duplicated().any()
        print(symbol, "missing hourly intervals", int((frame.ts.diff().dropna()!=3600000).sum()))
        frame.attrs["symbol"] = symbol
        for candidate in CANDIDATES:
            results[candidate["name"]].extend(evaluate(frame,candidate))
    report = dict(manifest=manifest, candidates=CANDIDATES,
                  results={k:summarize(v) for k,v in results.items()},
                  limitations="2024 crypto spot proxies, present-day asset selection, previously used rules. No venue funding or liquidation data. Weekly bootstrap retains shared market weeks.")
    (ROOT/"research/frozen_2024_results.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report["results"],indent=2))

if __name__ == "__main__":
    main()
