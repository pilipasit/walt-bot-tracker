# Tracker backtest research

Snapshot examined: 46 proxy-price hourly datasets spanning roughly 20 August–20 November 2026. These files were supplied separately and are not included in this repository. The corrected published snapshot is descriptive, not an actual trading record. It assumes one-hour bars and ideal close entries; partial Yahoo bars, Walt/proxy basis, spread, slippage, funding, and liquidation remain unresolved.

The corrected published run produced 1,765 completed trend-transition trades averaging -0.1903% of entry notional after a fixed 0.14 percentage-point round-trip fee. On 1,759 paired entries, the rule averaged -0.1802% and an equally weighted long/short baseline -0.1342%. Aligned entries averaged +0.0033% versus -0.1404% on 232 paired entries. Correlated instruments, differing volatility and the short sample preclude treating these as independent evidence of a durable edge.

## Exploratory chronological check

Run from the repository root with `python research/tracker_validation.py PATH_TO_CANDLE_DIRECTORY`. The script uses the page signal, 168-bar warmup, trend transitions, next-bar open entry where the next bar follows within an hour, a 72-hour horizon, adverse opening-gap stop fills, and a fixed 0.14% round-trip fee. It divides entries at 20 October 2026 UTC and excludes training trades whose 72-hour horizon reaches the split. The implementation is separate from the published simulator and has approximate one-hour/session assumptions; it is a sensitivity check, not a replication of the displayed result.

| Selection | Earlier count | Earlier average | Later count | Later average |
| --- | ---: | ---: | ---: | ---: |
| All | 871 | -0.167% | 541 | -0.139% |
| Aligned | 100 | -0.340% | 89 | +0.583% |

The later aligned result included 45 crypto trades averaging +1.458% and 13 stock trades averaging -1.350%. The earlier and later aligned random-direction averages were -0.197% and -0.003%. The split is exploratory: the full data was already reviewed, instruments and trades are correlated, and the 168-bar warmup removes much of the stock sample. It is not an untouched holdout. Do not enable alignment as an automatic trading filter based on these numbers.

## Next research and engineering priorities

1. Capture source, acquisition time, bar open and close times, market calendar, completeness and proxy mapping for each dataset; retain reproducible historical inputs where licensing permits. Detect partial bars and exclude the current unfinished bar.
2. Replay the page's actual ten-minute signal snapshots and Walt prices, use an executable next quote, and model spread, slippage, funding, venue fees, gap handling and liquidation. Compare with the current idealized close-entry model and show cost sensitivities. Historical Walt execution and funding data are needed; current funding is not a historical substitute.
3. Pre-register a small set of hypotheses: alignment, pullback entries, signal age and true-range exits. Evaluate against no trade, random direction, long-only and short-only on matched entries. Use trade-level returns, uncertainty that accounts for temporal and cross-asset dependence, and exposure/position constraints.
4. Acquire longer history across multiple regimes and use rolling walk-forward periods with an embargo at least as long as the outcome horizon. Keep a final untouched holdout; record every configuration tried. An independent forward paper test using recorded live signals is needed before considering deployment.

Backtest selection can produce misleading apparent performance even when candidate rules lack a real edge; see Bailey et al., [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253). Longer-horizon [time-series momentum research](https://www.aqr.com/insights/research/journal-article/time-series-momentum) does not establish that this hourly proxy-based rule works.
