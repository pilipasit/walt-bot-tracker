"""Build 24-hour adverse-touch frequencies from historical hourly candles."""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'bot/data/research/long_hourly'
OUTPUT = ROOT / 'bot/data/market/touch_stats.json'
K = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15, 20]


def calculate(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    # Keep the last observation at duplicate timestamps.
    by_time = {datetime.fromisoformat(r['']).astimezone(timezone.utc): r for r in rows}
    dates = sorted(by_time)
    times = np.array([d.timestamp() for d in dates])
    def values(key):
        return np.array([float(by_time[d][key] or 'nan') for d in dates])
    close, low, high = values('close'), values('low'), values('high')
    returns = np.abs(np.diff(np.log(close))) * 100
    # Entry is at the close of bar t; ATR includes the 24 returns ending there.
    atr = np.full(len(close), np.nan)
    atr[24:] = np.convolve(returns, np.ones(24) / 24, mode='valid')
    ends = np.searchsorted(times, times + 24 * 3600, side='right')
    long, short = [], []
    for i, end in enumerate(ends):
        if times[i] + 24 * 3600 > times[-1] or not np.isfinite(atr[i]) or atr[i] <= 0 or not np.isfinite(close[i]) or close[i] <= 0:
            continue
        lows, highs = low[i + 1:end], high[i + 1:end]
        if not (np.all(np.isfinite(lows) & (lows > 0)) and np.all(np.isfinite(highs) & (highs > 0))):
            continue
        # An empty window (e.g. over a weekend) has no observed touch.
        long.append(max(0, (1 - lows.min() / close[i]) * 100 / atr[i]) if len(lows) else 0)
        short.append(max(0, (highs.max() / close[i] - 1) * 100 / atr[i]) if len(highs) else 0)
    if not long:
        raise ValueError(f'No usable windows in {path}')
    return {'from': dates[0].isoformat(), 'to': dates[-1].isoformat(),
            'bars': len(dates), 'windows': len(long),
            'long': [round(float(np.mean(np.asarray(long) >= k)), 3) for k in K],
            'short': [round(float(np.mean(np.asarray(short) >= k)), 3) for k in K]}


def main():
    assets = {path.stem: calculate(path) for path in sorted(SOURCE.glob('*.csv'))}
    if not assets:
        raise ValueError(f'No CSV files in {SOURCE}')
    result = {'generated_at': datetime.now(timezone.utc).isoformat(), 'k': K, 'assets': assets}
    OUTPUT.write_text(json.dumps(result, separators=(',', ':')) + '\n', encoding='utf-8')
    print(f'Wrote {len(assets)} assets to {OUTPUT} ({OUTPUT.stat().st_size} bytes)')
    if OUTPUT.stat().st_size >= 40000:
        raise ValueError('Output exceeds 40 KB')


if __name__ == '__main__':
    main()
