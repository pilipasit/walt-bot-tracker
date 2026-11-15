"""Export the live bot's real trades for dashboard.html.

Writes data/live/dashboard_data.js (a plain script the page includes), built from the bot's
own log. Prices and moving averages are fetched live by the page itself.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .contracts import ROOT

LIVE = ROOT / "data" / "live"
OUT = LIVE / "dashboard_data.js"


def write_data() -> None:
    log = LIVE / "log.jsonl"
    if not log.exists():
        return
    events, account, positions, free = [], [], {}, None
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("mode") != "LIVE":
            continue
        ts = int(time.mktime(time.strptime(e["time"] + " UTC", "%Y-%m-%d %H:%M:%S %Z")) - time.timezone) * 1000
        kind, sym = e["event"], e.get("symbol")
        if kind == "account":
            account.append([ts, e["value"]])
            free = e.get("available")
        elif kind == "position_confirmed":
            p = e["position"]
            stop = ((p.get("auto_close") or {}).get("stop_loss") or {}).get("trigger_price_quote")
            positions[sym] = {"side": p["side"], "entry": float(p["avg_entry_price"]), "size": float(p["value_base"]),
                              "margin": float(p["margin_quote"]), "leverage": p["leverage"],
                              "stop": float(stop) if stop else None, "liquidation": float(p["liquidation_price"]), "ts": ts}
            events.append({"ts": ts, "symbol": sym, "kind": "opened " + p["side"], "price": float(p["avg_entry_price"])})
        elif kind == "close":
            positions.pop(sym, None)
            events.append({"ts": ts, "symbol": sym, "kind": "closed (" + e.get("reason", "") + ")", "price": None})
        elif kind == "position_gone":
            positions.pop(sym, None)
            events.append({"ts": ts, "symbol": sym, "kind": "closed by Walt (stop-loss or liquidation)", "price": None})
        elif kind in ("skip_open", "execute_error", "preview_rejected", "giving_up_on_bar", "halt"):
            note = e.get("reason") or str(e.get("error", ""))[:120]
            events.append({"ts": ts, "symbol": sym, "kind": kind.replace("_", " "), "price": None, "note": note})
    cfg = json.loads((ROOT / "config.live.json").read_text(encoding="utf-8"))
    data = {"updated": int(time.time() * 1000), "events": events, "account": account, "positions": positions,
            "config": {k: cfg[k] for k in ("symbols", "interval", "leverage", "margin_per_position", "stop_loss_pct",
                                           "max_drawdown_pct", "end_utc", "budget")},
            "fast": cfg["strategy"]["params"]["fast"], "slow": cfg["strategy"]["params"]["slow"],
            "freeBalance": free}
    OUT.write_text("window.BOT = " + json.dumps(data) + ";\n", encoding="utf-8")
    # One self-contained page: the record is written into the HTML, so it opens anywhere.
    template = (ROOT / "waltbot" / "dashboard_template.html").read_text(encoding="utf-8")
    inline = "<script>window.BOT = " + json.dumps(data).replace("</", "<\\/") + ";</script>"
    (ROOT / "dashboard.html").write_text(template.replace("<!--BOT_DATA-->", inline), encoding="utf-8")


def export_artifact_json(out_path: str, bars_shown: int = 180) -> dict:
    """One JSON file for the published Walt Bot Tracker: 4-hour bars with both averages and
    the rule's signal per coin, the bot's positions, its account history and its events.
    Every row carries `kind`; the page splits them."""
    import inspect

    from .data import _download

    cfg = json.loads((ROOT / "config.live.json").read_text(encoding="utf-8"))
    fast_n, slow_n = cfg["strategy"]["params"]["fast"], cfg["strategy"]["params"]["slow"]
    step, now = 4 * 3_600_000, int(time.time() * 1000)
    iso = lambda ts: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts / 1000))  # noqa: E731
    rnd = lambda x, ref: None if x is None else round(x, 1 if ref >= 1000 else 2 if ref >= 10 else 4 if ref >= 1 else 6)  # noqa: E731
    rows: list[dict] = []
    live = {}
    for sym in cfg["symbols"]:
        bars = [b for b in _download(f"{sym}USDT", "4h", now - (bars_shown + slow_n + 5) * step, now) if b.ts + step <= now]
        closes = [b.c for b in bars]
        sma = lambda n, i: sum(closes[i + 1 - n: i + 1]) / n if i + 1 >= n else None  # noqa: E731
        sig_prev, since = 0, None
        start = max(slow_n - 1, len(bars) - bars_shown)
        for i in range(slow_n - 1, len(bars)):
            f, s = sma(fast_n, i), sma(slow_n, i)
            sig = 1 if f > s else -1
            flip = bool(sig_prev and sig != sig_prev)
            if flip or since is None:
                since = bars[i].ts + step
            sig_prev = sig
            if i >= start:
                ref = closes[i]
                rows.append({"kind": "bar", "symbol": sym, "time": iso(bars[i].ts + step), "close": rnd(closes[i], ref),
                             "high": rnd(bars[i].h, ref), "low": rnd(bars[i].l, ref), "fast": rnd(f, ref), "slow": rnd(s, ref),
                             "rule": "long" if sig > 0 else "short", "flip": flip})
        price = _download(f"{sym}USDT", "1m", now - 180_000, now)[-1].c
        live[sym] = price
        f, s = sma(fast_n, len(bars) - 1), sma(slow_n, len(bars) - 1)
        rows.append({"kind": "coin", "symbol": sym, "price_now": rnd(price, price), "fast": rnd(f, price), "slow": rnd(s, price),
                     "gap_pct": round((f / s - 1) * 100, 2), "rule": "long" if f > s else "short", "since": iso(since)})
    write_data()
    bot = json.loads(OUT.read_text(encoding="utf-8")[len("window.BOT = "):].rstrip().rstrip(";"))
    for sym, p in bot["positions"].items():
        d = 1 if p["side"] == "long" else -1
        px = live.get(sym, p["entry"])
        rows.append({"kind": "position", "symbol": sym, "side": p["side"], "leverage": p["leverage"], "entry": p["entry"],
                     "stop": p["stop"], "margin": round(p["margin"], 2), "size": p["size"], "price_now": rnd(px, px),
                     "upnl": round(d * (px - p["entry"]) * p["size"], 2),
                     "to_stop_pct": round(abs(p["stop"] / px - 1) * 100, 2) if p["stop"] else None, "time": iso(p["ts"])})
    for ts, v in bot["account"]:
        rows.append({"kind": "account", "time": iso(ts), "value": round(v, 2)})
    lines = [json.loads(x) for x in (LIVE / "log.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    acc = [e for e in lines if e.get("mode") == "LIVE" and e["event"] == "account"]
    if acc:
        a = acc[-1]
        rows.append({"kind": "summary", "time": iso(bot["account"][-1][0]), "value": round(a["value"], 2),
                     "pnl": round(a["value"] - cfg["budget"], 2), "free": round(a["available"], 2),
                     "open_positions": a["open_positions"], "budget": cfg["budget"]})
    for n, e in enumerate(bot["events"]):
        rows.append({"kind": "event", "id": str(n), "time": iso(e["ts"]), "symbol": e["symbol"], "action": e["kind"],
                     "price": e["price"], "note": e.get("note") or ""})
    meta = [{"label": "Prices", "value": "Binance spot, 4-hour closes; price_now is the latest 1-minute close"},
            {"label": "Bot record", "value": "the live bot's own log, data/live/log.jsonl"},
            {"label": "export_artifact_json", "value": inspect.getsource(export_artifact_json), "type": "code", "lang": "python"}]
    out = {"rows": rows, "meta": meta}
    Path(out_path).write_text(json.dumps(out), encoding="utf-8")
    return {"rows": len(rows), "bytes": Path(out_path).stat().st_size}
