"""Post-Earnings-Announcement Drift (PEAD) research script.

Tests post-earnings drift with real historical earnings dates for 16 major tech/crypto-linked stocks:
AAPL, AMD, AMZN, AVGO, GOOGL, INTC, META, MU, NVDA, ORCL, PLTR, TSLA, TSM, COIN, MSTR, HOOD.

Caches earnings dates in research/pead_cache/<TICKER>.csv.
Prices from C:/Projects/walt-trading-bot/data/research/daily/<TICKER>.csv.

Rule: Enter at close of first trading day after report, in direction of reaction or EPS surprise.
Hold 5, 20, 60 trading days. Choose settings on 2012-2019 only, test frozen on 2020-2026.
Costs: 0.14% round trip; funding: 0.08%/calendar day charged to longs and paid to shorts.
Compares with random entries of same holding period.
Counts every configuration tried.
"""
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd

# Paths
ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "pead_cache"
DATA_DIR = Path(r"C:\Projects\walt-trading-bot\data\research\daily")

# Target universe
STOCKS = [
    "AAPL", "AMD", "AMZN", "AVGO", "GOOGL", "INTC", "META", "MU",
    "NVDA", "ORCL", "PLTR", "TSLA", "TSM", "COIN", "MSTR", "HOOD"
]

# Parameters
START = pd.Timestamp("2012-01-01")
SPLIT = pd.Timestamp("2020-01-01")
END = pd.Timestamp("2026-11-20")

FEE = 0.14      # 0.14% round-trip fee
FUND = 0.08     # 0.08% per calendar day: longs pay, shorts receive


