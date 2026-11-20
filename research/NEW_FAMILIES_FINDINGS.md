# New strategy families (Claude, 20 Nov 2026)

> **Correction (20 Nov 2026, later):** Walt's `funding_rate_hourly` is a **percent** per hour, not a fraction. The owner's
> real positions paid or received only about $1–2 a day (e.g. a $36k gold short), which matches the percent reading. So Walt
> funding is about 100× smaller than stated below: roughly 0.001–0.01% a day. Consequences: the funding-carry lead is
> **dead**, and funding does **not** sink long holds on Walt. With near-zero funding, the six-month long-only trend made +453%
> (drawdown 20%) vs +402% for simply holding the same assets (`hold_baseline.py`, funding 0 rows), so the trend filter
> still adds little over holding. Sections below that rely on large funding are superseded.


These tests go beyond the price-only rules. For every test, the rule's thresholds and side were chosen on the earlier period only. The rule was then run once, unchanged, on the later period. Costs are 0.14% per round trip; funding, where included, is 0.08%/day paid by longs and received by shorts.

| Family | Script | Earlier period | Later period (frozen rule) | Verdict |
|---|---|---|---|---|
| Hour-of-day blocks, 13 cryptos | `calendar_leadlag.py` | positive | −0.08%/trade, 4,658 trades | no |
| Day-of-week, 13 cryptos | same | +0.95%/trade | −0.28%/trade | no (overfit) |
| Weekend long/short | same | — | −0.18% / −0.10% | no |
| BTC leads alts (catch-up after a 2σ BTC move) | same | +0.08% | −0.08%, t −1.2 | no |
| Stock news gaps, follow vs fade (22 stocks) | `news_gap_drift.py` | best config chosen | ≈0 for the chosen config | no |
| Yields / dollar / Nasdaq → gold | `macro_gold.py` | | no setting with t > 2 | no |
| Crypto Fear & Greed (216 configs) | `sent_fng_wiki.py` | e.g. +1.9%/trade, t 3.0 | +0.3%, t 0.7; 16–28% of configs positive | no |
| GDELT news tone / volume (54 configs) | `sent_gdelt.py` | t ≈ 2.2 | t ≈ 0.6–0.8 | no |

The sentiment agent was stopped early to stay inside the usage limit. Its partial results are in `sent_*_results.json`.

## The one weak lead from the news-gap test

Following a large overnight stock gap for 20 days beat a random 20-day position in the later period, by about 1.5% per trade (t ≈ 2.4 for gap-ups). This was not the configuration chosen on the earlier period, so it is a hypothesis, not a result. Post-earnings drift is a documented anomaly, which gives it some credibility.

On Walt, stock longs pay 0.08–0.6% a day in funding, so this would only make sense in a spot account, such as IBKR.

## The structural lead: funding carry

Walt funding comes in fixed steps. The common values are 32e-6 and 96e-6 per hour, which is 0.077% and 0.23% a day, paid by longs to shorts.

- Of 104 assets with positive funding on 14–16 Nov, 87 were still positive on 20 Nov. The rank correlation between the two dates was 0.37.
- A short on Walt hedged by a long of the same asset elsewhere earns that funding without betting on direction. Gold is the safest candidate: since 2018, gold never rose more than 29% in any 30 days, so a 2× Walt short with monthly rebalancing would not have been liquidated. BTC and ETH have risen over 100% in 30 days.
- The cloud job now logs Walt funding every hour (`bot/data/market/funding_history.jsonl`). It also logs the page's signals every hour (`signal_log.jsonl`) for a forward test.

**Open checks before this is a strategy:**
1. Confirm the funding unit from the real gold short. Telegram's "funding" should show about +$98 per day.
2. Collect 2–4 weeks of funding history.
3. Measure how far Walt's price drifts from spot.
4. Account for counterparty risk.

## Ideas not yet tested

These need more data or budget:
- earnings-date drift using real earnings dates (IBKR earnings insights);
- options-implied volatility vs realised volatility;
- on-chain flows;
- analyst revisions;
- LLM-scored headlines;
- scoring the forward signal log after about 2 weeks.

Use Codex after 15:58 or the Gemini API (public text only) for bulk work.
