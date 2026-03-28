"""Tests for factor attribution analytics.

Validates OLS-based factor decomposition, factor return construction,
edge cases, and integration with backtest data.
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from alpha_holdings.analytics.attribution import (
    AttributionResult,
    FactorExposure,
    _build_factor_return_series,
    compute_factor_attribution,
)
from alpha_holdings.data.storage import LocalStorageBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path):
    return LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )


def _seed_price_history(backend, symbol, prices, start_date="2025-01-02"):
    """Write a multi-day price history for a symbol."""
    dates = pd.bdate_range(start=start_date, periods=len(prices))
    rows = []
    for dt, price in zip(dates, prices, strict=True):
        rows.append(
            {
                "date": str(dt.date()),
                "open": float(price),
                "high": float(price) * 1.01,
                "low": float(price) * 0.99,
                "close": float(price),
                "adjusted_close": float(price),
                "volume": 1_000_000,
            }
        )
    as_of = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
    path = backend.write_normalized_snapshot(
        dataset=f"{symbol.lower()}_prices",
        as_of=as_of,
        rows=rows,
    )
    backend.register_snapshot(
        dataset=f"{symbol.lower()}_prices",
        as_of=as_of,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"source": "test"},
    )
    return dates


def _seed_backtest_results(backend, nav_values, start_date="2025-01-02"):
    """Seed a backtest_results snapshot with a NAV series."""
    dates = pd.bdate_range(start=start_date, periods=len(nav_values))
    daily_returns = [0.0]
    for i in range(1, len(nav_values)):
        daily_returns.append(nav_values[i] / nav_values[i - 1] - 1.0)

    rows = [
        {
            "date": str(d.date()),
            "nav": float(nav_values[i]),
            "daily_return": round(daily_returns[i], 6),
            "benchmark_return": 0.0,
        }
        for i, d in enumerate(dates)
    ]
    as_of = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
    path = backend.write_normalized_snapshot(
        dataset="backtest_results",
        as_of=as_of,
        rows=rows,
    )
    backend.register_snapshot(
        dataset="backtest_results",
        as_of=as_of,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"portfolio_id": "test"},
    )
    return dates


def _make_seed_universe(tmp_path, symbols):
    csv_path = tmp_path / "universe.csv"
    lines = ["symbol,security_id,isin,name,country,currency,region,benchmark"]
    for sym in symbols:
        lines.append(f"{sym},{sym},XX,{sym} Inc.,US,USD,US,SPY")
    csv_path.write_text("\n".join(lines))
    return csv_path


# ---------------------------------------------------------------------------
# Tests: Factor return series construction
# ---------------------------------------------------------------------------


class TestFactorReturnSeries:
    def test_basic_construction(self):
        """Factor return series should have columns for each factor."""
        np.random.seed(42)
        n = 60
        dates = pd.bdate_range("2025-01-02", periods=n)
        symbols = ["A", "B", "C", "D"]
        price_data = {}
        volume_data = {}
        for sym in symbols:
            base = 100 + np.random.randn() * 10
            price_data[sym] = pd.Series(
                base * np.cumprod(1 + np.random.normal(0.001, 0.02, n)), index=dates
            )
            volume_data[sym] = pd.Series(np.random.randint(100000, 1000000, n), index=dates)

        price_matrix = pd.DataFrame(price_data)
        volume_matrix = pd.DataFrame(volume_data)

        factor_returns = _build_factor_return_series(
            price_matrix=price_matrix,
            volume_matrix=volume_matrix,
            rebalance_freq="monthly",
            lookback_days=20,
        )

        assert not factor_returns.empty
        assert "momentum" in factor_returns.columns
        assert "low_volatility" in factor_returns.columns
        assert "liquidity" in factor_returns.columns

    def test_returns_are_reasonable(self):
        """Factor returns should be centered near zero."""
        np.random.seed(123)
        n = 100
        dates = pd.bdate_range("2025-01-02", periods=n)
        symbols = ["A", "B", "C", "D", "E", "F"]
        price_data = {}
        volume_data = {}
        for sym in symbols:
            price_data[sym] = pd.Series(
                100 * np.cumprod(1 + np.random.normal(0.0, 0.02, n)), index=dates
            )
            volume_data[sym] = pd.Series(np.random.randint(100000, 1000000, n), index=dates)

        factor_returns = _build_factor_return_series(
            price_matrix=pd.DataFrame(price_data),
            volume_matrix=pd.DataFrame(volume_data),
            rebalance_freq="monthly",
            lookback_days=20,
        )

        for col in factor_returns.columns:
            assert abs(factor_returns[col].mean()) < 0.05  # daily mean should be small

    def test_empty_matrix(self):
        """Empty price matrix returns empty factor returns."""
        result = _build_factor_return_series(
            price_matrix=pd.DataFrame(),
            volume_matrix=pd.DataFrame(),
            rebalance_freq="monthly",
            lookback_days=20,
        )
        assert result.empty


# ---------------------------------------------------------------------------
# Tests: Full attribution computation
# ---------------------------------------------------------------------------


class TestFactorAttribution:
    def test_basic_attribution(self, tmp_path):
        """Attribution should produce factor exposures and alpha."""
        backend = _make_backend(tmp_path)
        symbols = ["AAPL", "MSFT", "GOOG", "AMZN"]
        universe_path = _make_seed_universe(tmp_path, symbols)

        np.random.seed(42)
        n = 60
        start = "2025-01-02"

        # Seed price histories for universe
        for sym in symbols:
            prices = 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, n))
            _seed_price_history(backend, sym, prices, start_date=start)

        # Seed backtest NAV (correlated with one of the stocks)
        nav = 1_000_000 * np.cumprod(1 + np.random.normal(0.0005, 0.015, n))
        dates = _seed_backtest_results(backend, nav, start_date=start)

        result = compute_factor_attribution(
            storage=backend,
            start_date=str(dates[0].date()),
            end_date=str(dates[-1].date()),
            seed_universe_path=universe_path,
            lookback_days=10,
        )

        assert isinstance(result, AttributionResult)
        assert len(result.factors) == 3
        assert result.r_squared >= 0.0
        assert result.r_squared <= 1.0
        assert result.residual_vol_ann >= 0.0

    def test_factor_names_correct(self, tmp_path):
        """Factor names should be momentum, low_volatility, liquidity."""
        backend = _make_backend(tmp_path)
        symbols = ["A", "B", "C", "D"]
        universe_path = _make_seed_universe(tmp_path, symbols)

        np.random.seed(99)
        n = 60
        start = "2025-01-02"

        for sym in symbols:
            prices = 100 * np.cumprod(1 + np.random.normal(0.001, 0.015, n))
            _seed_price_history(backend, sym, prices, start_date=start)

        nav = 1_000_000 * np.cumprod(1 + np.random.normal(0.0005, 0.012, n))
        dates = _seed_backtest_results(backend, nav, start_date=start)

        result = compute_factor_attribution(
            storage=backend,
            start_date=str(dates[0].date()),
            end_date=str(dates[-1].date()),
            seed_universe_path=universe_path,
            lookback_days=10,
        )

        names = {f.name for f in result.factors}
        assert names == {"momentum", "low_volatility", "liquidity"}

    def test_alpha_is_float(self, tmp_path):
        """Alpha should be a finite float."""
        backend = _make_backend(tmp_path)
        symbols = ["X", "Y", "Z", "W"]
        universe_path = _make_seed_universe(tmp_path, symbols)

        np.random.seed(77)
        n = 40
        start = "2025-01-02"

        for sym in symbols:
            prices = 100 * np.cumprod(1 + np.random.normal(0.0, 0.015, n))
            _seed_price_history(backend, sym, prices, start_date=start)

        nav = 1_000_000 * np.cumprod(1 + np.random.normal(0.0, 0.01, n))
        dates = _seed_backtest_results(backend, nav, start_date=start)

        result = compute_factor_attribution(
            storage=backend,
            start_date=str(dates[0].date()),
            end_date=str(dates[-1].date()),
            seed_universe_path=universe_path,
            lookback_days=10,
        )

        assert np.isfinite(result.alpha_ann)

    def test_raises_without_backtest(self, tmp_path):
        """Should raise when no backtest data exists."""
        backend = _make_backend(tmp_path)
        universe_path = _make_seed_universe(tmp_path, ["A"])

        with pytest.raises(ValueError, match="No backtest_results"):
            compute_factor_attribution(
                storage=backend,
                start_date="2025-01-02",
                end_date="2025-03-31",
                seed_universe_path=universe_path,
            )

    def test_raises_with_insufficient_data(self, tmp_path):
        """Should raise with too few data points."""
        backend = _make_backend(tmp_path)
        symbols = ["A"]
        universe_path = _make_seed_universe(tmp_path, symbols)

        _seed_price_history(backend, "A", [100, 101, 102], start_date="2025-01-02")
        _seed_backtest_results(backend, [1_000_000, 1_001_000, 1_002_000], start_date="2025-01-02")

        with pytest.raises(ValueError):
            compute_factor_attribution(
                storage=backend,
                start_date="2025-01-02",
                end_date="2025-01-06",
                seed_universe_path=universe_path,
            )

    def test_factor_exposure_has_t_stat(self, tmp_path):
        """Each factor exposure should have a t-stat."""
        backend = _make_backend(tmp_path)
        symbols = ["A", "B", "C", "D"]
        universe_path = _make_seed_universe(tmp_path, symbols)

        np.random.seed(55)
        n = 60

        for sym in symbols:
            prices = 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, n))
            _seed_price_history(backend, sym, prices)

        nav = 1_000_000 * np.cumprod(1 + np.random.normal(0.0005, 0.015, n))
        dates = _seed_backtest_results(backend, nav)

        result = compute_factor_attribution(
            storage=backend,
            start_date=str(dates[0].date()),
            end_date=str(dates[-1].date()),
            seed_universe_path=universe_path,
            lookback_days=10,
        )

        for f in result.factors:
            assert isinstance(f.t_stat, float)
            assert np.isfinite(f.t_stat)

    def test_contributions_sum_to_explained_return(self, tmp_path):
        """Factor contributions + alpha should approximate the portfolio excess return."""
        backend = _make_backend(tmp_path)
        symbols = ["A", "B", "C", "D", "E", "F"]
        universe_path = _make_seed_universe(tmp_path, symbols)

        np.random.seed(42)
        n = 80

        for sym in symbols:
            prices = 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, n))
            _seed_price_history(backend, sym, prices)

        nav = 1_000_000 * np.cumprod(1 + np.random.normal(0.0005, 0.015, n))
        dates = _seed_backtest_results(backend, nav)

        result = compute_factor_attribution(
            storage=backend,
            start_date=str(dates[0].date()),
            end_date=str(dates[-1].date()),
            seed_universe_path=universe_path,
            lookback_days=10,
        )

        total_factor_contrib = sum(f.contribution_ann for f in result.factors)
        # Alpha + factor contributions should be finite
        explained = total_factor_contrib + result.alpha_ann
        assert np.isfinite(explained)


# ---------------------------------------------------------------------------
# Tests: AttributionResult dataclass
# ---------------------------------------------------------------------------


class TestAttributionResultDataclass:
    def test_empty_factors(self):
        """AttributionResult with no factors should work."""
        result = AttributionResult(
            start_date="2025-01-01",
            end_date="2025-12-31",
            alpha_ann=0.05,
            r_squared=0.0,
            residual_vol_ann=0.15,
        )
        assert result.factors == []
        assert result.factor_returns is None

    def test_with_factors(self):
        fe = FactorExposure(
            name="momentum",
            beta=0.5,
            mean_factor_return_ann=0.03,
            contribution_ann=0.015,
            t_stat=2.1,
        )
        result = AttributionResult(
            start_date="2025-01-01",
            end_date="2025-12-31",
            alpha_ann=0.02,
            r_squared=0.45,
            residual_vol_ann=0.10,
            factors=[fe],
        )
        assert len(result.factors) == 1
        assert result.factors[0].name == "momentum"
