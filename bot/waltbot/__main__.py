"""Command line: python -m waltbot <contracts|backtest|paper> ..."""
from __future__ import annotations

import argparse
import json

from .backtest import compare, load_config, run_backtest
from .contracts import load_contracts
from .paper import SIDE, _iso, run_paper


def cmd_contracts(args) -> None:
    rows = sorted(load_contracts().values(), key=lambda c: c.base_asset)
    print(f"{'asset':<12}{'id':>5}{'max lev':>9}{'taker fee':>11}{'slippage':>10}{'funding/h':>11}{'min margin':>12}")
    for c in rows:
        if args.find and args.find.upper() not in c.base_asset:
            continue
        print(f"{c.base_asset:<12}{c.contract_id:>5}{c.max_leverage:>9}{c.taker_fee_pct:>11.4%}"
              f"{c.price_slippage:>10.2%}{c.funding_rate_hourly:>11.5%}{c.min_size_quote:>12}")


def cmd_backtest(args) -> None:
    summary, res = run_backtest(load_config(args.config))
    print(json.dumps(summary, indent=2))
    if args.trades:
        for e in res.events:
            print(f"{_iso(e.ts)}  {e.kind:<11} {SIDE[e.side]:<5} @ {e.price:<12.6g} size {e.size:<10g} pnl {e.pnl:>9.2f}  {e.note}")


