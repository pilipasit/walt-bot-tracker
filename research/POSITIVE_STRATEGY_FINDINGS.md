# Positive strategy research result

The strongest surviving historical candidate is the long-only large-move rule filtered by 30-day direction agreement. These results describe fixed initial notional, proxy-price backtests. They do not establish a future profit expectation or actual Walt performance.

## Exact rule

For BTC, ETH, SOL and XRP, use completed hourly bars. Compute the standard deviation of the last 72 hourly log returns. Divide the 120-hour log price change by this standard deviation times sqrt(120). Enter long at the following open when the absolute value is at least 2, hourly volatility is at most 0.02, and the sign of the 120-hour change agrees with the 720-hour change. Hold 24 hours. Wait until the exit before another entry in that symbol. Require uninterrupted hourly data through the feature window and outcome window for this historical audit. Outcome-window completeness is a retrospective eligibility filter and can bias results around outages.

Each asset uses a fixed quarter of starting capital. Assumed costs are 0.34 percentage points per round trip plus 0.03 per calendar day. This is a constant-notional accounting experiment, with no cash availability enforcement, reinvestment, leverage liquidation or venue-specific historical funding.

| Year | Trades | Portfolio return | Max hourly-open drawdown | Mean trade |
|---|---:|---:|---:|---:|
| 2022 | 82 | 3.913% | 15.547% | 0.191% |
| 2023 | 83 | 5.411% | 11.970% | 0.261% |
| 2024 | 72 | 19.476% | 12.315% | 1.082% |
| 2025 | 78 | 11.327% | 6.752% | 0.581% |
| 2026 | 68 | 0.017% | 8.240% | 0.001% |

2026 covers January through October. Annual simulations restart feature warmup, capital and position state. They must not be represented as one continuous investable equity curve.

## Selection and verification limits

The regime filter was selected after examining 12 configurations on 2022–2024. The parameters were then frozen for additional 2025 data. Earlier experiments examined many configurations and also used some 2026 Yahoo data. The total research search is broader than the final 12 configurations. These are exploratory results with selection bias.

A one-hour execution delay remained positive in 2022–2025. Removing each year's five best trades made 2022, 2023 and 2026 negative. Annual weekly-bootstrap intervals included losses except in 2025. Drawdown uses hourly opens and understates intrahour extremes.

Matched Yahoo and Binance closes usually differed by about 0.065–0.071%, but different source gaps and prices changed trade eligibility. The strict completeness filter and cooldown can propagate entry differences. Matched source results cannot be treated as interchangeable.

## Reproduction

Run python research/regime_portfolio.py for 2022–2025 and python research/regime_2026_archive.py for January–October 2026 after collecting the archive inputs with the frozen archive scripts. JSON results and archive source hashes accompany the experiments.

The research found historically positive candidates and rejected several alternatives. Practical deployment requires a cash-constrained simulator, executable Walt prices and funding history, point-in-time availability, and forward paper validation. Historical positivity cannot guarantee future results.