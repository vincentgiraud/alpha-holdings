"""Rebalance engine — generate trade proposals from target vs. current weights.

Reads the latest portfolio_weights snapshot and optional current holdings,
then generates concrete buy/sell trade proposals with estimated share counts
and values based on the latest available prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from alpha_holdings.data.storage import StorageBackend


@dataclass(slots=True)
class RebalanceResult:
    """Outcome of one rebalance run."""

    as_of: str
    portfolio_id: str
    portfolio_value: float
    trades_count: int
    buys: int
    sells: int
    estimated_turnover: float
    snapshot_path: Path
    proposals: pd.DataFrame


def rebalance_portfolio(
    *,
    storage: StorageBackend,
    as_of: str,
    portfolio_id: str = "default",
    portfolio_value: float = 1_000_000.0,
    seed_universe_path: Path | None = None,  # noqa: ARG001
) -> RebalanceResult:
    """Generate trade proposals to move from current to target portfolio weights.

    Args:
        storage: Backend for reading weights / prices and persisting proposals.
        as_of: Date prefix to locate portfolio_weights and price snapshots.
        portfolio_id: Portfolio identifier.
        portfolio_value: Total portfolio value in base currency.
        seed_universe_path: Path to seed universe CSV (unused currently, reserved).

    Returns:
        RebalanceResult with trade proposals DataFrame.
    """
    # 1. Read target weights from the latest portfolio_weights snapshot
    target_weights = _read_latest_weights(storage=storage, portfolio_id=portfolio_id)
    if target_weights is None:
        raise ValueError(
            f"No portfolio_weights snapshot found for portfolio_id={portfolio_id!r}. "
            "Run 'alpha construct' first."
        )

    # 2. Read prior weights (the second-most-recent snapshot, if any)
    prior_weights = _read_prior_weights(storage=storage, portfolio_id=portfolio_id)

    # 3. Read latest prices for all symbols involved
    all_symbols = set(target_weights.keys())
    if prior_weights:
        all_symbols |= set(prior_weights.keys())
    prices = _read_latest_prices(storage=storage, symbols=all_symbols, as_of=as_of)

    # 4. Generate trade proposals
    proposals = _generate_proposals(
        target_weights=target_weights,
        prior_weights=prior_weights or {},
        prices=prices,
        portfolio_value=portfolio_value,
        portfolio_id=portfolio_id,
        as_of=as_of,
    )

    # 5. Persist proposals as snapshot
    run_as_of = datetime.now(tz=UTC)
    snapshot_path = storage.write_normalized_snapshot(
        dataset="trade_proposals",
        as_of=run_as_of,
        rows=proposals.to_dict(orient="records") if not proposals.empty else [],
    )
    storage.register_snapshot(
        dataset="trade_proposals",
        as_of=run_as_of,
        snapshot_path=snapshot_path,
        row_count=len(proposals),
        metadata={
            "portfolio_id": portfolio_id,
            "requested_as_of": as_of,
            "portfolio_value": portfolio_value,
        },
    )

    buys = int((proposals["side"] == "buy").sum()) if not proposals.empty else 0
    sells = int((proposals["side"] == "sell").sum()) if not proposals.empty else 0
    turnover = float(proposals["abs_weight_change"].sum()) / 2.0 if not proposals.empty else 0.0

    return RebalanceResult(
        as_of=as_of,
        portfolio_id=portfolio_id,
        portfolio_value=portfolio_value,
        trades_count=len(proposals),
        buys=buys,
        sells=sells,
        estimated_turnover=round(turnover, 4),
        snapshot_path=snapshot_path,
        proposals=proposals,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_latest_weights(*, storage: StorageBackend, portfolio_id: str) -> dict[str, float] | None:
    """Read the most recent portfolio_weights snapshot."""
    snapshots = storage.list_snapshots(dataset_filter="portfolio_weights")
    if not snapshots:
        return None
    latest = max(snapshots, key=lambda s: str(s["as_of"]))
    try:
        df = pd.read_parquet(latest["snapshot_path"])
    except (FileNotFoundError, KeyError):
        return None
    if df.empty:
        return None
    if "portfolio_id" in df.columns:
        df = df[df["portfolio_id"] == portfolio_id]
    if df.empty:
        return None
    return dict(zip(df["symbol"], df["target_weight"].astype(float), strict=False))


def _read_prior_weights(*, storage: StorageBackend, portfolio_id: str) -> dict[str, float] | None:
    """Read the second-most-recent portfolio_weights snapshot (current holdings proxy)."""
    snapshots = storage.list_snapshots(dataset_filter="portfolio_weights")
    if len(snapshots) < 2:
        return None
    sorted_snaps = sorted(snapshots, key=lambda s: str(s["as_of"]), reverse=True)
    prior = sorted_snaps[1]
    try:
        df = pd.read_parquet(prior["snapshot_path"])
    except (FileNotFoundError, KeyError):
        return None
    if df.empty:
        return None
    if "portfolio_id" in df.columns:
        df = df[df["portfolio_id"] == portfolio_id]
    if df.empty:
        return None
    return dict(zip(df["symbol"], df["target_weight"].astype(float), strict=False))


def _read_latest_prices(
    *,
    storage: StorageBackend,
    symbols: set[str],
    as_of: str,
) -> dict[str, float]:
    """Read the latest close price for each symbol."""
    prices: dict[str, float] = {}
    for symbol in symbols:
        dataset = f"{symbol.lower()}_prices"
        try:
            df = storage.read_snapshot(dataset=dataset, as_of=as_of)
        except FileNotFoundError:
            continue
        if df.empty:
            continue
        # Use adjusted_close if available, else close
        close_col = "adjusted_close" if "adjusted_close" in df.columns else "close"
        if "date" in df.columns:
            df = df.sort_values("date")
        last_price = df[close_col].dropna().iloc[-1] if not df[close_col].dropna().empty else None
        if last_price is not None:
            prices[symbol] = float(last_price)
    return prices


def _generate_proposals(
    *,
    target_weights: dict[str, float],
    prior_weights: dict[str, float],
    prices: dict[str, float],
    portfolio_value: float,
    portfolio_id: str,
    as_of: str,
) -> pd.DataFrame:
    """Compute buy/sell proposals from weight changes."""
    all_symbols = set(target_weights.keys()) | set(prior_weights.keys())
    rows: list[dict] = []

    for symbol in sorted(all_symbols):
        target_w = target_weights.get(symbol, 0.0)
        prior_w = prior_weights.get(symbol, 0.0)
        weight_change = target_w - prior_w

        # Skip negligible changes (< 0.01% of portfolio)
        if abs(weight_change) < 0.0001:
            continue

        price = prices.get(symbol)
        if price and price > 0:
            target_value = target_w * portfolio_value
            prior_value = prior_w * portfolio_value
            trade_value = abs(target_value - prior_value)
            shares = trade_value / price
        else:
            trade_value = abs(weight_change) * portfolio_value
            shares = 0.0
            price = 0.0

        side = "buy" if weight_change > 0 else "sell"
        rows.append(
            {
                "portfolio_id": portfolio_id,
                "trade_date": as_of,
                "symbol": symbol,
                "side": side,
                "weight_change": round(weight_change, 6),
                "abs_weight_change": round(abs(weight_change), 6),
                "shares": round(shares, 2),
                "price_estimate": round(price, 4) if price else 0.0,
                "estimated_value": round(trade_value, 2),
                "reason": "rebalance_to_target",
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "portfolio_id",
                "trade_date",
                "symbol",
                "side",
                "weight_change",
                "abs_weight_change",
                "shares",
                "price_estimate",
                "estimated_value",
                "reason",
            ]
        )
    return (
        pd.DataFrame(rows).sort_values("abs_weight_change", ascending=False).reset_index(drop=True)
    )
