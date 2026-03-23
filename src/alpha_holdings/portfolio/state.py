"""Portfolio holdings state management.

Tracks per-security shares, average cost basis, and realized gains
by applying trade proposals to the current holdings snapshot.

Design:
- ``read_current_holdings`` loads the most recent ``holdings_snapshot`` from storage.
- ``apply_trades`` computes the new holdings after a set of proposals using
  weighted-average cost basis for buys and FIFO-equivalent realized gains for sells.
- ``snapshot_holdings`` orchestrates the full update-and-persist cycle called by
  the rebalance engine after trade proposals are generated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from alpha_holdings.data.storage import StorageBackend

# ---------------------------------------------------------------------------
# In-memory holding record
# ---------------------------------------------------------------------------


@dataclass
class HoldingRecord:
    """In-memory representation of one active position in the portfolio."""

    symbol: str
    shares: float
    book_cost_per_share: float
    realized_gain_total: float = field(default=0.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_current_holdings(
    *,
    storage: StorageBackend,
    portfolio_id: str,
) -> dict[str, HoldingRecord]:
    """Return the most recent holdings state for *portfolio_id*.

    Reads the latest ``holdings_snapshot`` registered for the given portfolio.
    Returns an empty dict when no snapshot exists (first-run scenario).
    """
    dataset = _holdings_dataset(portfolio_id)
    snapshots = storage.list_snapshots(dataset_filter=dataset)
    if not snapshots:
        return {}
    latest = max(snapshots, key=lambda s: str(s["as_of"]))
    try:
        df = pd.read_parquet(latest["snapshot_path"])
    except (FileNotFoundError, KeyError):
        return {}
    if df.empty:
        return {}
    return {
        row["symbol"]: HoldingRecord(
            symbol=row["symbol"],
            shares=float(row["shares"]),
            book_cost_per_share=float(row["book_cost_per_share"]),
            realized_gain_total=float(row.get("realized_gain_total", 0.0)),
        )
        for _, row in df.iterrows()
    }


def apply_trades(
    *,
    current: dict[str, HoldingRecord],
    proposals: pd.DataFrame,
) -> dict[str, HoldingRecord]:
    """Apply trade proposals to *current* holdings and return updated holdings.

    - **Buy**: increases shares; book cost is updated to the weighted-average of
      the existing cost and the execution price.
    - **Sell**: reduces shares; realized gain for the lot is crystallised using
      (execution_price - book_cost_per_share) x shares_sold.

    Positions not mentioned in *proposals* carry forward unchanged.

    Args:
        current: Current holdings keyed by symbol.
        proposals: DataFrame with at least ``[symbol, side, shares, price_estimate]``
            columns (as produced by :func:`~alpha_holdings.portfolio.rebalance.rebalance_portfolio`).

    Returns:
        Updated holdings dict (symbol → HoldingRecord).  Zero-share positions are
        kept; callers may filter them out as needed.
    """
    # Work on a shallow copy so callers retain the original dict.
    updated: dict[str, HoldingRecord] = {
        sym: HoldingRecord(
            symbol=h.symbol,
            shares=h.shares,
            book_cost_per_share=h.book_cost_per_share,
            realized_gain_total=h.realized_gain_total,
        )
        for sym, h in current.items()
    }

    if proposals.empty:
        return updated

    for _, row in proposals.iterrows():
        symbol: str = str(row["symbol"])
        side: str = str(row["side"])
        trade_shares = float(row["shares"])
        price = float(row["price_estimate"])

        if trade_shares <= 0 or price <= 0:
            continue

        if side == "buy":
            existing = updated.get(symbol)
            if existing is None or existing.shares <= 0:
                updated[symbol] = HoldingRecord(
                    symbol=symbol,
                    shares=trade_shares,
                    book_cost_per_share=price,
                    realized_gain_total=0.0 if existing is None else existing.realized_gain_total,
                )
            else:
                total_shares = existing.shares + trade_shares
                new_cost = (
                    existing.shares * existing.book_cost_per_share + trade_shares * price
                ) / total_shares
                updated[symbol] = HoldingRecord(
                    symbol=symbol,
                    shares=total_shares,
                    book_cost_per_share=new_cost,
                    realized_gain_total=existing.realized_gain_total,
                )

        elif side == "sell":
            existing = updated.get(symbol)
            if existing is None:
                continue
            # Cannot sell more than held (rounding guard)
            sold_shares = min(trade_shares, existing.shares)
            realized = (price - existing.book_cost_per_share) * sold_shares
            updated[symbol] = HoldingRecord(
                symbol=symbol,
                shares=existing.shares - sold_shares,
                book_cost_per_share=existing.book_cost_per_share,
                realized_gain_total=existing.realized_gain_total + realized,
            )

    return updated


def snapshot_holdings(
    *,
    storage: StorageBackend,
    portfolio_id: str,
    proposals: pd.DataFrame,
    prices: dict[str, float],
    portfolio_value: float,
    as_of: datetime | None = None,
) -> Path:
    """Apply trades and persist a ``holdings_snapshot`` for *portfolio_id*.

    This is the primary entry point called by the rebalance engine.  It:

    1. Reads the previous ``holdings_snapshot`` (or starts from zero).
    2. Applies *proposals* with :func:`apply_trades`.
    3. Enriches each active position with current price, market value, weight,
       cost-basis total, and unrealised/realised gain.
    4. Writes a new ``holdings_snapshot`` parquet file and registers it.

    Args:
        storage: Storage backend.
        portfolio_id: Portfolio identifier.
        proposals: Trade proposals DataFrame (may be empty for no-op).
        prices: Latest prices keyed by symbol (used for market-value calculation).
        portfolio_value: Total portfolio value used for weight denominator.
        as_of: Snapshot timestamp; defaults to UTC now.

    Returns:
        Path to the persisted parquet file.
    """
    if as_of is None:
        as_of = datetime.now(tz=UTC)

    current = read_current_holdings(storage=storage, portfolio_id=portfolio_id)
    updated = apply_trades(current=current, proposals=proposals)

    # Drop fully exited positions (epsilon guard for floating-point residue)
    active = {sym: h for sym, h in updated.items() if h.shares > 1e-8}

    # Compute portfolio-level totals for weight calculation
    total_market_value = sum(
        h.shares * prices.get(h.symbol, h.book_cost_per_share) for h in active.values()
    )

    rows = []
    for sym, h in sorted(active.items()):
        current_price = prices.get(sym, h.book_cost_per_share)
        market_value = h.shares * current_price
        cost_basis_total = h.shares * h.book_cost_per_share
        unrealized_gain = market_value - cost_basis_total
        weight = market_value / total_market_value if total_market_value > 0 else 0.0
        rows.append(
            {
                "portfolio_id": portfolio_id,
                "as_of_date": as_of.isoformat(),
                "symbol": sym,
                "shares": round(h.shares, 6),
                "book_cost_per_share": round(h.book_cost_per_share, 6),
                "current_price": round(current_price, 6),
                "market_value": round(market_value, 4),
                "cost_basis_total": round(cost_basis_total, 4),
                "unrealized_gain": round(unrealized_gain, 4),
                "realized_gain_total": round(h.realized_gain_total, 4),
                "weight": round(weight, 6),
            }
        )

    dataset = _holdings_dataset(portfolio_id)
    path = storage.write_normalized_snapshot(
        dataset=dataset,
        as_of=as_of,
        rows=rows,
    )
    storage.register_snapshot(
        dataset=dataset,
        as_of=as_of,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"portfolio_id": portfolio_id, "portfolio_value": portfolio_value},
    )
    return path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _holdings_dataset(portfolio_id: str) -> str:
    """Return the storage dataset name scoped to a portfolio.

    Using a per-portfolio name prevents file-path collisions when multiple
    portfolios both call ``snapshot_holdings`` at the same timestamp.
    """
    return f"holdings_snapshot_{portfolio_id}"
