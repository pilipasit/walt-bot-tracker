import json
import tempfile
import unittest
from pathlib import Path

from waltbot.backtest import market_signal, market_trade, run

HOUR = 3_600_000


def rising(n):
    return [[i * HOUR, 100 * 1.002 ** i, 100 * 1.002 ** (i + 1),
             100 * 1.002 ** (i - 1), 100 * 1.002 ** i, 0] for i in range(n)]


class MarketBacktestTests(unittest.TestCase):
    def test_rising_series_longs_mostly_hit_target(self):
        candles = rising(200)
        closes = [c[4] for c in candles]
        trades = []
        for t in range(47, 120):
            signal = market_signal(closes, t)
            self.assertEqual(signal['trend'], 'up')
            trades.append(market_trade(candles, t, 1, 2.5 * signal['atr_pct_h'] + signal['band_pct']))
        self.assertGreater(sum(x['outcome'] == 'tp' for x in trades), len(trades) * .9)

    def test_future_candle_cannot_change_signal(self):
        candles = rising(200)
        closes = [c[4] for c in candles]
        before = market_signal(closes, 120)
        closes[121:] = [x * 10 for x in closes[121:]]
        self.assertEqual(before, market_signal(closes, 120))

    def test_same_candle_stop_first(self):
        candles = [[0, 100, 100, 100, 100, 0], [HOUR, 100, 103, 98, 101, 0]]
        self.assertEqual(market_trade(candles, 0, 1, 1)['outcome'], 'stop')
        self.assertEqual(market_trade(candles, 0, -1, 1)['outcome'], 'stop')

    def test_episode_count_and_gap(self):
        candles = rising(150)
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'TEST.json').write_text(json.dumps({'symbol': 'TEST', 'group': 'test', 'candles': candles}))
            result = run(temp)['overall']['all']
            self.assertEqual(result['signal_hours'], 103)
            self.assertEqual(result['episodes'], 1)
            for c in candles[100:]:
                c[0] += HOUR
            Path(temp, 'TEST.json').write_text(json.dumps({'symbol': 'TEST', 'group': 'test', 'candles': candles}))
            self.assertEqual(run(temp)['overall']['all']['episodes'], 2)

    def test_censored_and_timeout(self):
        candles = [[i * HOUR, 100, 100, 100, 100, 0] for i in range(73)]
        self.assertIsNone(market_trade(candles[:-1], 0, 1, 1))
        self.assertEqual(market_trade(candles, 0, 1, 1)['outcome'], 'timeout')


if __name__ == '__main__':
    unittest.main()
