"""Telegram alerts for the markets page, sent through EOS's bot relay (only the owner can receive them).

Runs inside every cloud cycle (every minute), after Walt's prices are sampled. It reads the owner's positions from the
private positions store (walt-positions worker; never from the public repo) and sends one message per event:
  · a position's price comes near its stop or take-profit (within a quarter of the asset's normal swing, min 0.15%);
  · the price is beyond a stop / take-profit (the position was probably closed by Walt — check the app);
  · the trend call on an asset you hold changes (up / down / sideways);
  · a new clean, aligned trend appears (quality ≥ 0.4 and 24h/3d/7d agree) — at most once per asset per 12 h.
Each event fires once; `data/market/alerts_state.json` remembers what was sent (ids and symbols only — no amounts).
Analysis only: these messages describe prices and the page's rules; they are not advice and trigger no orders.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from .markets import OUT

STATE = OUT / "alerts_state.json"
SEND_URL = "https://eos-bot.mrsupetbred.workers.dev/send"
POS_URL = "https://walt-positions.mrsupetbred.workers.dev/positions"
UA = {"User-Agent": "walt-markets-alerts/1.0", "Content-Type": "application/json"}  # Cloudflare blocks Python's default UA
PAGE = "https://pilipasit.github.io/walt-bot-tracker/"


def _http(url: str, key: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={**UA, "Authorization": f"Bearer {key}"}, method="POST" if body else "GET")
    return json.load(urllib.request.urlopen(req, timeout=15))


def send(text: str) -> bool:
    key = os.environ.get("EOS_SEND_SECRET", "")
    if not key:
        return False
    try:
        return bool(_http(SEND_URL, key, {"text": text, "parse_mode": "HTML",
                                          "link_preview_options": {"is_disabled": True}}).get("ok"))
    except Exception as e:  # noqa: BLE001
        print("alert not sent:", str(e)[:120], flush=True)
        return False


def _fmt(p: float) -> str:
    return f"{p:,.2f}" if p >= 100 else f"{p:,.4f}" if p >= 1 else f"{p:.6g}"


def check() -> int:
    """Evaluate all alert rules once; returns how many messages were sent."""
    try:
        markets = json.loads((OUT / "markets.json").read_text(encoding="utf-8"))
        live = json.loads((OUT / "live.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — no data yet
        return 0
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        state = {}
    sent_keys: dict = state.setdefault("sent", {})
    trends_seen: dict = state.setdefault("trends", {})
    now = int(time.time() * 1000)
    assets = {a["symbol"]: a for a in markets.get("assets", [])}
    price = {s: v[1] for s, v in live.get("prices", {}).items()}
    msgs: list[tuple[str, str]] = []          # (dedup key, text)

    positions = []
    key = os.environ.get("POSITIONS_SYNC_KEY", "")
    if key:
        try:
            positions = _http(POS_URL, key).get("positions", [])
        except Exception as e:  # noqa: BLE001
            print("positions not read:", str(e)[:120], flush=True)

    held = set()
    for p in positions:
        a, px = assets.get(p.get("sym")), price.get(p.get("sym"))
        if not a or not px:
            continue
        held.add(p["sym"])
        x = a.get("analysis") or {}
        noise = 2.5 * x.get("atr_pct_h", 0.3) + x.get("band_pct", 0)
        near = max(0.15, noise / 4)
        side, pid, name = p.get("side", 1), p.get("id"), a.get("name", p["sym"])
        tag = f"{name} {'short' if side < 0 else 'long'} {p.get('lev')}×"
        for lvl, label in ((p.get("sl"), "stop"), (p.get("tp"), "take-profit")):
            if not lvl:
                continue
            beyond = side * (px - lvl) < 0 if label == "stop" else side * (px - lvl) > 0
            dist = abs(px - lvl) / px * 100
            if beyond:
                msgs.append((f"{pid}:{label}:hit", f"⚠️ <b>{tag}</b>: price {_fmt(px)} is beyond your {label} {_fmt(lvl)} — "
                                                  f"Walt has probably closed it. Check the app."))
            elif dist <= near:
                msgs.append((f"{pid}:{label}:near:{int(now // 3_600_000)}",
                             f"🔔 <b>{tag}</b>: {_fmt(px)} is {dist:.2f}% from your {label} {_fmt(lvl)}."))

    for s, a in assets.items():
        x = a.get("analysis")
        if not x:
            continue
        prev = trends_seen.get(s)
        trends_seen[s] = x["trend"]
        if s in held and prev and prev != x["trend"]:
            msgs.append((f"flip:{s}:{now // 600_000}", f"↔️ <b>{a['name']}</b> (you hold it): trend changed {prev} → {x['trend']} "
                                                       f"at {_fmt(price.get(s, x['price']))} (quality {x['score']:.2f})."))
        if x.get("aligned") and x["score"] >= 0.4 and x["trend"] != "sideways":
            last = sent_keys.get(f"clean:{s}:{x['trend']}", 0)
            if now - last > 12 * 3_600_000 and prev is not None:
                arrow = "▲" if x["trend"] == "up" else "▼"
                msgs.append((f"clean:{s}:{x['trend']}", f"{arrow} <b>{a['name']}</b>: clean {x['trend']} trend, 24h/3d/7d agree "
                                                        f"(quality {x['score']:.2f}, R² {x['r2']:.2f})."))

    n = 0
    for k, text in msgs:
        if k.startswith("clean:"):
            pass                                   # time-gated above
        elif k in sent_keys:
            continue
        if n >= 5:                                 # never flood: at most 5 messages a minute
            break
        if send(text + f'\n<a href="{PAGE}">Open the page</a> · analysis, not advice'):
            sent_keys[k] = now
            n += 1
    # forget old keys (2 days) so the file stays small
    state["sent"] = {k: v for k, v in sent_keys.items() if now - v < 2 * 86_400_000}
    STATE.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    return n
