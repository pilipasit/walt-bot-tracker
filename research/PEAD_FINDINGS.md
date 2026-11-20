# Post-Earnings-Announcement Drift (PEAD) Findings

> **Correction (Claude, 20 Nov 2026):** the 0.08%/day funding in this test came from a wrong reading of Walt's rate; real
> Walt funding is about 100x smaller. Read the **No Funding** columns. Even then the frozen choice is not significant on
> 2020-2026 (+3.16% per 60-day trade, t 1.13), and the long side beats a random long by under 1% (market beta). The EPS-surprise
> rule's +1.77% (t 2.06) was not the setting chosen on 2012-2019 (+0.04% there), so it doesn't count. Verdict unchanged: null.

Real earnings dates for 16 tech/crypto stocks (AAPL, AMD, AMZN, AVGO, GOOGL, INTC, META, MU, NVDA, ORCL, PLTR, TSLA, TSM, COIN, MSTR, HOOD) were fetched from Yahoo Finance and cached in `pead_cache/`. Entry occurs at the close of the first trading day after announcement; holding periods are 5, 20, and 60 days. Round-trip fee is 0.14%; funding is 0.08%/day charged to longs and paid to shorts. Settings were selected on 2012–2019 (399 reports) across 216 configurations and tested frozen on 2020–2026 (430 reports).

## 1. Frozen Choices vs Random Baselines (2020–2026 Out-of-Sample)

| Configuration | Selected on 2012–2019 | 2020–2026 (Funded) | 2020–2026 (No Funding) | Random Benchmark |
|---|---|---|---|---|
| **Primary (Funded)**: Agreement ≥2%, h=20, Follow | +1.44% (t 2.16, n=181) | **+0.28%** (t 0.17, n=174) | +1.41% (t 0.87) | −0.14% (50/50) |
| **Unfunded**: Agreement ≥2%, h=60, Follow | +3.82% (t 2.41, n=170) | **−0.31%** (t −0.11, n=162) | +3.16% (t 1.13) | −0.14% (50/50) |
| **Raw Day Reaction**: h=20, Follow | +0.86% (t 1.90, n=399) | **−0.18%** (t −0.21, n=419) | −0.27% (t −0.32) | −0.14% (50/50) |
| **Raw EPS Surprise**: h=20, Follow | +0.04% (t 0.08, n=398) | **+0.42%** (t 0.49, n=419) | +1.77% (t 2.06) | −0.14% (50/50) |

## 2. Long vs Short Asymmetry on Chosen Rule (Agreement ≥2%, h=20)

| Side | Trades | Funded Return | Unfunded Return | Unconditional Hold (same h) |
|---|---|---|---|---|
| **Longs** (positive surprise + day reaction) | 129 | +1.73% (t 0.94) | +4.07% (t 2.21) | +0.83% funded / +3.15% nofund |
| **Shorts** (negative surprise + day reaction) | 45 | −3.89% (t −1.22) | −6.22% (t −1.95) | −1.11% funded / −3.43% nofund |
| **Both Combined** | 174 | **+0.28%** (t 0.17) | +1.41% (t 0.87) | −0.14% (50/50) |

## 3. Why This Is a Null Result

1. **Funding drag destroys the edge**: On Walt, holding for 20 trading days costs ~2.24% in funding; 60 days costs ~6.72%. The frozen rule's out-of-sample funded return is indistinguishable from zero (+0.28%, t = 0.17).
2. **Long "drift" is just market beta**: While unfunded longs gained +4.07% over 20 days, entering on *any random day* yielded +3.15% (excess return < 1%). Over 60 days, PEAD longs (+10.21%) lagged a random long hold (+10.55%).
3. **Short catastrophe**: Shorting bad earnings lost −6.22% per trade unfunded (−3.89% funded) due to persistent upward drift in large-cap tech.
4. **Conclusion**: Post-earnings announcement drift has no tradable alpha after Walt funding costs, and apparent spot profits are pure market beta. Verdict: **Null result (do not trade).**
