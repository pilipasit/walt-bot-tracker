# Review of the Codex research (20 Nov 2026, Claude)

> **Correction 2 (20 Nov 2026, 15:30, supersedes the one below):** measured on the owner's MSTR short, Walt's
> `funding_rate_hourly` is the **8-hour rate as a fraction, paid hourly at 1/8**. Real funding is therefore about 0.01%/day
> at the common rate (3.5%/year) and 0.03%/day at the upper rate (10.5%/year): 12.5x more than the "percent" reading
> below, and 8x less than the original reading. Funding carry stays dead (3.5–10% a year minus hedge costs and risk).
> Long holds pay a real but modest drag: the six-month long trend made +453% at zero funding and +50% at 0.03%/day
> (`hold_baseline.py`). That bounds the true Walt result. Holding the same assets in a spot account avoids it.

> **Correction (20 Nov 2026, later):** Walt's `funding_rate_hourly` is a **percent** per hour, not a fraction. The owner's
> real positions paid or received only about $1–2 a day (e.g. a $36k gold short), which matches the percent reading. So Walt
> funding is about 100× smaller than stated below: roughly 0.001–0.01% a day. Consequences: the funding-carry lead is
> **dead**, and funding does **not** sink long holds on Walt. With near-zero funding, the six-month long-only trend made +453%
> (drawdown 20%) vs +402% for simply holding the same assets (`hold_baseline.py`, funding 0 rows), so the trend filter
> still adds little over holding. Sections below that rely on large funding are superseded.


Second opinion on `BACKTEST_AUDIT.md`, `POSITIVE_STRATEGY_FINDINGS.md` and `BROADER_RESEARCH_FINDINGS.md`. I added two checks.

## 1. The six-month trend "edge" is the asset mix

`hold_baseline.py` reruns `broad_daily.py` with one extra rule: hold every asset, using the same inverse-volatility weights, the same 126-day warm-up and the same costs. Both rules run at 1× from 2015 to Nov 2026, on 39 assets.

| Funding per day | Six-month trend, long only | Hold everything |
|---|---|---|
| 0 | +453%, drawdown 20% | +402%, drawdown 22% |
| 0.03% | +50%, drawdown 35% | +37%, drawdown 31% |
| 0.08% | −83%, drawdown 83% | −84%, drawdown 84% |

The trend filter adds almost nothing over holding the same 39 assets. That universe is today's winners (NVDA, PLTR, crypto, gold), so it carries survivorship bias. This matches Codex's rotation check: random picks from the same asset classes beat SPY in 64 of 64 runs.

## 2. Walt funding is larger than every cost the backtests assumed

`funding_rate_hourly` is a fraction per hour, with positive meaning longs pay shorts. Two sources:

- **Current snapshot** (`bot/data/market/markets.json`):
  - BTC +0.23%/day, ETH +0.21, XAU +0.27, SPY +0.42, NVDA +0.54, XPT +2.8.
  - Some crypto is negative: TRX −0.56, ADA −0.48.
- **Walt funding log, 14–16 Nov** (`walt-trading-bot/data/funding_log.csv`, 27 snapshots, 130 assets):
  - The median is about +0.077%/day, which is 28%/year paid by longs.
  - There are large spikes, e.g. NVDA up to +18%/day and XAU from −5.5% to +1.1%/day.

What this means:

- Any multi-day long on Walt perps pays about 0.08–0.5% a day. That is more than the 0.14% round-trip fee, every day.
- At the median rate, both slow long-only strategies lose over 80% (table above). The only "positive" findings, the slow long trend and the crypto large-move long, cannot be implemented on Walt perps. They belong in spot or ETFs, for example IBKR, where there is no funding.
- The page's own backtest ignored funding. Shorts receive it, longs pay it. This is a side bias the rule never saw.

The unit is inferred, not documented by Walt. A fraction of 3e-5 per hour is close to Hyperliquid-style rates; the alternative reading, percent, would make it about 0.3% a year, which is implausibly small. To confirm, compare the gold short's "funding paid" in Telegram after one day with about +$98 × days held.

## Why the tracker's rule loses

- **24-hour trends don't persist hourly on these assets.** After Codex's fixes the rule makes −0.180% per entry against −0.134% for a random direction. In this sample, picking the direction made results slightly worse.
- **The cleanest trends do worst** (quality 0.4+: −0.61% per trade). By the time a 24-hour move scores as clean, it has mostly happened.
- **The stop sits inside normal noise.** 2.5 × the average hourly move, plus the channel band, is hit by ordinary swings: stops were 56% of exits.
- **Costs are larger than the edge.** The 0.14% fee alone is bigger than any edge found. Funding and spread come on top.
- **The aligned (★) filter is unstable.** It was positive in the later sample only through crypto, while aligned stocks lost. It is not proven.

## Suggested tracker changes (not built)

1. **Stop presenting the 24-hour trend as a trade signal.**
   - Relabel it "recent direction" and drop the "best trade" highlight and the quality star as recommendations.
   - Keep the backtest line, which now shows paired average P/L against random.
2. **Show funding as a first-class cost.**
   - On each card: "holding 1 day costs/earns X% of margin at L×", and the break-even move including funding.
   - Warn when funding is against the side and above about 0.1%/day.
3. **Log Walt funding every 10 minutes into the repo**, as the cloud job already reads it. After a few weeks there is a real funding history to backtest with. Today the backtests have none.
4. **Leverage sanity check.**
   - At 25× on gold, the stop distance is about 1 hourly noise unit.
   - Show "chance a normal day touches this stop", computed from ATR, next to each leverage option.
5. **Forward paper log.**
   - Record every signal the page shows, with time, price and funding, then score it a few days later.
   - This is the only honest test left. The research has now looked at all past data, so any further fitted rule is overfit.

## Not recommended

- High leverage on any of these rules. Codex's leverage test reached −96% to −100%.
- Long-only multi-day holds on Walt perps. Funding makes them lose.
- More rule searching on the same data. Codex has tried 100+ configurations, and the survivors look like selection bias.
