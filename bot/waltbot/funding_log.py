"""Walt funding-rate history, built from saved perps_list_contracts snapshots.

Walt publishes no funding history, so the only way to learn how its rates behave is to keep
every snapshot. `ingest` adds one snapshot file to data/funding_log.csv (once); `summary`
shows how each contract's rate has behaved across the snapshots collected so far.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .contracts import ROOT

LOG = ROOT / "data" / "funding_log.csv"
FIELDS = ["snapshot_ms", "asset", "funding_rate_hourly", "next_funding_at", "open_interest_usd", "volume_24h_usd"]


def _rows() -> list[dict]:
    if not LOG.exists():
        return []
    with open(LOG, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ingest(path: str) -> int:
    """Add a snapshot. The time comes from the epoch-ms suffix of the saved file name, or its mtime."""
    p = Path(path)
    m = re.search(r"(\d{13})", p.name)
    stamp = int(m.group(1)) if m else int(p.stat().st_mtime * 1000)
    if any(int(r["snapshot_ms"]) == stamp for r in _rows()):
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))["contracts"]
    new = not LOG.exists()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for c in data:
            w.writerow({"snapshot_ms": stamp, "asset": c["base_asset"], "funding_rate_hourly": c.get("funding_rate_hourly") or 0,
                        "next_funding_at": c.get("next_funding_at"), "open_interest_usd": c.get("open_interest_usd"),
                        "volume_24h_usd": c.get("volume_24h_usd")})
    return len(data)


def summary(assets: list[str] | None = None) -> list[dict]:
    """Per contract, one observation per funding hour (the last snapshot seen for that hour)."""
    by_asset: dict[str, dict[str, float]] = {}
    for r in sorted(_rows(), key=lambda r: int(r["snapshot_ms"])):
        by_asset.setdefault(r["asset"], {})[r["next_funding_at"]] = float(r["funding_rate_hourly"])
    out = []
    for asset, hours in by_asset.items():
        if assets and asset not in assets:
            continue
        v = list(hours.values())
        mean = sum(v) / len(v)
        out.append({"asset": asset, "hours": len(v), "mean_pct_h": mean * 100, "min_pct_h": min(v) * 100,
                    "max_pct_h": max(v) * 100, "share_positive": sum(x > 0 for x in v) / len(v),
                    "annualized_pct": mean * 8760 * 100})
    return sorted(out, key=lambda r: -r["mean_pct_h"])