def ensure_cached_earnings():
    """Ensure earnings dates are cached for all stocks."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for s in STOCKS:
        fpath = CACHE_DIR / f"{s}.csv"
        if not fpath.exists():
            print(f"Fetching earnings dates for {s} from Yahoo Finance...")
            import yfinance as yf
            t = yf.Ticker(s)
            df = t.get_earnings_dates(limit=100)
            if df is not None and len(df) > 0:
                df.to_csv(fpath)
            time.sleep(0.5)


def load_data():
    """Load price data and parsed earnings events."""
    prices = {}
    for s in STOCKS:
        f = DATA_DIR / f"{s}.csv"
        if f.exists():
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            prices[s] = df[(df.open > 0) & (df.close > 0)].sort_index()

    earnings = {}
    for s in STOCKS:
        fpath = CACHE_DIR / f"{s}.csv"
        if not fpath.exists():
            continue
        pe = pd.read_csv(fpath).dropna(subset=["Reported EPS"])
        df = prices[s]
        dates = df.index
        rows = []
        for _, r in pe.iterrows():
            ed_str = str(r["Earnings Date"])
            dt_date = pd.Timestamp(ed_str[:10])
            h_int = int(ed_str[11:13]) if len(ed_str) >= 13 and ed_str[11:13].isdigit() else 16
            
            # Timing awareness:
            # hour < 9.5 -> report was before market open (BMO), so date itself is the reaction day.
            # hour >= 9.5 -> report was mid-day or after close (AMC), so next trading day is reaction day.
            cand = dates[dates >= dt_date] if h_int < 9.5 else dates[dates > dt_date]
            if len(cand) == 0:
                continue
            react_date = cand[0]
            pos = dates.get_loc(react_date)
            if pos == 0:
                continue
            
            surprise = r["Surprise(%)"] if "Surprise(%)" in r else np.nan
            rows.append((react_date, pos, surprise))
        
        rows.sort(key=lambda x: x[1])
        earnings[s] = rows

    return prices, earnings


def run_strategy(prices, earnings, sig, thresh, h, mode, side_filter="both"):
    """
    Simulate PEAD strategy.
    sig: 'day_reaction', 'gap_reaction', 'eps_surprise', 'agreement'
    thresh: float (e.g. 0.0, 1.0, 2.0)
    h: holding period in trading days (5, 20, 60)
    mode: 1 (follow), -1 (fade)
    side_filter: 'both', 'long_only', 'short_only'
    """
    trades = []
    for s in STOCKS:
        if s not in prices or s not in earnings:
            continue
        df = prices[s]
        dates = df.index
        n_bars = len(df)
        rows = earnings[s]

        nxt = 0
        for react_date, pos, surprise in rows:
            if react_date < START:
                continue
            if pos < nxt or pos + h >= n_bars:
                continue

            prev_c = df.close.iloc[pos - 1]
            react_c = df.close.iloc[pos]
            react_o = df.open.iloc[pos]

            day_ret = (react_c / prev_c - 1) * 100
            gap_ret = (react_o / prev_c - 1) * 100

            if sig == "day_reaction":
                if abs(day_ret) < thresh:
                    continue
                raw_side = 1 if day_ret > 0 else -1
            elif sig == "gap_reaction":
                if abs(gap_ret) < thresh:
                    continue
                raw_side = 1 if gap_ret > 0 else -1
            elif sig == "eps_surprise":
                if pd.isna(surprise) or abs(surprise) < thresh:
                    continue
                raw_side = 1 if surprise > 0 else -1
            elif sig == "agreement":
                if pd.isna(surprise) or abs(surprise) < thresh or abs(day_ret) < thresh:
                    continue
                if day_ret > 0 and surprise > 0:
                    raw_side = 1
                elif day_ret < 0 and surprise < 0:
                    raw_side = -1
                else:
                    continue
            else:
                continue

            pos_dir = raw_side * mode
            if side_filter == "long_only" and pos_dir < 0:
                continue
            if side_filter == "short_only" and pos_dir > 0:
                continue

            exit_c = df.close.iloc[pos + h]
            trade_ret = (exit_c / react_c - 1) * 100
            cal_days = (dates[pos + h] - dates[pos]).days

            trades.append((react_date, pos_dir, trade_ret, cal_days, s, raw_side))
            nxt = pos + h

    return trades


def summarize(tr_list, funding=True):
    """Summarize trade list returns."""
    if not tr_list:
        return {"n": 0, "avg": 0.0, "win": 0.0, "median": 0.0, "tot": 0.0, "dd": 0.0, "t": 0.0}
    
    pnls = []
    for t_date, pos_dir, trade_ret, cal_days, s, raw_side in tr_list:
        # funded: longs pay FUND*days, shorts receive FUND*days
        pnl = pos_dir * trade_ret - FEE - (pos_dir * FUND * cal_days if funding else 0.0)
        pnls.append(pnl)
    
    pnls = np.array(pnls)
    n = len(pnls)
    avg = float(pnls.mean())
    med = float(np.median(pnls))
    win = float((pnls > 0).mean() * 100)
    std = float(pnls.std(ddof=1)) if n > 1 else 0.0
    t = float(avg / (std / np.sqrt(n))) if n > 2 and std > 0 else 0.0

    eq = np.cumprod(1 + pnls / 100.0)
    pk = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    tot = float((eq[-1] - 1.0) * 100.0)
    dd = float((eq / pk - 1.0).min() * 100.0)

    return {
        "n": n,
        "avg": round(avg, 3),
        "median": round(med, 3),
        "win": round(win, 1),
        "tot": round(tot, 1),
        "dd": round(dd, 1),
        "t": round(t, 2)
    }


def compute_random_baselines(prices):
    """Compute unconditional and random baselines for holding periods."""
    baselines = {}
    for h in [5, 20, 60]:
        baselines[h] = {}
        for period_name, p_start, p_end in [("train", START, SPLIT), ("test", SPLIT, END)]:
            long_f, long_nf = [], []
            short_f, short_nf = [], []
            rnd_5050_f, rnd_5050_nf = [], []
            
            for s in STOCKS:
                if s not in prices:
                    continue
                df = prices[s]
                sub = df[(df.index >= p_start) & (df.index < p_end)]
                dates = sub.index
                n = len(sub)
                for i in range(n - h):
                    c0 = sub.close.iloc[i]
                    c1 = sub.close.iloc[i + h]
                    ret = (c1 / c0 - 1.0) * 100.0
                    cal_days = (dates[i + h] - dates[i]).days

                    # Long
                    l_nf = ret - FEE
                    l_f = ret - FEE - FUND * cal_days
                    # Short
                    s_nf = -ret - FEE
                    s_f = -ret - FEE + FUND * cal_days

                    long_nf.append(l_nf)
                    long_f.append(l_f)
                    short_nf.append(s_nf)
                    short_f.append(s_f)
                    rnd_5050_nf.append(0.5 * (l_nf + s_nf))
                    rnd_5050_f.append(0.5 * (l_f + s_f))

            baselines[h][period_name] = {
                "long_funded": round(float(np.mean(long_f)), 3),
                "long_nofund": round(float(np.mean(long_nf)), 3),
                "short_funded": round(float(np.mean(short_f)), 3),
                "short_nofund": round(float(np.mean(short_nf)), 3),
                "random_5050_funded": round(float(np.mean(rnd_5050_f)), 3),
                "random_5050_nofund": round(float(np.mean(rnd_5050_nf)), 3),
            }
    return baselines


def main():
    print("Step 1: Checking / caching earnings dates...")
    ensure_cached_earnings()

    print("Step 2: Loading price data and earnings events...")
    prices, earnings = load_data()

    print("Step 3: Running configuration sweep across 2012-2019 (train) and 2020-2026 (test)...")
    signals = ["day_reaction", "gap_reaction", "eps_surprise", "agreement"]
    thresholds = [0.0, 1.0, 2.0]
    horizons = [5, 20, 60]
    modes = [1, -1]
    sides = ["both", "long_only", "short_only"]

    all_configs = {}
    config_count = 0

    for sig in signals:
        for thresh in thresholds:
            for h in horizons:
                for mode in modes:
                    mode_str = "follow" if mode == 1 else "fade"
                    for side in sides:
                        config_count += 1
                        key = f"{sig}_th{thresh}_h{h}_{mode_str}_{side}"
                        tr = run_strategy(prices, earnings, sig, thresh, h, mode, side)
                        
                        tr_train = [x for x in tr if x[0] < SPLIT]
                        tr_test = [x for x in tr if x[0] >= SPLIT]

                        tr_test_long = [x for x in tr_test if x[1] > 0]
                        tr_test_short = [x for x in tr_test if x[1] < 0]

                        all_configs[key] = {
                            "config": {
                                "signal": sig,
                                "threshold": thresh,
                                "horizon": h,
                                "mode": mode_str,
                                "side": side
                            },
                            "train_funded": summarize(tr_train, funding=True),
                            "train_nofund": summarize(tr_train, funding=False),
                            "test_funded": summarize(tr_test, funding=True),
                            "test_nofund": summarize(tr_test, funding=False),
                            "test_long_only_funded": summarize(tr_test_long, funding=True),
                            "test_long_only_nofund": summarize(tr_test_long, funding=False),
                            "test_short_only_funded": summarize(tr_test_short, funding=True),
                            "test_short_only_nofund": summarize(tr_test_short, funding=False),
                        }

    print(f"Total configurations tried: {config_count}")

    # Baseline calculations
    baselines = compute_random_baselines(prices)

    # Selection on Train 2012-2019:
    # 1. Primary frozen choice: Best bidirectional (side='both') strategy with funding (min n >= 50)
    both_funded_configs = {
        k: v for k, v in all_configs.items()
        if v["config"]["side"] == "both" and v["train_funded"]["n"] >= 50
    }
    best_frozen_key = max(both_funded_configs, key=lambda k: both_funded_configs[k]["train_funded"]["avg"])
    best_frozen = all_configs[best_frozen_key]

    # 2. Best bidirectional unfunded strategy on train (min n >= 50)
    both_nofund_configs = {
        k: v for k, v in all_configs.items()
        if v["config"]["side"] == "both" and v["train_nofund"]["n"] >= 50
    }
    best_nofund_key = max(both_nofund_configs, key=lambda k: both_nofund_configs[k]["train_nofund"]["avg"])
    best_nofund = all_configs[best_nofund_key]

    # 3. Best overall across all 216 configs on train funded (min n >= 40)
    all_valid_funded = {k: v for k, v in all_configs.items() if v["train_funded"]["n"] >= 40}
    best_overall_key = max(all_valid_funded, key=lambda k: all_valid_funded[k]["train_funded"]["avg"])

    # Prepare output dictionary
    output = {
        "configs_tried": config_count,
        "stocks": STOCKS,
        "parameters": {
            "fee_roundtrip_pct": FEE,
            "funding_daily_pct": FUND,
            "train_period": "2012-01-01 to 2019-12-31",
            "test_period": "2020-01-01 to 2026-11-20"
        },
        "chosen_frozen_setting": best_frozen_key,
        "chosen_frozen_results": best_frozen,
        "chosen_unfunded_setting": best_nofund_key,
        "chosen_unfunded_results": best_nofund,
        "best_overall_on_train": best_overall_key,
        "best_overall_results": all_configs[best_overall_key],
        "random_baselines": baselines,
        "all_configs": all_configs
    }

    out_file = ROOT / "pead_results.json"
    out_file.write_text(json.dumps(output, indent=2))
    print(f"Results written to {out_file.name}")

    print("\n" + "=" * 80)
    print("SELECTION & FROZEN OUT-OF-SAMPLE TEST SUMMARY")
    print("=" * 80)
    print(f"Configurations evaluated: {config_count}")
    print(f"Primary frozen choice (bidirectional, funded, train 2012-2019): {best_frozen_key}")
    print(f"  Train (Funded)  : n={best_frozen['train_funded']['n']}, avg={best_frozen['train_funded']['avg']}%, win={best_frozen['train_funded']['win']}%, t={best_frozen['train_funded']['t']}")
    print(f"  Train (NoFund)  : n={best_frozen['train_nofund']['n']}, avg={best_frozen['train_nofund']['avg']}%, win={best_frozen['train_nofund']['win']}%, t={best_frozen['train_nofund']['t']}")
    print(f"  Test  (Funded)  : n={best_frozen['test_funded']['n']}, avg={best_frozen['test_funded']['avg']}%, win={best_frozen['test_funded']['win']}%, t={best_frozen['test_funded']['t']}")
    print(f"  Test  (NoFund)  : n={best_frozen['test_nofund']['n']}, avg={best_frozen['test_nofund']['avg']}%, win={best_frozen['test_nofund']['win']}%, t={best_frozen['test_nofund']['t']}")
    print(f"  Test Longs (Funded)  : n={best_frozen['test_long_only_funded']['n']}, avg={best_frozen['test_long_only_funded']['avg']}%, t={best_frozen['test_long_only_funded']['t']}")
    print(f"  Test Shorts (Funded) : n={best_frozen['test_short_only_funded']['n']}, avg={best_frozen['test_short_only_funded']['avg']}%, t={best_frozen['test_short_only_funded']['t']}")
    
    h = best_frozen["config"]["horizon"]
    print(f"\nRandom Baseline Comparison for h={h} days on Test (2020-2026):")
    print(f"  Random 50/50 Hold   : Funded={baselines[h]['test']['random_5050_funded']}%, NoFund={baselines[h]['test']['random_5050_nofund']}%")
    print(f"  Random Long Hold    : Funded={baselines[h]['test']['long_funded']}%, NoFund={baselines[h]['test']['long_nofund']}%")
    print(f"  Random Short Hold   : Funded={baselines[h]['test']['short_funded']}%, NoFund={baselines[h]['test']['short_nofund']}%")


if __name__ == "__main__":
    main()
