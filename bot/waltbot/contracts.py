"""Walt Perps contract specs, loaded from a saved snapshot of perps_list_contracts.

The snapshot (data/contracts_raw.json) is refreshed by hand: fees, leverage caps and
funding rates change, so re-save it before trusting a backtest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "contracts_raw.json"


@dataclass(frozen=True)
class Contract:
    contract_id: int
    base_asset: str
    size_decimals: int
    min_size_base: float
    min_size_quote: float  # minimum margin per order
    max_size_quote: float  # maximum margin per order
    max_leverage: int
    taker_fee_pct: float
    price_slippage: float  # worst-case widening Walt applies when sizing a market order
    funding_rate_hourly: float  # snapshot value; positive = longs pay shorts
    closeout_margin_fraction: float  # fraction of notional at which the position is closed out
    is_active: bool = True
    price_decimals: int = 2

    @property
    def market_id(self) -> str:
        return f"{self.base_asset}-USD-PERP"


def _parse(raw: dict) -> Contract:
    margin = raw.get("margin") or {}
    return Contract(
        contract_id=int(raw["contract_id"]),
        base_asset=raw["base_asset"],
        size_decimals=int(raw["size_decimals"]),
        min_size_base=float(raw["min_size_base"]),
        min_size_quote=float(raw["min_size_quote"]),
        max_size_quote=float(raw["max_size_quote"]),
        max_leverage=int(raw["max_leverage"]),
        taker_fee_pct=float(raw["taker_fee_pct"]),
        price_slippage=float(raw["price_slippage"]),
        funding_rate_hourly=float(raw.get("funding_rate_hourly") or 0.0),
        # Margin fractions are in basis points (1000 -> 10 %), matching max_leverage 10.
        closeout_margin_fraction=float(margin.get("closeout_margin_fraction", 0)) / 10000.0,
        is_active=bool(raw.get("is_active", True)),
        price_decimals=int(raw.get("price_decimals", 2)),
    )


def load_contracts(path: Path = SNAPSHOT) -> dict[str, Contract]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {c["base_asset"].upper(): _parse(c) for c in raw["contracts"]}


def get_contract(symbol: str, path: Path = SNAPSHOT) -> Contract:
    contracts = load_contracts(path)
    key = symbol.upper()
    if key not in contracts:
        raise KeyError(f"No Walt contract for '{symbol}'. Run: python -m waltbot contracts")
    return contracts[key]
