"""Compare fixed and learned policies on nonoverlapping trend transitions."""
import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))
from waltbot.backtest import market_signal

HOUR = 3600000
FEE = 0.14
OCT = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000
NOV = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp() * 1000


def opportunities(directory):
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text())
        bars = data["candles"]
        closes = [bar[4] for bar in bars]
        previous = None
        available = 0
        for i in range(48, len(bars) - 9):
            signal = market_signal(closes[max(0, i-167):i+1], min(i, 167))
            trend = signal["trend"]
            changed = previous is not None and trend != previous
            previous = trend
            entry, exit_bar = bars[i+1], bars[i+9]
            if not changed or trend == "sideways" or entry[0] < available:
                continue
            if entry[0] - bars[i][0] != HOUR:
                continue
            available = exit_bar[0]
            side = 1 if trend == "up" else -1
            gross = side * (exit_bar[1] / entry[1] - 1) * 100
            key = (data["group"], trend, bool(signal["aligned"]))
            yield entry[0], exit_bar[0], key, gross


def stats(rows, policy):
    returns = [policy(key) * gross - FEE for _, _, key, gross in rows if policy(key)]
    return dict(n=len(returns), mean=round(statistics.mean(returns), 4) if returns else None,
                per_opportunity=round(sum(returns)/len(rows), 4))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    rows = list(opportunities(args.directory))
    train = [r for r in rows if r[1] < OCT]
    validation = [r for r in rows if r[0] >= OCT and r[1] < NOV]
    test = [r for r in rows if r[0] >= NOV]
    cells = defaultdict(list)
    for _, _, key, gross in train:
        cells[key].append(gross)
    # Shrink noisy cell means toward zero with twenty zero-return observations.
    estimates = {k: sum(v)/(len(v)+20) for k, v in cells.items()}

    def learned(key):
        estimate = estimates.get(key, 0)
        if abs(estimate) <= FEE:
            return 0
        return 1 if estimate > 0 else -1

    policies = dict(follow=lambda k: 1, reverse=lambda k: -1,
                    follow_aligned_reverse_other=lambda k: 1 if k[2] else -1,
                    learned=learned)
    scores = {name: dict(validation=stats(validation, policy), test=stats(test, policy))
              for name, policy in policies.items()}
    winner = max(scores, key=lambda n: scores[n]["validation"]["per_opportunity"])
    print(json.dumps(dict(counts=list(map(len,(train,validation,test))), scores=scores,
                          validation_winner=winner), indent=2))


if __name__ == "__main__":
    main()
