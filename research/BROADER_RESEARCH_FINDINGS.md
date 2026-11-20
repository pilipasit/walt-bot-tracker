# Broader strategy research findings

## Return and risk definitions

Earlier +3.91%, +5.41%, +19.48%, +11.33% returns were total yearly portfolio returns under fixed quarter-of-starting-capital notional allocations, across 82, 83, 72 and 78 entries. They were long-only. Maximum drawdown was the largest fall from a previous portfolio equity peak, using hourly open marks. It differs from the final annual loss.

The daily experiments below use positions spread across 39 presently listed assets with available cached daily histories. Signals are calculated at prior closes and deferred to available opens. Assets are inverse-volatility weighted, with each capped at 10% of target gross exposure. Position accounting tracks contract quantities, mark-to-market, fees, funding, and changes in exposures. It assumes linear contracts. Entry counts count new or reversed positions, not all resize transactions.

Fees/slippage total 0.12% per notional traded per side. An adverse funding scenario charges 0.03% daily on gross exposure. These assumed charges are not reconstructed historical Walt funding. Margin stress uses hypothetical 5% gross maintenance and simultaneous adverse daily extremes, so its breaches are conservative scenario events rather than reconstructed venue liquidations. Cash after fees can leave realized exposure slightly above target.

## Experiments at 1x target gross exposure

| Strategy | Total return 2015–Nov 2026 | Conservative drawdown | Entries |
|---|---:|---:|---:|
| trend126_long | 109.81% | 28.52% | 2059 |
| trend126_ls | -54.94% | 55.88% | 4122 |
| breakout55_ls | -65.26% | 66.60% | 1358 |
| strong_candle_ls | -93.42% | 94.01% | 15638 |
| failed_breakout_ls | -93.49% | 93.59% | 6311 |
| compression_breakout_ls | 21.10% | 27.70% | 1197 |
| strong_candle_long | 23.32% | 60.17% | 6346 |
| trend_breakout_mix | 103.58% | 29.76% | 2600 |
| trend_breakout_confirm | 15.65% | 32.33% | 1041 |

The strongest total return was the six-month long-only trend. The quiet-period breakout was the strongest candle-based long/short candidate. Combining them did not improve on the trend strategy. Five-fold target exposure magnified the long-only trend drawdown to 85.5%.

## Research scope and evidence

The research compared time-series trends, breakouts, cross-sectional rotation, short-term reversal, candle strength, failed breakouts, quiet-period breakouts, regime selection, and combinations. Earlier machine-learning regressors and shallow decision rules were also tested. Published research motivated slower time-series momentum and transaction-cost-sensitive reversals. References include https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data and https://papers.ssrn.com/sol3/papers.cfm?abstract_id=298092 .

## Limitations and conclusion

Historical candidates with positive full-period results were found. No tested rule established dependable positive yearly performance or future profitability. The broad daily search is exploratory on already available data, with current-universe survivorship and proxy-market differences. Cache source acquisition and completeness are not fully reconstructed. Rates/yield proxies were excluded. Futures rolls, actual spreads, funding, leverage caps, contract rounding and per-contract liquidation require venue data and further implementation. Daily trade-time alignment across asset classes is approximate. Annual reports omit the final close fee from the final year while total return includes it. Portfolio stress drawdown is distinct from a precise historical intraday drawdown.

These results support a shortlist for forward paper validation, while deployment profit remains unverified. Research completion means the requested hypotheses, long/short sides, allocation structures and leverage scenarios were investigated and documented, with both positive and rejected results retained.