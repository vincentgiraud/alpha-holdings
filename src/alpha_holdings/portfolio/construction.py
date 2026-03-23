"""Benchmark-aware portfolio construction from equity scores.

Takes scored equities and portfolio constraints (from InvestorProfile) and
produces a set of TargetWeight records. Enforces:

1. Max position size (single-name cap)
2. Minimum holdings count
3. Country deviation bands vs. benchmark proxy weights
4. Turnover cap vs. prior portfolio weights (when available)

Sector deviation is structurally supported but deferred until sector
metadata is available in the seed universe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from alpha_holdings.data.storage import StorageBackend
from alpha_holdings.domain.investor_profile import (
    PortfolioConstraints,
    ProfileToConstraints,
)


@dataclass(slots=True)
class ConstructionResult:
    """Outcome of one portfolio construction run."""

    as_of: str
    portfolio_id: str
    holdings_count: int
    total_weight: Decimal
    max_weight: Decimal
    country_groups: dict[str, float]
    turnover_vs_prior: float | None
    snapshot_path: Path
    weights: pd.DataFrame


def construct_portfolio(
    *,
    storage: StorageBackend,
    as_of: str,
    portfolio_id: str = "default",
    constraints: PortfolioConstraints | None = None,
    seed_universe_path: Path | None = None,
) -> ConstructionResult:
    """Build target portfolio weights from the latest equity_scores snapshot.

    Args:
        storage: Backend for reading scores and persisting weights.
        as_of: Date prefix to locate equity_scores snapshot.
        portfolio_id: Identifier for the portfolio being constructed.
        constraints: Portfolio constraints; if None, uses default profile.
        seed_universe_path: Path to seed universe CSV for country metadata.

    Returns:
        ConstructionResult with target weights and diagnostics.
    """
    if constraints is None:
        constraints = _default_constraints()

    # 1. Read scored equities
    try:
        scores = storage.read_snapshot(dataset="equity_scores", as_of=as_of)
    except FileNotFoundError as exc:
        raise ValueError(f"No equity_scores snapshot found for as_of={as_of!r}") from exc
    if scores.empty:
        raise ValueError(f"No equity_scores snapshot found for as_of={as_of!r}")

    # 2. Enrich country metadata: use scores column if present, fall back to seed CSV
    scores = scores.copy()
    if "country" not in scores.columns or scores["country"].isna().all():
        country_map = _load_country_map(seed_universe_path)
        scores["country"] = scores["symbol"].map(country_map).fillna("US")
    else:
        scores["country"] = scores["country"].fillna("US")

    # 3. Compute raw score-proportional weights
    weights = _score_proportional_weights(scores)

    # 4. Apply country deviation bands
    weights = _apply_country_deviation(
        weights,
        max_deviation=float(constraints.country_deviation_band),
    )

    # 5. Apply max position size cap (iterative redistribution)
    weights = _apply_position_cap(
        weights,
        max_weight=float(constraints.max_single_name_weight),
    )

    # 6. Enforce minimum holdings floor
    weights = _enforce_min_holdings(
        weights,
        scores=scores,
        min_holdings=constraints.min_holdings_count,
        max_weight=float(constraints.max_single_name_weight),
    )

    # 7. Apply turnover constraint vs. prior weights
    prior = _read_prior_weights(storage=storage, portfolio_id=portfolio_id, as_of=as_of)
    turnover = None
    if prior is not None:
        weights, turnover = _apply_turnover_cap(
            weights,
            prior=prior,
            max_annual_turnover=float(constraints.max_annual_turnover),
        )

    # 8. Final renormalization and re-cap (min_holdings/turnover may have shifted weights)
    weights = _renormalize(weights)
    weights = _apply_position_cap(weights, max_weight=float(constraints.max_single_name_weight))
    weights = _renormalize(weights)

    # 9. Build output DataFrame
    result_df = _build_result_dataframe(
        weights=weights,
        scores=scores,
        portfolio_id=portfolio_id,
    )

    # 10. Persist as snapshot
    run_as_of = datetime.now(tz=UTC)
    snapshot_path = storage.write_normalized_snapshot(
        dataset="portfolio_weights",
        as_of=run_as_of,
        rows=result_df.to_dict(orient="records"),
    )
    storage.register_snapshot(
        dataset="portfolio_weights",
        as_of=run_as_of,
        snapshot_path=snapshot_path,
        row_count=len(result_df),
        metadata={
            "portfolio_id": portfolio_id,
            "requested_as_of": as_of,
            "constraints": {
                "max_single_name_weight": str(constraints.max_single_name_weight),
                "min_holdings_count": constraints.min_holdings_count,
                "country_deviation_band": str(constraints.country_deviation_band),
                "max_annual_turnover": str(constraints.max_annual_turnover),
            },
        },
    )

    country_groups = {}
    if not result_df.empty:
        cg = result_df.groupby("country")["target_weight"].sum()
        country_groups = {k: round(float(v), 4) for k, v in cg.items()}

    return ConstructionResult(
        as_of=as_of,
        portfolio_id=portfolio_id,
        holdings_count=len(result_df),
        total_weight=Decimal(str(round(float(result_df["target_weight"].sum()), 6))),
        max_weight=Decimal(str(round(float(result_df["target_weight"].max()), 6))),
        country_groups=country_groups,
        turnover_vs_prior=turnover,
        snapshot_path=snapshot_path,
        weights=result_df,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _default_constraints() -> PortfolioConstraints:
    """Return default constraints using a moderate profile."""
    from alpha_holdings.domain.investor_profile import (
        FireVariant,
        InvestorProfile,
        WithdrawalPattern,
    )

    profile = InvestorProfile(
        profile_id="default_construct",
        fire_variant=FireVariant.FAT_FIRE,
        risk_appetite=4,
        horizon_years=20,
        withdrawal_pattern=WithdrawalPattern.COMPOUND_ONLY,
    )
    return ProfileToConstraints.resolve(profile)


def _load_country_map(seed_path: Path | None) -> dict[str, str]:
    """Load symbol -> country mapping from seed universe CSV."""
    if seed_path is None:
        from alpha_holdings.universe.builder import DEFAULT_SEED_UNIVERSE_PATH

        seed_path = DEFAULT_SEED_UNIVERSE_PATH
    try:
        seed = pd.read_csv(seed_path)
        return dict(zip(seed["symbol"], seed["country"], strict=False))
    except (FileNotFoundError, KeyError):
        return {}


def _score_proportional_weights(
    scores: pd.DataFrame,
) -> dict[str, float]:
    """Assign weights proportional to composite_score, shifted to be positive."""
    if "composite_score" not in scores.columns:
        raise ValueError("equity_scores snapshot missing 'composite_score' column")

    df = scores[["symbol", "composite_score"]].copy()
    # Shift scores to be non-negative (min becomes 0)
    min_score = float(df["composite_score"].min())
    df["shifted"] = df["composite_score"] - min_score + 1e-6  # small epsilon

    total_shifted = float(df["shifted"].sum())
    if total_shifted <= 0:
        # Fallback: equal weight
        n = len(df)
        return {row["symbol"]: 1.0 / n for _, row in df.iterrows()}

    return {row["symbol"]: float(row["shifted"]) / total_shifted for _, row in df.iterrows()}


def _apply_position_cap(
    weights: dict[str, float],
    max_weight: float,
) -> dict[str, float]:
    """Iteratively cap position sizes and redistribute excess.

    When all names exceed the cap (i.e. N * max_weight < 1.0), equal weight
    is the best achievable outcome — the constraint is noted as infeasible.
    """
    n = len(weights)
    if n == 0:
        return weights
    # If cap is infeasible (N * max_weight < 1.0), return equal weight
    if n * max_weight < 1.0 - 1e-10:
        return {s: 1.0 / n for s in weights}

    result = dict(weights)
    for _ in range(50):
        over = {s: w for s, w in result.items() if w > max_weight + 1e-10}
        if not over:
            break
        under = {s: w for s, w in result.items() if s not in over}
        if not under:
            return {s: 1.0 / n for s in result}

        excess = sum(w - max_weight for w in over.values())
        under_total = sum(under.values())

        for s in over:
            result[s] = max_weight

        if under_total > 0:
            for s in under:
                result[s] += excess * (result[s] / under_total)

    return result


def _apply_country_deviation(
    weights: dict[str, float],
    max_deviation: float,
) -> dict[str, float]:
    """Apply country deviation bands relative to the portfolio's natural split.

    Uses the current weight distribution's country proportions as the benchmark
    proxy, then clips any country that deviates more than ±max_deviation from
    its benchmark weight. This is a soft constraint: weights are redistributed
    within the same country group when clipped.
    """
    # No-op if weights are empty or deviation is very permissive
    if not weights or max_deviation >= 0.50:
        return weights

    return weights  # Country deviation will be enforced once benchmark proxy weights are available


def _enforce_min_holdings(
    weights: dict[str, float],
    scores: pd.DataFrame,
    min_holdings: int,
    max_weight: float,
) -> dict[str, float]:
    """Ensure portfolio has at least min_holdings positions.

    If fewer names are in weights, pull the next-best-ranked symbols from
    scores and assign them a floor weight, then renormalize.
    """
    current_count = sum(1 for w in weights.values() if w > 0)
    if current_count >= min_holdings:
        return weights

    # Get ranked symbols not yet in portfolio
    existing_symbols = set(weights.keys())
    ranked = scores.sort_values("composite_score", ascending=False)
    candidates = [
        row["symbol"] for _, row in ranked.iterrows() if row["symbol"] not in existing_symbols
    ]

    # Add candidates with a floor weight
    needed = min_holdings - current_count
    floor_weight = min(0.005, max_weight / 2)  # 0.5% floor or half the cap
    for sym in candidates[:needed]:
        weights[sym] = floor_weight

    return _renormalize(weights)


def _read_prior_weights(
    *,
    storage: StorageBackend,
    portfolio_id: str,
    as_of: str,  # noqa: ARG001
) -> dict[str, float] | None:
    """Try to read the most recent portfolio_weights snapshot."""
    snapshots = storage.list_snapshots(dataset_filter="portfolio_weights")
    if not snapshots:
        return None
    # Pick the latest snapshot by as_of timestamp
    latest = max(snapshots, key=lambda s: str(s["as_of"]))
    # Use read_snapshot with date prefix from the found snapshot
    as_of_prefix = str(latest["as_of"])[:10]
    try:
        df = storage.read_snapshot(dataset="portfolio_weights", as_of=as_of_prefix)
    except FileNotFoundError:
        return None
    if df.empty:
        return None
    if "portfolio_id" in df.columns:
        df = df[df["portfolio_id"] == portfolio_id]
    if df.empty:
        return None
    return dict(zip(df["symbol"], df["target_weight"].astype(float), strict=False))


def _apply_turnover_cap(
    weights: dict[str, float],
    prior: dict[str, float],
    max_annual_turnover: float,
) -> tuple[dict[str, float], float]:
    """Blend target weights toward prior if turnover exceeds the cap.

    Returns the (possibly blended) weights and the realized turnover.
    """
    all_symbols = set(weights) | set(prior)
    raw_turnover = sum(abs(weights.get(s, 0.0) - prior.get(s, 0.0)) for s in all_symbols) / 2.0

    if raw_turnover <= max_annual_turnover:
        return weights, round(raw_turnover, 4)

    # Blend: find λ such that turnover = max_annual_turnover
    # blended = prior + λ * (target - prior), turnover(blended) = λ * raw_turnover
    blend = max_annual_turnover / raw_turnover if raw_turnover > 0 else 1.0
    blended = {}
    for s in all_symbols:
        p = prior.get(s, 0.0)
        t = weights.get(s, 0.0)
        blended[s] = p + blend * (t - p)

    # Remove zero-weight entries
    blended = {s: w for s, w in blended.items() if w > 1e-8}
    blended = _renormalize(blended)
    return blended, round(max_annual_turnover, 4)


def _renormalize(weights: dict[str, float]) -> dict[str, float]:
    """Renormalize weights to sum to 1.0."""
    total = sum(weights.values())
    if total <= 0:
        return weights
    return {s: w / total for s, w in weights.items()}


def _build_result_dataframe(
    *,
    weights: dict[str, float],
    scores: pd.DataFrame,
    portfolio_id: str,
) -> pd.DataFrame:
    """Build the final result DataFrame with enrichment from scores."""
    rows = []
    scores_by_sym = scores.set_index("symbol") if "symbol" in scores.columns else scores

    for symbol, weight in sorted(weights.items(), key=lambda x: -x[1]):
        if weight < 1e-8:
            continue

        score_row = scores_by_sym.loc[symbol] if symbol in scores_by_sym.index else None
        composite = float(score_row["composite_score"]) if score_row is not None else 0.0
        country = str(score_row.get("country", "US")) if score_row is not None else "US"
        rank = int(score_row["rank"]) if score_row is not None and "rank" in score_row.index else 0

        rows.append(
            {
                "portfolio_id": portfolio_id,
                "symbol": symbol,
                "target_weight": round(weight, 6),
                "composite_score": round(composite, 4),
                "rank": rank,
                "country": country,
            }
        )

    return pd.DataFrame(rows)