def cmd_compare(args) -> None:
    cfg = load_config(args.config)
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else [cfg["symbol"]]
    rows = compare(cfg, symbols, args.split, args.short)
    cols = ["symbol", "strategy", "return_pct", "in_sample_pct", "out_sample_pct", "max_dd_pct",
            "trips", "win_pct", "fees", "funding", "liq", "buy_hold_pct"]
    widths = [max(len(c), *(len(str(r[c])) for r in rows)) + 2 for c in cols]
    print("".join(c.ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("".join(str(r[c]).ljust(w) for c, w in zip(cols, widths)))


def cmd_experiment(args) -> None:
    from .experiment import run
    run(load_config(args.config), args.hours, args.reset)


def cmd_research(args) -> None:
    from . import research

    cfg = load_config(args.config)
    if args.zero_cost:
        cfg["zero_cost"] = True
        cfg["sim"] = {**cfg.get("sim", {}), "fill_slippage": 0.0}
    out = research.run(cfg, args.grid, args.min_trades_per_day)
    configs = out["configs"]
    chosen = [c for name in out["selected"] for c in configs if c["config"] == name]
    n_sym = configs[0]["n_symbols"]
    print(f"grid '{args.grid}': {len(configs)} configurations x {n_sym} contracts"
          f"{' (ZERO COST diagnostic)' if args.zero_cost else ''}.")
    print("Returns are mean % per contract account per segment; trades per day are summed over contracts.")
    print()
    print(f"SELECTED on train+validation ({len(chosen)}):")
    print(research.table(chosen))
    print()
    print("TOP 15 by test return (hindsight; for context only, not a selection):")
    print(research.table(sorted(configs, key=lambda c: -c["test"])[:15]))
    pos = sum(c["test"] > 0 for c in configs)
    print()
    print(f"All configurations: {pos} of {len(configs)} positive on test; mean test return "
          f"{sum(c['test'] for c in configs) / len(configs):+.2f} %")
    if chosen:
        print(f"Selected configurations: {sum(c['test'] > 0 for c in chosen)} of {len(chosen)} positive on test; "
              f"mean test return {sum(c['test'] for c in chosen) / len(chosen):+.2f} %")


def cmd_model(args) -> None:
    from . import mlresearch as ml

    out = ml.run(load_config(args.config))
    rows = out["results"]
    print()
    print("Predictive correlation between forecast and realized return (0 = no signal):")
    for r in out["ic"]:
        print(f"  {r['model']:<14} horizon {r['horizon']:>2}h  {r['segment']:<6} {r['ic']:+.4f}")
    chosen = [r for name in out["selected"] for r in rows if r["config"] == name]
    print()
    print(f"Cost per round trip: {out['cost_per_trade_pct']:.2f} % of the position. Gross per trade must beat it.")
    print(f"SELECTED on validation ({len(chosen)} of {len(rows)}):")
    print(ml.table(chosen))
    print()
    print("TOP 12 by test return (hindsight; context only):")
    print(ml.table(sorted(rows, key=lambda r: -r["test"])[:12]))
    pos = sum(r["test"] > 0 for r in rows)
    print()
    print(f"All: {pos} of {len(rows)} positive on test; mean test {sum(r['test'] for r in rows) / len(rows):+.2f} %")
    if chosen:
        print(f"Selected: {sum(r['test'] > 0 for r in chosen)} of {len(chosen)} positive on test; "
              f"mean test {sum(r['test'] for r in chosen) / len(chosen):+.2f} %")


def cmd_llm(args) -> None:
    from . import llm_experiment as x

    if args.action == "start":
        x.start(args.contracts)
    elif args.action == "brief":
        x.brief(args.contracts)
    elif args.action == "decide":
        x.decide(args.file)
    elif args.action == "report":
        x.write_report()
        print("chain intact" if x.verify_chain() else "CHAIN BROKEN")
    elif args.action == "keepawake":
        import ctypes
        import time

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        print(f"keeping the PC awake for {args.hours} hours; Ctrl-C to stop", flush=True)
        time.sleep(args.hours * 3600)


def _live_config(path: str) -> dict:
    from datetime import datetime

    cfg = load_config(path)
    cfg["end_ts"] = int(datetime.fromisoformat(cfg["end_utc"].replace("Z", "+00:00")).timestamp() * 1000)
    return cfg


def cmd_live_login(args) -> None:
    """Owner sign-in to Walt, then a read-only look at the account. Places nothing."""
    import asyncio

    from .walt_client import connect

    async def go():
        async with connect(interactive=True) as walt:
            print("server time:", await walt.call("get_server_time"))
            print("balance:", json.dumps(await walt.call("balance_get")))
            print("open positions:", json.dumps(await walt.call("perps_list_positions", {"statuses": ["open"]})))

    asyncio.run(go())


def cmd_live(args) -> None:
    import asyncio

    from .live import run
    from .walt_client import connect

    cfg = _live_config(args.config)
    if args.live:
        print("LIVE MODE: real orders will be placed on Walt with real money.", flush=True)
    else:
        print("Dry run: reads the real account, places nothing. Add --live to trade.", flush=True)
    asyncio.run(run(cfg, args.live, args.loop, connect))


def cmd_live_retry(args) -> None:
    """Owner command: let the running live bot re-attempt this bar's decision for one symbol."""
    from .live import DIR

    path = DIR / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    sym = args.symbol.upper()
    if sym not in state["symbols"]:
        raise SystemExit(f"{sym} is not one of the bot's symbols: {sorted(state['symbols'])}")
    st = state["symbols"][sym]
    st.update({"last_bar": 0, "attempts": 0, "attempt_bar": 0})
    st.pop("pending_side", None)
    path.write_text(json.dumps(state), encoding="utf-8")
    print(f"{sym}: the bot will re-evaluate it on its next cycle (within its poll interval) and act on the current signal.")


def cmd_paper(args) -> None:
    print(json.dumps(run_paper(load_config(args.config), reset=args.reset), indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(prog="waltbot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("contracts", help="list Walt contract specs from the saved snapshot")
    p.add_argument("--find", help="filter by asset name")
    p.set_defaults(fn=cmd_contracts)
    p = sub.add_parser("backtest", help="run the configured strategy over history")
    p.add_argument("--config", default="config.json")
    p.add_argument("--trades", action="store_true", help="also print every event")
    p.set_defaults(fn=cmd_backtest)
    p = sub.add_parser("compare", help="run every strategy with default parameters, side by side")
    p.add_argument("--config", default="config.json")
    p.add_argument("--symbols", help="comma-separated assets, e.g. BTC,ETH,SOL (default: config symbol)")
    p.add_argument("--split", type=float, default=0.7, help="share of history used as in-sample")
    p.add_argument("--short", action="store_true", help="allow short positions where the strategy supports them")
    p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("experiment", help="unattended paper forward test of the pre-registered panel; writes REPORT.md")
    p.add_argument("--config", default="config.experiment.json")
    p.add_argument("--hours", type=float, default=8.0)
    p.add_argument("--reset", action="store_true", help="discard the previous run and start a new one")
    p.set_defaults(fn=cmd_experiment)
    p = sub.add_parser("research", help="grid of short-term rules with train/validation/test split")
    p.add_argument("--config", default="config.experiment.json")
    p.add_argument("--grid", default="short", choices=["short", "selective", "swing", "xsection"])
    p.add_argument("--zero-cost", action="store_true", help="diagnostic: no fees, funding or slippage")
    p.add_argument("--min-trades-per-day", type=float, default=0.0, help="selection floor, summed over contracts")
    p.set_defaults(fn=cmd_research)
    p = sub.add_parser("model", help="fit a model on all inputs and trade only large predicted moves")
    p.add_argument("--config", default="config.research.json")
    p.set_defaults(fn=cmd_model)
    p = sub.add_parser("llm", help="ledger for the model-as-trader experiment (simulated)")
    p.add_argument("action", choices=["start", "brief", "decide", "report", "keepawake"])
    p.add_argument("--contracts", help="path to a fresh perps_list_contracts snapshot")
    p.add_argument("--file", help="decision JSON file")
    p.add_argument("--hours", type=float, default=26.0)
    p.set_defaults(fn=cmd_llm)
    p = sub.add_parser("live-login", help="sign in to Walt in your browser and show the account (read-only)")
    p.set_defaults(fn=cmd_live_login)
    p = sub.add_parser("live", help="live trader; a dry run unless --live is given")
    p.add_argument("--config", default="config.live.json")
    p.add_argument("--live", action="store_true", help="place real orders with real money")
    p.add_argument("--loop", action="store_true", help="keep running until the end time or the kill switch")
    p.set_defaults(fn=cmd_live)
    p = sub.add_parser("cloud-setup", help="move the live bot to GitHub Actions (run once, on this PC)")
    p.set_defaults(fn=lambda a: __import__("waltbot.cloud", fromlist=["setup"]).setup())
    p = sub.add_parser("cloud-move", help="ONE command: stop the PC bot between cycles, move it to GitHub, start it there")
    p.set_defaults(fn=lambda a: __import__("waltbot.cloud", fromlist=["move"]).move())
    p = sub.add_parser("cloud-relogin", help="the cloud bot's Walt sign-in expired: sign in again and send it to GitHub")
    p.set_defaults(fn=lambda a: __import__("waltbot.cloud", fromlist=["relogin"]).relogin())
    p = sub.add_parser("cloud-run", help="one bot cycle on GitHub Actions (used by the workflow)")
    p.add_argument("--mode", choices=["prices", "dry", "live", "markets"], default="markets")
    p.set_defaults(fn=lambda a: __import__("waltbot.cloud", fromlist=["run"]).run(a.mode))
    p = sub.add_parser("cloud-status", help="show what the GitHub copy of the bot last did")
    p.set_defaults(fn=lambda a: __import__("waltbot.cloud", fromlist=["status"]).status())
    p = sub.add_parser("live-retry", help="let the live bot re-attempt the current bar for one symbol")
    p.add_argument("symbol")
    p.set_defaults(fn=cmd_live_retry)
    p = sub.add_parser("paper", help="advance paper trading and print any order ticket")
    p.add_argument("--config", default="config.json")
    p.add_argument("--reset", action="store_true", help="start paper trading over from now")
    p.set_defaults(fn=cmd_paper)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
