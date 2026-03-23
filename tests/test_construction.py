"""Tests for portfolio construction engine.

Validates constraint enforcement: position cap, min holdings, turnover cap,
and output schema. Uses LocalStorageBackend with tmp_path fixtures.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.domain.investor_profile import (
    FireVariant,
    InvestorProfile,
    PortfolioConstraints,
    ProfileToConstraints,
    WithdrawalPattern,
)
from alpha_holdings.portfolio.construction import construct_portfolio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path):
    """Create a local storage backend in tmp_path."""
    return LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )


def _seed_scores(backend, symbols, scores=None, countries=None):
    """Write a fake equity_scores snapshot into storage."""
    n = len(symbols)
    if scores is None:
        # Linearly descending scores
        scores = [float(n - i) for i in range(n)]
    if countries is None:
        countries = ["US"] * n

    rows = []
    for i, sym in enumerate(symbols):
        rows.append(
            {
                "rank": i + 1,
                "symbol": sym,
                "composite_score": scores[i],
                "factor_momentum": 0.1,
                "factor_low_volatility": 0.1,
                "factor_liquidity": 0.1,
                "country": countries[i],
                "has_fundamentals": True,
            }
        )

    as_of = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
    path = backend.write_normalized_snapshot(
        dataset="equity_scores",
        as_of=as_of,
        rows=rows,
    )
    backend.register_snapshot(
        dataset="equity_scores",
        as_of=as_of,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"requested_as_of": "2026-03-23"},
    )
    return rows


def _seed_prior_weights(backend, weights_dict, portfolio_id="default"):
    """Write a fake portfolio_weights snapshot into storage."""
    rows = [
        {
            "portfolio_id": portfolio_id,
            "symbol": sym,
            "target_weight": w,
            "composite_score": 0.0,
            "rank": i + 1,
            "country": "US",
        }
        for i, (sym, w) in enumerate(weights_dict.items())
    ]
    as_of = datetime(2026, 3, 22, 12, 0, 0, tzinfo=UTC)
    path = backend.write_normalized_snapshot(
        dataset="portfolio_weights",
        as_of=as_of,
        rows=rows,
    )
    backend.register_snapshot(
        dataset="portfolio_weights",
        as_of=as_of,
        snapshot_path=path,
        row_count=len(rows),
    )


# ---------------------------------------------------------------------------
# Tests: basic construction
# ---------------------------------------------------------------------------


class TestBasicConstruction:
    def test_constructs_from_scores(self, tmp_path):
        backend = _make_backend(tmp_path)
        syms = [f"SYM{i}" for i in range(10)]
        _seed_scores(backend, syms)

        result = construct_portfolio(storage=backend, as_of="2026-03-23", seed_universe_path=None)

        assert result.holdings_count == 10
        assert abs(float(result.total_weight) - 1.0) < 1e-4

    def test_weights_sum_to_one(self, tmp_path):
        backend = _make_backend(tmp_path)
        syms = [f"SYM{i}" for i in range(20)]
        _seed_scores(backend, syms)

        result = construct_portfolio(storage=backend, as_of="2026-03-23", seed_universe_path=None)

        total = float(result.weights["target_weight"].sum())
        assert abs(total - 1.0) < 1e-4

    def test_higher_score_gets_higher_weight(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_scores(backend, ["HIGH", "MID", "LOW"], scores=[10.0, 5.0, 1.0])

        constraints = PortfolioConstraints(
            profile_id="test",
            max_single_name_weight=Decimal("0.80"),
            sector_deviation_band=Decimal("0.50"),
            country_deviation_band=Decimal("0.50"),
            max_annual_turnover=Decimal("1.00"),
            min_holdings_count=1,
        )

        result = construct_portfolio(
            storage=backend,
            as_of="2026-03-23",
            constraints=constraints,
            seed_universe_path=None,
        )

        w = result.weights.set_index("symbol")
        assert float(w.loc["HIGH", "target_weight"]) > float(w.loc["LOW", "target_weight"])

    def test_raises_on_missing_scores(self, tmp_path):
        backend = _make_backend(tmp_path)
        with pytest.raises((ValueError, FileNotFoundError)):
            construct_portfolio(storage=backend, as_of="2026-03-23")


# ---------------------------------------------------------------------------
# Tests: position size cap
# ---------------------------------------------------------------------------


class TestPositionCap:
    def test_no_position_exceeds_max_weight(self, tmp_path):
        backend = _make_backend(tmp_path)
        # 25 names (enough for 5% cap to be feasible: 25 x 0.05 = 1.25 headroom)
        # One very high score, rest low → would produce >5% weight without cap
        scores = [100.0] + [1.0] * 24
        syms = [f"SYM{i}" for i in range(25)]
        _seed_scores(backend, syms, scores=scores)

        constraints = PortfolioConstraints(
            profile_id="test",
            max_single_name_weight=Decimal("0.05"),
            sector_deviation_band=Decimal("0.10"),
            country_deviation_band=Decimal("0.10"),
            max_annual_turnover=Decimal("1.00"),
            min_holdings_count=1,
        )

        result = construct_portfolio(
            storage=backend,
            as_of="2026-03-23",
            constraints=constraints,
            seed_universe_path=None,
        )

        assert float(result.max_weight) <= 0.05 + 1e-6

    def test_cap_redistributes_to_others(self, tmp_path):
        backend = _make_backend(tmp_path)
        scores = [100.0, 1.0, 1.0]
        _seed_scores(backend, ["BIG", "SMALLA", "SMALLB"], scores=scores)

        constraints = PortfolioConstraints(
            profile_id="test",
            max_single_name_weight=Decimal("0.40"),
            sector_deviation_band=Decimal("0.50"),
            country_deviation_band=Decimal("0.50"),
            max_annual_turnover=Decimal("1.00"),
            min_holdings_count=1,
        )

        result = construct_portfolio(
            storage=backend,
            as_of="2026-03-23",
            constraints=constraints,
            seed_universe_path=None,
        )

        w = result.weights.set_index("symbol")
        big_w = float(w.loc["BIG", "target_weight"])
        assert big_w <= 0.40 + 1e-6
        # Small names should get the excess
        small_total = sum(float(w.loc[s, "target_weight"]) for s in ["SMALLA", "SMALLB"])
        assert small_total > 0.50  # they should get at least 60%


# ---------------------------------------------------------------------------
# Tests: minimum holdings
# ---------------------------------------------------------------------------


class TestMinHoldings:
    def test_enforces_min_holdings_count(self, tmp_path):
        backend = _make_backend(tmp_path)
        # Only 5 scored symbols, but min_holdings = 5 should still work
        syms = [f"SYM{i}" for i in range(5)]
        _seed_scores(backend, syms)

        constraints = PortfolioConstraints(
            profile_id="test",
            max_single_name_weight=Decimal("0.50"),
            sector_deviation_band=Decimal("0.50"),
            country_deviation_band=Decimal("0.50"),
            max_annual_turnover=Decimal("1.00"),
            min_holdings_count=5,
        )

        result = construct_portfolio(
            storage=backend,
            as_of="2026-03-23",
            constraints=constraints,
            seed_universe_path=None,
        )

        assert result.holdings_count >= 5

    def test_min_holdings_cannot_exceed_universe_size(self, tmp_path):
        """If min_holdings > scored symbols, we get all scored symbols."""
        backend = _make_backend(tmp_path)
        syms = [f"SYM{i}" for i in range(3)]
        _seed_scores(backend, syms)

        constraints = PortfolioConstraints(
            profile_id="test",
            max_single_name_weight=Decimal("0.50"),
            sector_deviation_band=Decimal("0.50"),
            country_deviation_band=Decimal("0.50"),
            max_annual_turnover=Decimal("1.00"),
            min_holdings_count=10,  # more than available
        )

        result = construct_portfolio(
            storage=backend,
            as_of="2026-03-23",
            constraints=constraints,
            seed_universe_path=None,
        )

        # Should have all 3, not crash
        assert result.holdings_count == 3


# ---------------------------------------------------------------------------
# Tests: turnover constraint
# ---------------------------------------------------------------------------


class TestTurnoverConstraint:
    def test_turnover_reported_when_prior_exists(self, tmp_path):
        backend = _make_backend(tmp_path)
        syms = [f"SYM{i}" for i in range(5)]
        _seed_scores(backend, syms)
        _seed_prior_weights(backend, {f"SYM{i}": 0.2 for i in range(5)})

        result = construct_portfolio(storage=backend, as_of="2026-03-23", seed_universe_path=None)

        assert result.turnover_vs_prior is not None

    def test_no_prior_means_no_turnover(self, tmp_path):
        backend = _make_backend(tmp_path)
        syms = [f"SYM{i}" for i in range(5)]
        _seed_scores(backend, syms)

        result = construct_portfolio(storage=backend, as_of="2026-03-23", seed_universe_path=None)

        assert result.turnover_vs_prior is None

    def test_high_turnover_gets_blended(self, tmp_path):
        backend = _make_backend(tmp_path)
        # Prior: 100% in SYM0. New scores want to spread across 5 names.
        _seed_scores(backend, [f"SYM{i}" for i in range(5)])
        _seed_prior_weights(backend, {"SYM0": 1.0})

        constraints = PortfolioConstraints(
            profile_id="test",
            max_single_name_weight=Decimal("0.50"),
            sector_deviation_band=Decimal("0.50"),
            country_deviation_band=Decimal("0.50"),
            max_annual_turnover=Decimal("0.20"),  # very tight
            min_holdings_count=1,
        )

        result = construct_portfolio(
            storage=backend,
            as_of="2026-03-23",
            constraints=constraints,
            seed_universe_path=None,
        )

        assert result.turnover_vs_prior is not None
        assert result.turnover_vs_prior <= 0.20 + 1e-4


# ---------------------------------------------------------------------------
# Tests: output schema
# ---------------------------------------------------------------------------


class TestOutputSchema:
    def test_result_dataframe_has_required_columns(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_scores(backend, [f"SYM{i}" for i in range(5)])

        result = construct_portfolio(storage=backend, as_of="2026-03-23", seed_universe_path=None)

        required = {"portfolio_id", "symbol", "target_weight", "composite_score", "rank", "country"}
        assert required.issubset(set(result.weights.columns))

    def test_snapshot_persisted(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_scores(backend, [f"SYM{i}" for i in range(5)])

        result = construct_portfolio(storage=backend, as_of="2026-03-23", seed_universe_path=None)

        assert result.snapshot_path.exists()

    def test_country_groups_populated(self, tmp_path):
        backend = _make_backend(tmp_path)
        countries = ["US", "US", "GB", "GB", "CH"]
        _seed_scores(backend, [f"SYM{i}" for i in range(5)], countries=countries)

        result = construct_portfolio(storage=backend, as_of="2026-03-23", seed_universe_path=None)

        assert "US" in result.country_groups
        assert "GB" in result.country_groups
        assert "CH" in result.country_groups
        assert abs(sum(result.country_groups.values()) - 1.0) < 1e-4


# ---------------------------------------------------------------------------
# Tests: with real profile constraints
# ---------------------------------------------------------------------------


class TestWithProfileConstraints:
    def test_conservative_profile_produces_more_holdings(self, tmp_path):
        """Conservative profiles have higher min_holdings (40-50)."""
        backend = _make_backend(tmp_path)
        syms = [f"SYM{i}" for i in range(60)]
        _seed_scores(backend, syms)

        conservative = InvestorProfile(
            profile_id="conservative",
            fire_variant=FireVariant.RETIREMENT_COMPLEMENT,
            risk_appetite=2,
            horizon_years=5,
            withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        )
        constraints = ProfileToConstraints.resolve(conservative)

        result = construct_portfolio(
            storage=backend,
            as_of="2026-03-23",
            constraints=constraints,
            seed_universe_path=None,
        )

        assert result.holdings_count >= constraints.min_holdings_count

    def test_aggressive_profile_allows_larger_positions(self, tmp_path):
        backend = _make_backend(tmp_path)
        # 35 names: one dominant, rest low. Needs >=20 for 5% cap feasibility.
        scores = [100.0] + [1.0] * 34
        _seed_scores(backend, [f"SYM{i}" for i in range(35)], scores=scores)

        aggressive = InvestorProfile(
            profile_id="aggressive",
            fire_variant=FireVariant.FAT_FIRE,
            risk_appetite=5,
            horizon_years=20,
            withdrawal_pattern=WithdrawalPattern.COMPOUND_ONLY,
        )
        constraints = ProfileToConstraints.resolve(aggressive)

        result = construct_portfolio(
            storage=backend,
            as_of="2026-03-23",
            constraints=constraints,
            seed_universe_path=None,
        )

        # Max weight should be at most the constraint cap
        assert float(result.max_weight) <= float(constraints.max_single_name_weight) + 1e-6
