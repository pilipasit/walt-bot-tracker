# What follows an extreme 7-day move (Claude, 20 Nov 2026)

`extreme_moves.py` uses daily prices for 39 Walt assets, 2012 to Nov 2026.

**Method:**
- Move size z = the 7-day log move ÷ (daily volatility over the prior 60 days × √7).
- Returns are measured from the next day's open, in the **same direction** as the move: positive means it kept going, negative means it bounced.
- At most one event per asset per 5 days.
- No parameters were fitted. Both periods are shown.

## Results (average return in the move's direction; t in brackets)

| Case | 2012–2019, 3 days | 2012–2019, 10 days | 2020–2026, 3 days | 2020–2026, 10 days |
|---|---|---|---|---|
| Drops z ≥ 2, all | −0.44% (t −2.5) | −0.73% (t −2.1) | −0.52% (t −2.1) | −1.35% (t −3.4) |
| Drops z ≥ 4, all | −1.90% (t −2.7) | −1.11% | −2.68% (t −2.8) | −4.62% (t −4.0) |
| Drops z ≥ 3, non-crypto | −0.98% (t −3.5) | −1.30% (t −2.5) | −0.32% | −1.67% (t −1.8) |
| Drops z ≥ 3, crypto | +2.59% (n 27) | +4.12% | −4.20% (t −3.5) | −3.46% (t −2.5) |
| Rallies z ≥ 2, crypto | +2.90% (t 2.2) | +3.26% | +2.42% (t 3.2) | +4.33% (t 3.6) |
| Rallies z ≥ 2, non-crypto | −0.27% | −0.75% (t −2.3) | −0.14% | +0.10% |

The baseline is the average same-direction drift on all days: about 0.0–0.2%.

## Reading

- **After extreme drops, prices tended to bounce, in both periods.** Shorting after a large fall fought the bounce, on average −0.5% to −4.6% over 3–10 days. This matches the earnings-drift test, where shorts after bad news lost.
- **Crypto rallies of 2σ+ in a week tended to keep going in both periods**, by +2.4% to +4.3% over 3–10 days. Non-crypto rallies did not.
- **This is the most consistent pattern found so far:** same sign in both periods and in most buckets.

## Caveats

- **Survivorship.** The asset list is today's survivors. Assets that crashed and never recovered are missing, which flatters the bounces after drops. The crypto-rally result is less affected.
- **Clustering.** Events bunch together (March 2020, the 2022 crypto crashes), so t-statistics overstate certainty.
- **Volatility.** Moves are large in both directions after extreme weeks. These averages hide wide outcomes.
- **Costs.** Subtract about 0.14% per Walt round trip, plus slippage.

## In the tracker

Each card now shows an **"Extended move"** note when |z| ≥ 2 (`markets.py` `_move7`, daily volatility cached once a day). It states what usually followed in both periods.

This is a description of past behaviour, not a signal. The forward signal log will show whether it holds on new data.
