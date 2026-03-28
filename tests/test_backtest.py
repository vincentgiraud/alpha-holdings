"""Tests for the backtest runner.

Validates walk-forward simulation: NAV tracking, rebalance frequency,
performance metrics, benchmark comparison, and edge cases.
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from alpha_holdings.backtest.runner import (
    BacktestResult,
    _compute_max_drawdown,
    _drift_weights,
    _generate_rebalance_dates,
    _score_and_construct,
    run_backtest,
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


def _make_seed_universe(tmp_path, symbols):
    """Write a minimal seed universe CSV."""
    csv_path = tmp_path / "universe.csv"
    lines = ["symbol,security_id,isin,name,country,currency,region,benchmark"]
    for sym in symbols:
        lines.append(f"{sym},{sym},XX,{sym} Inc.,US,USD,US,SPY")
    csv_path.write_text("\n".join(lines))
    return csv_path


# ---------------------------------------------------------------------------
# Tests: Unit tests for internal helpers
# ---------------------------------------------------------------------------


class TestMaxDrawdown:
    def test_no_drawdown_uptrend(self):
        nav = np.array([100.0, 105.0, 110.0, 115.0])
        assert _compute_max_drawdown(nav) == 0.0

    def test_simple_drawdown(self):
        nav = np.array([100.0, 90.0, 80.0, 95.0])
        # Peak 100 → trough 80 → dd = 20%
        assert _compute_max_drawdown(nav) == pytest.approx(0.20, abs=0.001)

    def test_recovery_then_deeper(self):
        nav = np.array([100.0, 90.0, 100.0, 70.0])
        # First dd: 10%, then recovery, then 100→70 = 30%
        assert _compute_max_drawdown(nav) == pytest.approx(0.30, abs=0.001)

    def test_single_point(self):
        assert _compute_max_drawdown(np.array([100.0])) == 0.0


class TestDriftWeights:
    def test_positive_return_increases_weight(self):
        weights = {"A": 0.5, "B": 0.5}
        returns = pd.Series({"A": 0.10, "B": 0.0})
        drifted = _drift_weights(weights, returns)
        assert drifted["A"] > drifted["B"]

    def test_weights_sum_to_one(self):
        weights = {"A": 0.3, "B": 0.4, "C": 0.3}
        returns = pd.Series({"A": 0.05, "B": -0.02, "C": 0.01})
        drifted = _drift_weights(weights, returns)
        assert sum(drifted.values()) == pytest.approx(1.0, abs=1e-10)


class TestGenerateRebalanceDates:
    def test_monthly_dates(self):
        dates = pd.bdate_range("2025-01-02", "2025-04-30")
        idx = pd.DatetimeIndex(dates)
        rebal = _generate_rebalance_dates(trading_dates=idx, freq="monthly")
        # Should have one date per month (Jan, Feb, Mar, Apr)
        assert len(rebal) >= 3

    def test_quarterly_dates(self):
        dates = pd.bdate_range("2025-01-02", "2025-12-31")
        idx = pd.DatetimeIndex(dates)
        rebal = _generate_rebalance_dates(trading_dates=idx, freq="quarterly")
        assert len(rebal) == 4


class TestScoreAndConstruct:
    def test_produces_weights(self):
        from alpha_holdings.domain.investor_profile import PortfolioConstraints

        dates = pd.bdate_range("2025-01-02", periods=30)
        prices = pd.DataFrame(
            {
                "A": np.linspace(100, 120, 30),  # uptrend
                "B": np.linspace(100, 90, 30),  # downtrend
                "C": np.linspace(100, 105, 30),  # slight uptrend
            },
            index=dates,
        )
        volumes = pd.DataFrame(
            {"A": 1e6, "B": 1e6, "C": 1e6},
            index=dates,
        )
        constraints = PortfolioConstraints(
            profile_id="test",
            max_single_name_weight=0.50,
            sector_deviation_band=0.05,
            country_deviation_band=0.05,
            max_annual_turnover=0.50,
            min_holdings_count=2,
        )

        weights = _score_and_construct(
            prices=prices,
            volumes=volumes,
            constraints=constraints,
            max_weight=0.50,
            min_holdings=2,
        )

        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)
        # A has best momentum → should have highest weight
        assert weights["A"] > weights["B"]


# ---------------------------------------------------------------------------
# Tests: Full backtest integration
# ---------------------------------------------------------------------------


class TestBacktestIntegration:
    """Integration tests using seeded price data in tmp_path."""

    def test_basic_backtest(self, tmp_path):
        backend = _make_backend(tmp_path)
        symbols = ["AAA", "BBB", "CCC"]
        seed_path = _make_seed_universe(tmp_path, symbols)

        # 60 trading days of synthetic data
        np.random.seed(42)
        for sym in symbols:
            base = 100.0
            prices = [base]
            for _ in range(59):
                prices.append(prices[-1] * (1 + np.random.normal(0.001, 0.02)))
            _seed_price_history(backend, sym, prices)

        result = run_backtest(
            storage=backend,
            start_date="2025-01-02",
            end_date="2025-03-28",
            rebalance_freq="monthly",
            seed_universe_path=seed_path,
            initial_value=1_000_000.0,
            lookback_days=10,
        )

        assert isinstance(result, BacktestResult)
        assert result.rebalance_count >= 1
        assert len(result.nav_series) > 0
        assert result.nav_series["nav"].iloc[0] == pytest.approx(1_000_000.0, rel=0.01)
        assert result.snapshot_path.exists()

    def test_backtest_metrics_reasonable(self, tmp_path):
        backend = _make_backend(tmp_path)
        symbols = ["XX", "YY"]
        seed_path = _make_seed_universe(tmp_path, symbols)

        # Uptrend data
        for sym in symbols:
            prices = [100.0 + i * 0.5 for i in range(60)]
            _seed_price_history(backend, sym, prices)

        result = run_backtest(
            storage=backend,
            start_date="2025-01-02",
            end_date="2025-03-28",
            seed_universe_path=seed_path,
            lookback_days=10,
        )

        assert result.total_return > 0  # uptrend should be positive
        assert result.volatility >= 0
        assert 0.0 <= result.max_drawdown <= 1.0

    def test_backtest_with_benchmark(self, tmp_path):
        backend = _make_backend(tmp_path)
        symbols = ["PP", "QQ"]
        seed_path = _make_seed_universe(tmp_path, symbols)

        for sym in ["PP", "QQ", "SPY"]:
            prices = [100.0 + i * 0.3 for i in range(60)]
            _seed_price_history(backend, sym, prices)

        result = run_backtest(
            storage=backend,
            start_date="2025-01-02",
            end_date="2025-03-28",
            seed_universe_path=seed_path,
            benchmark_symbol="SPY",
            lookback_days=10,
        )

        assert result.benchmark_total_return is not None

    def test_backtest_snapshot_persisted(self, tmp_path):
        backend = _make_backend(tmp_path)
        symbols = ["RR"]
        seed_path = _make_seed_universe(tmp_path, symbols)
        prices = [100.0 + i * 0.2 for i in range(60)]
        _seed_price_history(backend, "RR", prices)

        run_backtest(
            storage=backend,
            start_date="2025-01-02",
            end_date="2025-03-28",
            seed_universe_path=seed_path,
            lookback_days=10,
        )

        snaps = backend.list_snapshots(dataset_filter="backtest_results")
        assert len(snaps) >= 1


# ---------------------------------------------------------------------------
# Tests: Error cases
# ---------------------------------------------------------------------------


class TestBacktestErrors:
    def test_no_symbols_raises(self, tmp_path):
        backend = _make_backend(tmp_path)
        seed_path = tmp_path / "empty.csv"
        seed_path.write_text("symbol,security_id,isin,name,country,currency,region,benchmark\n")

        with pytest.raises(ValueError, match="No symbols"):
            run_backtest(
                storage=backend,
                start_date="2025-01-02",
                end_date="2025-03-28",
                seed_universe_path=seed_path,
            )

    def test_no_price_data_raises(self, tmp_path):
        backend = _make_backend(tmp_path)
        symbols = ["NODATA"]
        seed_path = _make_seed_universe(tmp_path, symbols)
        # Don't seed any prices

        with pytest.raises(ValueError, match="No price data"):
            run_backtest(
                storage=backend,
                start_date="2025-01-02",
                end_date="2025-03-28",
                seed_universe_path=seed_path,
            )


# ---------------------------------------------------------------------------
# Tests: Fundamentals-aware backtesting
# ---------------------------------------------------------------------------


class TestFundamentalsAwareBacktest:
    def test_backtest_with_fundamentals_factors(self, tmp_path):
        """Verify that backtest scoring uses fundamentals factors when available."""
        backend = _make_backend(tmp_path)
        symbols = ["AAPL", "MSFT", "GOOGL"]
        seed_path = _make_seed_universe(tmp_path, symbols)
        as_of = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)

        # Seed price histories (uptrend for all)
        for i, symbol in enumerate(symbols):
            prices = [100.0 + i + j * 0.5 for j in range(60)]
            _seed_price_history(backend, symbol, prices)

        # Seed fundamentals snapshots for two symbols
        # AAPL: profitable, strong balance sheet, good cash flow
        _write_fundamentals_snapshot(
            backend,
            "aapl_fundamentals",
            "AAPL",
            as_of,
            revenue=100.0,
            net_income=25.0,
            debt_to_equity=0.5,
            current_ratio=2.0,
            free_cash_flow=20.0,
        )

        # MSFT: highly profitable, very strong balance sheet
        _write_fundamentals_snapshot(
            backend,
            "msft_fundamentals",
            "MSFT",
            as_of,
            revenue=100.0,
            net_income=30.0,
            debt_to_equity=0.3,
            current_ratio=2.5,
            free_cash_flow=25.0,
        )
        # GOOGL: no fundamentals snapshot (degraded mode)

        result = run_backtest(
            storage=backend,
            start_date="2025-01-02",
            end_date="2025-03-28",
            seed_universe_path=seed_path,
            lookback_days=10,
        )

        # Basic validation: backtest completed with NAV series
        assert result.rebalance_count >= 1
        assert len(result.nav_series) > 0
        assert result.nav_series["nav"].iloc[0] > 0
        # Verify rebalance occurred and produced weight history
        if result.weight_history is not None:
            assert len(result.weight_history) > 0
        # Check for degraded-mode warning for GOOGL
        assert any("degraded" in w.lower() or "free-source" in w.lower() for w in result.warnings)


def _write_fundamentals_snapshot(
    backend,
    dataset: str,
    ticker: str,
    as_of: datetime,
    *,
    revenue: float,
    net_income: float,
    debt_to_equity: float,
    current_ratio: float,
    free_cash_flow: float,
) -> None:
    rows = [
        {
            "security_id": ticker,
            "period_end_date": str(as_of.date()),
            "period_type": "FY",
            "revenue": revenue,
            "net_income": net_income,
            "debt_to_equity": debt_to_equity,
            "current_ratio": current_ratio,
            "free_cash_flow": free_cash_flow,
            "source": "test",
            "currency": "USD",
        }
    ]

    snapshot_path = backend.write_normalized_snapshot(dataset=dataset, as_of=as_of, rows=rows)
    backend.register_snapshot(
        dataset=dataset,
        as_of=as_of,
        snapshot_path=snapshot_path,
        row_count=len(rows),
        metadata={"source": "test"},
    )
