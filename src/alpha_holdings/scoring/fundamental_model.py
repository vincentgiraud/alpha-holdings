"""Config-driven equity scoring over stored snapshots.

This is the first Phase 3 scoring slice. It computes transparent factor
contributions from daily price snapshots so the workflow is deterministic and
testable while richer fundamentals ingestion is expanded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from alpha_holdings.data.storage import StorageBackend
from alpha_holdings.universe.builder import build_liquid_universe_from_snapshots


@dataclass(slots=True)
class ScoreSummary:
    """Outcome of one score run."""

    as_of: str
    universe_size: int
    securities_scored: int
    skipped: list[str]
    snapshot_path: Path
    scores: pd.DataFrame


def score_equities_from_snapshots(
    *,
    storage: StorageBackend,
    as_of: str,
    lookback_days: int,
    min_avg_dollar_volume: float,
    seed_universe_path: Path | None = None,
    base_currency: str = "USD",
) -> ScoreSummary:
    """Score all liquid equities discovered in snapshots for an as-of date prefix."""
    universe = build_liquid_universe_from_snapshots(
        storage=storage,
        as_of=as_of,
        lookback_days=lookback_days,
        min_avg_dollar_volume=min_avg_dollar_volume,
        seed_universe_path=seed_universe_path,
        base_currency=base_currency,
    )
    if not universe.symbols:
        raise ValueError(f"No liquid symbols available for as_of={as_of!r}.")

    rows: list[dict[str, object]] = []
    skipped: list[str] = []

    for symbol in universe.symbols:
        dataset = f"{symbol.lower()}_prices"
        try:
            prices = storage.read_snapshot(dataset=dataset, as_of=as_of)
        except FileNotFoundError:
            skipped.append(symbol)
            continue

        factor_row = _compute_factor_row(symbol=symbol, prices=prices, lookback_days=lookback_days)
        if factor_row is None:
            skipped.append(symbol)
            continue
        rows.append(factor_row)

    if not rows:
        raise ValueError("No symbols had enough data to compute scores.")

    scores = pd.DataFrame(rows)
    _apply_factor_contributions(scores)

    run_as_of = datetime.now(tz=UTC)
    snapshot_path = storage.write_normalized_snapshot(
        dataset="equity_scores",
        as_of=run_as_of,
        rows=scores.to_dict(orient="records"),
    )
    storage.register_snapshot(
        dataset="equity_scores",
        as_of=run_as_of,
        snapshot_path=snapshot_path,
        row_count=len(scores),
        metadata={
            "requested_as_of": as_of,
            "lookback_days": lookback_days,
            "min_avg_dollar_volume": min_avg_dollar_volume,
            "factors": ["momentum", "low_volatility", "liquidity"],
        },
    )

    ordered = scores.sort_values("composite_score", ascending=False).reset_index(drop=True)
    ordered.insert(0, "rank", ordered.index + 1)
    return ScoreSummary(
        as_of=as_of,
        universe_size=len(universe.symbols),
        securities_scored=len(ordered),
        skipped=sorted(set(skipped)),
        snapshot_path=snapshot_path,
        scores=ordered,
    )


def _compute_factor_row(
    *, symbol: str, prices: pd.DataFrame, lookback_days: int
) -> dict[str, object] | None:
    if prices.empty:
        return None
    required = {"close", "volume"}
    if not required.issubset(prices.columns):
        return None

    frame = prices.sort_values("date") if "date" in prices.columns else prices
    window = frame.tail(lookback_days + 1)
    if len(window) < lookback_days + 1:
        return None

    close = window["adjusted_close"] if "adjusted_close" in window.columns else window["close"]
    close = close.fillna(window["close"]) if hasattr(close, "fillna") else close
    returns = close.pct_change().dropna()
    if returns.empty:
        return None

    momentum = float((close.iloc[-1] / close.iloc[0]) - 1.0)
    volatility = float(returns.std(ddof=0))
    avg_dollar_volume = float(
        (window.tail(lookback_days)["close"] * window.tail(lookback_days)["volume"]).mean()
    )

    return {
        "symbol": symbol,
        "momentum": momentum,
        "volatility": volatility,
        "avg_dollar_volume": avg_dollar_volume,
    }


def _apply_factor_contributions(scores: pd.DataFrame) -> None:
    scores["factor_momentum"] = _zscore(scores["momentum"])
    scores["factor_low_volatility"] = _zscore(-scores["volatility"])
    scores["factor_liquidity"] = _zscore(scores["avg_dollar_volume"])
    scores["composite_score"] = scores[
        ["factor_momentum", "factor_low_volatility", "factor_liquidity"]
    ].mean(axis=1)


def _zscore(series: pd.Series) -> pd.Series:
    std = float(series.std(ddof=0))
    if std == 0.0 or len(series) == 1:
        return pd.Series([0.0] * len(series), index=series.index)
    return (series - float(series.mean())) / std
