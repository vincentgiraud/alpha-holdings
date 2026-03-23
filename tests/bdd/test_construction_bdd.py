"""BDD scenarios for portfolio construction behavior."""

from datetime import UTC, datetime
from decimal import Decimal

from pytest_bdd import given, parsers, scenarios, then, when

from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.domain.investor_profile import (
    FireVariant,
    InvestorProfile,
    PortfolioConstraints,
    ProfileToConstraints,
    WithdrawalPattern,
)
from alpha_holdings.portfolio.construction import construct_portfolio

scenarios("features/construction.feature")

AS_OF_DT = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
AS_OF_STR = "2026-03-23"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path):
    return LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )


def _seed_scores(backend, scores: dict[str, float]) -> None:
    """Write an equity_scores snapshot from symbol -> composite_score mapping."""
    rows = [
        {
            "symbol": sym,
            "composite_score": score,
            "rank": i + 1,
            "country": "US",
            "has_fundamentals": True,
            "factor_momentum": score * 0.4,
            "factor_low_volatility": score * 0.3,
            "factor_liquidity": score * 0.3,
            "factor_profitability": 0.0,
            "factor_balance_sheet_quality": 0.0,
            "factor_cash_flow_quality": 0.0,
        }
        for i, (sym, score) in enumerate(sorted(scores.items(), key=lambda kv: kv[1], reverse=True))
    ]
    path = backend.write_normalized_snapshot(dataset="equity_scores", as_of=AS_OF_DT, rows=rows)
    backend.register_snapshot(
        dataset="equity_scores",
        as_of=AS_OF_DT,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"source": "test"},
    )


def _default_constraints() -> PortfolioConstraints:
    profile = InvestorProfile(
        profile_id="bdd_construct",
        fire_variant=FireVariant.FAT_FIRE,
        risk_appetite=4,
        horizon_years=20,
        withdrawal_pattern=WithdrawalPattern.COMPOUND_ONLY,
        crypto_enabled=False,
    )
    return ProfileToConstraints.resolve(profile)


# ---------------------------------------------------------------------------
# Scenario: weights sum to 1.0
# ---------------------------------------------------------------------------


@given(
    'a storage backend with equity scores for "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"',
    target_fixture="backend",
)
def given_five_scored_symbols(tmp_path):
    backend = _make_backend(tmp_path)
    _seed_scores(backend, {"AAPL": 0.9, "MSFT": 0.8, "GOOGL": 0.7, "AMZN": 0.6, "NVDA": 0.5})
    return backend


@when(
    parsers.parse('I construct a portfolio for as-of date "{as_of}"'),
    target_fixture="construction_result",
)
def when_construct_portfolio(backend, as_of):
    # Use generous max weight so score proportionality is preserved across scenarios
    constraints = _default_constraints().model_copy(
        update={"max_single_name_weight": Decimal("0.5")}
    )
    return construct_portfolio(storage=backend, as_of=as_of, constraints=constraints)


@then(parsers.parse("the constructed portfolio target weights sum to 1.0 within tolerance {tol:f}"))
def then_weights_sum_to_one(construction_result, tol):
    total = float(construction_result.total_weight)
    assert abs(total - 1.0) <= tol


# ---------------------------------------------------------------------------
# Scenario: no single position exceeds max weight
# ---------------------------------------------------------------------------


@when(
    parsers.parse("I construct a portfolio with max single name weight {max_w:f}"),
    target_fixture="construction_result",
)
def when_construct_with_max_weight(backend, max_w):
    constraints = _default_constraints().model_copy(
        update={"max_single_name_weight": Decimal(str(max_w))}
    )
    return construct_portfolio(storage=backend, as_of=AS_OF_STR, constraints=constraints)


@then(parsers.parse("no symbol in the portfolio has weight greater than {max_w:f}"))
def then_no_weight_exceeds(construction_result, max_w):
    for _, row in construction_result.weights.iterrows():
        assert float(row["target_weight"]) <= max_w + 1e-6


# ---------------------------------------------------------------------------
# Scenario: minimum holdings floor
# ---------------------------------------------------------------------------


@given(
    "a storage backend with equity scores for 7 symbols where one dominates",
    target_fixture="backend",
)
def given_seven_symbols_one_dominant(tmp_path):
    """7-symbol universe: STAR dominates, others score low.

    Without a min_holdings floor the engine would concentrate heavily into STAR.
    With floor ≥ 5 the engine must include at least 5 of the 7 symbols.
    """
    backend = _make_backend(tmp_path)
    _seed_scores(
        backend,
        {"STAR": 1.0, "A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2, "E": 0.2, "F": 0.2},
    )
    return backend


@when(
    parsers.parse("I construct a portfolio requiring at least {min_h:d} holdings"),
    target_fixture="construction_result",
)
def when_construct_with_min_holdings(backend, min_h):
    constraints = _default_constraints().model_copy(update={"min_holdings_count": min_h})
    return construct_portfolio(storage=backend, as_of=AS_OF_STR, constraints=constraints)


@then(parsers.parse("the constructed portfolio contains at least {min_h:d} holdings"))
def then_min_holdings(construction_result, min_h):
    assert construction_result.holdings_count >= min_h


# ---------------------------------------------------------------------------
# Scenario: higher-scoring symbol gets more weight
# ---------------------------------------------------------------------------


@given(
    'a storage backend with equity scores where "STRONG" scores higher than "WEAK"',
    target_fixture="backend",
)
def given_strong_and_weak(tmp_path):
    backend = _make_backend(tmp_path)
    # 3 filler symbols so the min_holdings floor is satisfied without capping STRONG
    _seed_scores(
        backend,
        {"STRONG": 1.0, "WEAK": 0.1, "FIL1": 0.3, "FIL2": 0.3, "FIL3": 0.3},
    )
    return backend


@then(parsers.parse('"{better}" has a higher target weight than "{worse}"'))
def then_better_has_higher_weight(construction_result, better, worse):
    weights = construction_result.weights.set_index("symbol")["target_weight"]
    assert float(weights[better]) > float(weights[worse])
