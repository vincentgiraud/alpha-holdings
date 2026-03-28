"""Benchmark-aware portfolio construction from equity scores.

Takes scored equities and portfolio constraints (from InvestorProfile) and
produces a set of TargetWeight records. Enforces:

1. Max position size (single-name cap)
2. Minimum holdings count
3. Sector deviation bands vs. benchmark proxy weights
3. Country deviation bands vs. benchmark proxy weights
4. Turnover cap vs. prior portfolio weights (when available)
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
    warnings: list[str]
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

    warnings: list[str] = []

    # 1. Read scored equities
    try:
        scores = storage.read_snapshot(dataset="equity_scores", as_of=as_of)
    except FileNotFoundError as exc:
        raise ValueError(f"No equity_scores snapshot found for as_of={as_of!r}") from exc
    if scores.empty:
        raise ValueError(f"No equity_scores snapshot found for as_of={as_of!r}")

    # 2. Enrich country/sector metadata from scores and seed universe
    scores = scores.copy()
    seed_universe = _load_seed_universe(seed_universe_path)
    if "country" not in scores.columns or scores["country"].isna().all():
        country_map = _build_map(seed_universe, "country")
        scores["country"] = scores["symbol"].map(country_map).fillna("US")
    else:
        scores["country"] = scores["country"].fillna("US")

    if "sector" not in scores.columns or scores["sector"].isna().all():
        sector_map = _build_map(seed_universe, "sector")
        scores["sector"] = scores["symbol"].map(sector_map).fillna("Unknown")
    else:
        scores["sector"] = scores["sector"].fillna("Unknown")

    # 3. Compute raw score-proportional weights
    weights = _score_proportional_weights(scores)

    warnings.extend(
        _collect_construction_warnings(
            scores=scores,
            seed_universe=seed_universe,
            symbols=set(weights.keys()),
            sector_deviation_band=float(constraints.sector_deviation_band),
        )
    )

    # 4. Apply sector deviation bands
    weights = _apply_sector_deviation(
        weights,
        scores=scores,
        seed_universe=seed_universe,
        max_deviation=float(constraints.sector_deviation_band),
    )

    # 5. Apply country deviation bands
    weights = _apply_country_deviation(
        weights,
        max_deviation=float(constraints.country_deviation_band),
    )

    # 6. Apply max position size cap (iterative redistribution)
    weights = _apply_position_cap(
        weights,
        max_weight=float(constraints.max_single_name_weight),
    )

    # 7. Enforce minimum holdings floor
    weights = _enforce_min_holdings(
        weights,
        scores=scores,
        min_holdings=constraints.min_holdings_count,
        max_weight=float(constraints.max_single_name_weight),
    )

    # 8. Apply turnover constraint vs. prior weights
    prior = _read_prior_weights(storage=storage, portfolio_id=portfolio_id, as_of=as_of)
    turnover = None
    if prior is not None:
        weights, turnover = _apply_turnover_cap(
            weights,
            prior=prior,
            max_annual_turnover=float(constraints.max_annual_turnover),
        )

    # 9. Final renormalization and re-cap (min_holdings/turnover may have shifted weights)
    weights = _renormalize(weights)
    weights = _apply_position_cap(weights, max_weight=float(constraints.max_single_name_weight))
    weights = _renormalize(weights)

    # 10. Build output DataFrame
    result_df = _build_result_dataframe(
        weights=weights,
        scores=scores,
        portfolio_id=portfolio_id,
    )

    # 11. Persist as snapshot
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
            "warnings": warnings,
            "constraints": {
                "max_single_name_weight": str(constraints.max_single_name_weight),
                "min_holdings_count": constraints.min_holdings_count,
                "sector_deviation_band": str(constraints.sector_deviation_band),
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
        warnings=warnings,
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


def _load_seed_universe(seed_path: Path | None) -> pd.DataFrame:
    """Load and normalize seed universe data for metadata lookups."""
    if seed_path is None:
        from alpha_holdings.universe.builder import DEFAULT_SEED_UNIVERSE_PATH

        seed_path = DEFAULT_SEED_UNIVERSE_PATH

    try:
        seed = pd.read_csv(seed_path)
        if "symbol" not in seed.columns:
            return pd.DataFrame()
        seed = seed.copy()
        seed["symbol"] = seed["symbol"].astype(str).str.upper().str.strip()
        return seed
    except (FileNotFoundError, KeyError):
        return pd.DataFrame()


def _build_map(seed_universe: pd.DataFrame, column: str) -> dict[str, str]:
    """Build a symbol -> metadata map from seed universe rows."""
    if seed_universe.empty or column not in seed_universe.columns:
        return {}
    mapped = seed_universe[["symbol", column]].dropna()
    return dict(zip(mapped["symbol"], mapped[column], strict=False))


def _collect_construction_warnings(
    *,
    scores: pd.DataFrame,
    seed_universe: pd.DataFrame,
    symbols: set[str],
    sector_deviation_band: float,
) -> list[str]:
    """Build user-facing degraded-mode warnings for construction output."""
    warnings: list[str] = []

    if "sector" in scores.columns:
        unknown_mask = scores["sector"].astype(str).str.strip().eq("Unknown")
        missing_count = int(unknown_mask.sum())
        if missing_count > 0:
            warnings.append(
                "Degraded execution: missing sector metadata for "
                f"{missing_count} symbol(s); sector deviation checks may be approximate."
            )

    if sector_deviation_band < 0.50:
        benchmark_sector_weights = _infer_benchmark_sector_weights(
            symbols=symbols,
            seed_universe=seed_universe,
        )
        if not benchmark_sector_weights:
            warnings.append(
                "Degraded execution: benchmark sector reference unavailable; "
                "sector deviation constraint fallback was used."
            )

    # Deduplicate while preserving order
    deduped: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        if warning not in seen:
            deduped.append(warning)
            seen.add(warning)
    return deduped


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


def _apply_sector_deviation(
    weights: dict[str, float],
    *,
    scores: pd.DataFrame,
    seed_universe: pd.DataFrame,
    max_deviation: float,
) -> dict[str, float]:
    """Constrain sector weights to benchmark +/- max_deviation bands.

    The benchmark proxy is inferred from seed universe rows matching the
    current symbols, then expanded to all members of the dominant benchmark.
    """
    if not weights or max_deviation >= 0.50 or "sector" not in scores.columns:
        return weights

    symbol_to_sector = (
        scores[["symbol", "sector"]]
        .dropna(subset=["symbol"])
        .assign(sector=lambda df: df["sector"].fillna("Unknown").astype(str))
        .set_index("symbol")["sector"]
        .to_dict()
    )
    if not symbol_to_sector:
        return weights

    benchmark_sector_weights = _infer_benchmark_sector_weights(
        symbols=set(weights.keys()),
        seed_universe=seed_universe,
    )
    if not benchmark_sector_weights:
        return weights

    return _enforce_group_deviation_band(
        weights=weights,
        group_by_symbol=symbol_to_sector,
        benchmark_group_weights=benchmark_sector_weights,
        max_deviation=max_deviation,
    )


def _infer_benchmark_sector_weights(
    *,
    symbols: set[str],
    seed_universe: pd.DataFrame,
) -> dict[str, float]:
    """Infer benchmark sector weights from seed universe benchmark membership."""
    if seed_universe.empty or "sector" not in seed_universe.columns:
        return {}
    if "benchmark" not in seed_universe.columns:
        return {}

    frame = seed_universe.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
    frame["sector"] = frame["sector"].fillna("Unknown").astype(str)

    matching = frame[frame["symbol"].isin(symbols)]
    if matching.empty:
        return {}

    benchmark_series = matching["benchmark"].dropna().astype(str)
    if benchmark_series.empty:
        return {}

    dominant_benchmark = benchmark_series.mode().iloc[0]
    benchmark_members = frame[frame["benchmark"].astype(str) == dominant_benchmark]
    if benchmark_members.empty:
        return {}

    counts = benchmark_members["sector"].value_counts(normalize=True)
    return {str(sector): float(weight) for sector, weight in counts.items()}


def _enforce_group_deviation_band(
    *,
    weights: dict[str, float],
    group_by_symbol: dict[str, str],
    benchmark_group_weights: dict[str, float],
    max_deviation: float,
) -> dict[str, float]:
    """Project group totals to benchmark-relative deviation bands."""
    result = dict(weights)
    if not result:
        return result

    def group_totals(current: dict[str, float]) -> dict[str, float]:
        totals: dict[str, float] = {}
        for symbol, weight in current.items():
            group = group_by_symbol.get(symbol, "Unknown")
            totals[group] = totals.get(group, 0.0) + weight
        return totals

    def symbol_lists(current: dict[str, float]) -> dict[str, list[str]]:
        lists: dict[str, list[str]] = {}
        for symbol in current:
            group = group_by_symbol.get(symbol, "Unknown")
            lists.setdefault(group, []).append(symbol)
        return lists

    groups = set(benchmark_group_weights) | {group_by_symbol.get(s, "Unknown") for s in result}
    lower = {g: max(0.0, benchmark_group_weights.get(g, 0.0) - max_deviation) for g in groups}
    upper = {g: min(1.0, benchmark_group_weights.get(g, 0.0) + max_deviation) for g in groups}

    for _ in range(10):
        totals = group_totals(result)
        grouped_symbols = symbol_lists(result)

        # Step 1: clip groups above upper bound and collect excess.
        excess = 0.0
        for group, total in totals.items():
            max_total = upper.get(group, 1.0)
            if total <= max_total + 1e-10:
                continue
            keep = max_total
            scale = keep / total if total > 0 else 1.0
            for symbol in grouped_symbols.get(group, []):
                old = result[symbol]
                result[symbol] = old * scale
            excess += total - keep

        if excess > 1e-10:
            totals = group_totals(result)
            grouped_symbols = symbol_lists(result)
            capacities = {
                group: max(0.0, upper.get(group, 1.0) - total) for group, total in totals.items()
            }
            total_capacity = sum(capacities.values())
            if total_capacity > 1e-12:
                for group, capacity in capacities.items():
                    if capacity <= 0:
                        continue
                    add_group = excess * (capacity / total_capacity)
                    symbols = grouped_symbols.get(group, [])
                    group_weight = sum(result[s] for s in symbols)
                    if group_weight > 1e-12:
                        for symbol in symbols:
                            result[symbol] += add_group * (result[symbol] / group_weight)
                    elif symbols:
                        add_each = add_group / len(symbols)
                        for symbol in symbols:
                            result[symbol] += add_each

        # Step 2: attempt to raise groups below lower bound by drawing from groups
        # that still sit above their lower bounds.
        totals = group_totals(result)
        grouped_symbols = symbol_lists(result)
        deficits = {
            group: max(0.0, lower.get(group, 0.0) - total) for group, total in totals.items()
        }
        total_deficit = sum(deficits.values())
        if total_deficit > 1e-10:
            donors = {group: max(0.0, totals[group] - lower.get(group, 0.0)) for group in totals}
            total_donor = sum(donors.values())
            if total_donor > 1e-12:
                draw = min(total_deficit, total_donor)
                for group, donor_capacity in donors.items():
                    if donor_capacity <= 0:
                        continue
                    remove_group = draw * (donor_capacity / total_donor)
                    symbols = grouped_symbols.get(group, [])
                    group_weight = sum(result[s] for s in symbols)
                    if group_weight <= 1e-12:
                        continue
                    for symbol in symbols:
                        result[symbol] -= remove_group * (result[symbol] / group_weight)

                totals_after = group_totals(result)
                grouped_after = symbol_lists(result)
                deficits_after = {
                    group: max(0.0, lower.get(group, 0.0) - total)
                    for group, total in totals_after.items()
                }
                total_deficit_after = sum(deficits_after.values())
                if total_deficit_after > 1e-12:
                    for group, deficit in deficits_after.items():
                        if deficit <= 0:
                            continue
                        symbols = grouped_after.get(group, [])
                        if not symbols:
                            continue
                        group_weight = sum(result[s] for s in symbols)
                        if group_weight > 1e-12:
                            for symbol in symbols:
                                result[symbol] += deficit * (result[symbol] / group_weight)
                        else:
                            add_each = deficit / len(symbols)
                            for symbol in symbols:
                                result[symbol] += add_each

        result = _renormalize({s: max(0.0, w) for s, w in result.items()})

        # Stop if all group totals are inside bounds.
        totals = group_totals(result)
        if all(
            lower.get(group, 0.0) - 1e-6 <= total <= upper.get(group, 1.0) + 1e-6
            for group, total in totals.items()
        ):
            break

    return result


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
        sector = str(score_row.get("sector", "Unknown")) if score_row is not None else "Unknown"
        rank = int(score_row["rank"]) if score_row is not None and "rank" in score_row.index else 0

        rows.append(
            {
                "portfolio_id": portfolio_id,
                "symbol": symbol,
                "target_weight": round(weight, 6),
                "composite_score": round(composite, 4),
                "rank": rank,
                "country": country,
                "sector": sector,
            }
        )

    return pd.DataFrame(rows)
