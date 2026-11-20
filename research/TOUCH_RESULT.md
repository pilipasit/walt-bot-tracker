# Touch odds result

The generator measures 24-hour calendar windows after each hourly close, using the preceding 24 close-to-close absolute log returns as the volatility scale. It records long and short adverse touches separately, includes gaps, and excludes windows without a complete horizon. Asset cards interpolate these frequencies and retain the bell-curve fallback when data is unavailable.

Examples below are long / short measured probabilities, versus the old bell-curve probability.

| Asset | 2 x ATR measured (long / short) | 2 x bell curve | 4 x ATR measured (long / short) | 4 x bell curve |
|---|---:|---:|---:|---:|
| BTC | 72.7% / 72.2% | 74.5% | 51.7% / 50.3% | 51.5% |
| ETH | 72.7% / 71.8% | 74.5% | 51.5% / 50.4% | 51.5% |
| AAPL | 45.7% / 52.2% | 74.5% | 20.7% / 25.0% | 51.5% |
| TSLA | 46.2% / 45.8% | 74.5% | 22.4% / 24.5% | 51.5% |
| SPY | 46.1% / 45.7% | 74.5% | 25.8% / 20.8% | 51.5% |

Browser verification: check the tooltip and long/short scenario cards, and confirm the fallback still displays when the statistics request is unavailable. Candle highs and lows cannot reveal intrabar ordering, and closed-market gaps are observed only when the next candle arrives.

`nPort 8790 served a different copy of the site: the page returned 200 but its market directory did not contain the generated JSON. Verify the updated copy in a real browser.
