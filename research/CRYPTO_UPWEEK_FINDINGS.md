# Crypto "big up-week" momentum (Claude, 20 Nov 2026)

`crypto_upweek.py` uses daily prices for 12 crypto assets (ADA, BCH, BNB, BTC, DOGE, DOT, ETH, LINK, SOL, TRX, XLM, XRP), 2017 to Nov 2026.

## Rule

The thresholds come from `extreme_moves.py`; nothing was fitted.

- When an asset's 7-day move is at least 2× its normal week, upward, buy at the next open and hold 3 (or 10) days.
- Capital is split into 12 equal slots, one per asset, at 1×.
- Costs are 0.14% per round trip plus 0.03%/day funding on longs, Walt's upper common rate.

## Results (total and yearly portfolio return, after costs)

| Rule | Total 2017–Nov 2026 | Max drawdown | Trades | Random entries, same count | 2018 | 2022 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|
| ≥ 2×, hold 3 days | **+300%** | 12.5% | 613 | +13% | +18.7% | −0.3% | +44.4% | −1.8% | −1.9% |
| ≥ 2×, hold 10 days | +524% | 23.6% | 386 | +35% | +5.9% | −7.1% | +87.4% | −1.7% | −12.2% |
| ≥ 3×, hold 10 days | +201% | 11.0% | 170 | +15% | +7.6% | −0.3% | +52.6% | −3.3% | −3.1% |
| Buy and hold, equal weight | +76,165% | 88.3% | — | — | −68.1% | −69.2% | +125% | −23.4% | −37.1% |

Per trade, for ≥ 2× hold 3 days: average +2.84%, median +0.45%, win rate 51%. The profit comes from a minority of big winners.

## Reading

- **This is the strongest candidate in all the research so far:**
  - it beats random entries of the same count by a wide margin;
  - drawdowns were small, because capital sits idle most of the time;
  - it nearly broke even in both crypto bear years, while buy-and-hold lost about 70%.
- **But it has stalled.** 2025 and 2026 are flat to slightly negative. Codex's similar large-move rule showed the same: positive 2022–2025, about zero in 2026. The edge may have faded as crypto matured, or this may be a lull.

## Caveats

- **Survivorship.** The list is today's surviving coins. Coins that rallied and then died (LUNA, FTT) are missing, and they would have produced losing trades.
- **Concentration.** Profits are concentrated in 2017–2021 and 2024 alt-season rallies.
- **Path risk.** Daily marks only; intraday swings and liquidation at leverage are not modelled.

## What happens next

The tracker shows the rule's live status ("Strategy watch" panel). The hourly signal log now records each asset's 7-day move size, so the rule can be scored on genuinely new data.

The honest next step is a **forward paper test** of 2–3 months before any real money.
