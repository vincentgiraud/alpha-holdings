"""Config-driven equity scoring over stored snapshots.

This Phase 3 scoring slice computes transparent factor contributions from daily
price snapshots and, when available, persisted fundamentals snapshots. Symbols
without fundamentals coverage remain scoreable with explicit degraded flags.
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

        fundamentals = _read_fundamentals_snapshot(storage=storage, symbol=symbol, as_of=as_of)
        factor_row = _compute_factor_row(
            symbol=symbol,
            prices=prices,
            fundamentals=fundamentals,
            lookback_days=lookback_days,
        )
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
            "factors": [
                "momentum",
                "low_volatility",
                "liquidity",
                "profitability",
                "balance_sheet_quality",
                "cash_flow_quality",
            ],
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
    *,
    symbol: str,
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame | None,
    lookback_days: int,
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

    fundamentals_metrics = _compute_fundamentals_metrics(fundamentals)

    return {
        "symbol": symbol,
        "momentum": momentum,
        "volatility": volatility,
        "avg_dollar_volume": avg_dollar_volume,
        **fundamentals_metrics,
    }


def _apply_factor_contributions(scores: pd.DataFrame) -> None:
    scores["factor_momentum"] = _zscore(scores["momentum"])
    scores["factor_low_volatility"] = _zscore(-scores["volatility"])
    scores["factor_liquidity"] = _zscore(scores["avg_dollar_volume"])
    fundamentals_mask = scores["has_fundamentals"].astype(bool)
    scores["factor_profitability"] = 0.0
    scores["factor_balance_sheet_quality"] = 0.0
    scores["factor_cash_flow_quality"] = 0.0
    if fundamentals_mask.any():
        scores.loc[fundamentals_mask, "factor_profitability"] = _zscore(
            scores.loc[fundamentals_mask, "profitability"]
        )
        scores.loc[fundamentals_mask, "factor_balance_sheet_quality"] = _zscore(
            scores.loc[fundamentals_mask, "balance_sheet_quality"]
        )
        scores.loc[fundamentals_mask, "factor_cash_flow_quality"] = _zscore(
            scores.loc[fundamentals_mask, "cash_flow_quality"]
        )

    factor_columns = [
        "factor_momentum",
        "factor_low_volatility",
        "factor_liquidity",
        "factor_profitability",
        "factor_balance_sheet_quality",
        "factor_cash_flow_quality",
    ]
    scores["composite_score"] = scores[factor_columns].mean(axis=1)


def _read_fundamentals_snapshot(
    *, storage: StorageBackend, symbol: str, as_of: str
) -> pd.DataFrame | None:
    dataset = f"{symbol.lower()}_fundamentals"
    try:
        return storage.read_snapshot(dataset=dataset, as_of=as_of)
    except FileNotFoundError:
        return None


def _compute_fundamentals_metrics(fundamentals: pd.DataFrame | None) -> dict[str, object]:
    if fundamentals is None or fundamentals.empty:
        return {
            "profitability": 0.0,
            "balance_sheet_quality": 0.0,
            "cash_flow_quality": 0.0,
            "has_fundamentals": False,
        }

    ordered = (
        fundamentals.sort_values("period_end_date", ascending=False)
        if "period_end_date" in fundamentals.columns
        else fundamentals
    )
    latest = ordered.iloc[0]

    net_income = _coerce_float(latest.get("net_income"))
    revenue = _coerce_float(latest.get("revenue"))
    debt_to_equity = _coerce_float(latest.get("debt_to_equity"))
    current_ratio = _coerce_float(latest.get("current_ratio"))
    free_cash_flow = _coerce_float(latest.get("free_cash_flow"))

    profitability = 0.0
    if revenue is not None and revenue > 0 and net_income is not None:
        profitability = net_income / revenue
    elif net_income is not None:
        profitability = net_income
    balance_sheet_quality = 0.0
    if debt_to_equity is not None:
        balance_sheet_quality += -debt_to_equity
    if current_ratio is not None:
        balance_sheet_quality += current_ratio
    cash_flow_quality = free_cash_flow if free_cash_flow is not None else 0.0

    return {
        "profitability": float(profitability or 0.0),
        "balance_sheet_quality": float(balance_sheet_quality),
        "cash_flow_quality": float(cash_flow_quality),
        "has_fundamentals": True,
    }


def _coerce_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _zscore(series: pd.Series) -> pd.Series:
    std = float(series.std(ddof=0))
    if std == 0.0 or len(series) == 1:
        return pd.Series([0.0] * len(series), index=series.index)
    return (series - float(series.mean())) / std
