"""Tests for the performance analytics / report module.

Validates metric computation: returns, volatility, Sharpe, drawdown,
benchmark-relative metrics, edge cases, and snapshot persistence.
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from alpha_holdings.analytics.performance import (
    PerformanceReport,
    compute_report_from_nav,
    generate_report,
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


def _make_nav_series(
    start_nav=1_000_000.0,
    daily_returns=None,
    n_days=60,
    benchmark_returns=None,
):
    """Build a synthetic NAV DataFrame."""
    dates = pd.bdate_range("2025-01-02", periods=n_days)

    if daily_returns is None:
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, n_days)
        daily_returns[0] = 0.0

    nav = [start_nav]
    for r in daily_returns[1:]:
        nav.append(nav[-1] * (1 + r))

    df = pd.DataFrame(
        {
            "date": dates[:n_days],
            "nav": nav[:n_days],
            "daily_return": daily_returns[:n_days],
        }
    )

    if benchmark_returns is not None:
        df["benchmark_return"] = benchmark_returns[:n_days]

    return df


# ---------------------------------------------------------------------------
# Tests: Core metrics
# ---------------------------------------------------------------------------


class TestCoreMetrics:
    def test_uptrend_positive_return(self, tmp_path):
        backend = _make_backend(tmp_path)
        # Steady 0.1% daily return
        returns = np.array([0.0] + [0.001] * 59)
        nav_df = _make_nav_series(daily_returns=returns, n_days=60)

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
            portfolio_id="test",
        )

        assert report.total_return > 0
        assert report.annualized_return > 0

    def test_downtrend_negative_return(self, tmp_path):
        backend = _make_backend(tmp_path)
        returns = np.array([0.0] + [-0.001] * 59)
        nav_df = _make_nav_series(daily_returns=returns, n_days=60)

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert report.total_return < 0
        assert report.max_drawdown > 0

    def test_volatility_positive(self, tmp_path):
        backend = _make_backend(tmp_path)
        nav_df = _make_nav_series(n_days=60)

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert report.volatility > 0

    def test_flat_market_zero_volatility(self, tmp_path):
        backend = _make_backend(tmp_path)
        returns = np.array([0.0] * 60)
        nav_df = _make_nav_series(daily_returns=returns, n_days=60)

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert report.volatility == 0.0
        assert report.total_return == pytest.approx(0.0, abs=1e-6)

    def test_max_drawdown_correct(self, tmp_path):
        backend = _make_backend(tmp_path)
        # NAV: 100, 110, 90, 95  →  peak 110, trough 90 → dd = 18.18%
        nav_df = pd.DataFrame(
            {
                "date": pd.bdate_range("2025-01-02", periods=4),
                "nav": [100.0, 110.0, 90.0, 95.0],
            }
        )

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        expected_dd = (110.0 - 90.0) / 110.0  # ~0.1818
        assert report.max_drawdown == pytest.approx(expected_dd, abs=0.001)


class TestSharpeRatio:
    def test_positive_sharpe_uptrend(self, tmp_path):
        backend = _make_backend(tmp_path)
        returns = np.array([0.0] + [0.002] * 59)  # consistent positive
        nav_df = _make_nav_series(daily_returns=returns, n_days=60)

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
            risk_free_rate=0.02,
        )

        assert report.sharpe_ratio > 0

    def test_calmar_ratio(self, tmp_path):
        backend = _make_backend(tmp_path)
        nav_df = pd.DataFrame(
            {
                "date": pd.bdate_range("2025-01-02", periods=5),
                "nav": [100.0, 105.0, 95.0, 100.0, 110.0],
            }
        )

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert report.calmar_ratio is not None
        assert report.calmar_ratio > 0  # positive return / positive drawdown


# ---------------------------------------------------------------------------
# Tests: Benchmark-relative metrics
# ---------------------------------------------------------------------------


class TestBenchmarkMetrics:
    def test_excess_return_computed(self, tmp_path):
        backend = _make_backend(tmp_path)
        port_returns = np.array([0.0] + [0.002] * 59)
        bm_returns = np.array([0.0] + [0.001] * 59)
        nav_df = _make_nav_series(
            daily_returns=port_returns,
            benchmark_returns=bm_returns,
            n_days=60,
        )

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert report.excess_return is not None
        assert report.excess_return > 0  # portfolio beats benchmark

    def test_tracking_error_computed(self, tmp_path):
        backend = _make_backend(tmp_path)
        np.random.seed(42)
        port_returns = np.array([0.0, *list(np.random.normal(0.001, 0.015, 59))])
        bm_returns = np.array([0.0, *list(np.random.normal(0.001, 0.01, 59))])
        nav_df = _make_nav_series(
            daily_returns=port_returns,
            benchmark_returns=bm_returns,
            n_days=60,
        )

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert report.tracking_error is not None
        assert report.tracking_error > 0

    def test_information_ratio_computed(self, tmp_path):
        backend = _make_backend(tmp_path)
        port_returns = np.array([0.0] + [0.003] * 59)
        bm_returns = np.array([0.0] + [0.001] * 59)
        nav_df = _make_nav_series(
            daily_returns=port_returns,
            benchmark_returns=bm_returns,
            n_days=60,
        )

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert report.information_ratio is not None

    def test_no_benchmark_returns(self, tmp_path):
        backend = _make_backend(tmp_path)
        nav_df = _make_nav_series(n_days=60)
        # No benchmark_return column

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert report.benchmark_return is None
        assert report.tracking_error is None
        assert report.information_ratio is None


# ---------------------------------------------------------------------------
# Tests: Output and persistence
# ---------------------------------------------------------------------------


class TestReportOutput:
    def test_summary_dataframe_shape(self, tmp_path):
        backend = _make_backend(tmp_path)
        nav_df = _make_nav_series(n_days=60)

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert isinstance(report.summary, pd.DataFrame)
        assert "metric" in report.summary.columns
        assert "value" in report.summary.columns
        assert len(report.summary) >= 10  # at least 10 metrics

    def test_snapshot_persisted(self, tmp_path):
        backend = _make_backend(tmp_path)
        nav_df = _make_nav_series(n_days=60)

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert report.snapshot_path.exists()
        snaps = backend.list_snapshots(dataset_filter="performance_report")
        assert len(snaps) >= 1

    def test_best_worst_days(self, tmp_path):
        backend = _make_backend(tmp_path)
        returns = np.array([0.0, 0.05, -0.03, 0.01, -0.01])
        nav_df = _make_nav_series(daily_returns=returns, n_days=5)

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        assert report.best_day == pytest.approx(0.05, abs=0.001)
        assert report.worst_day == pytest.approx(-0.03, abs=0.001)

    def test_positive_days_percentage(self, tmp_path):
        backend = _make_backend(tmp_path)
        # 3 positive, 1 negative out of 4 trading returns
        returns = np.array([0.0, 0.01, 0.02, -0.01, 0.01])
        nav_df = _make_nav_series(daily_returns=returns, n_days=5)

        report = compute_report_from_nav(
            nav_series=nav_df,
            storage=backend,
        )

        # 3/4 = 75% positive days (skip first 0 day)
        assert report.positive_days_pct == pytest.approx(75.0, abs=1.0)


# ---------------------------------------------------------------------------
# Tests: generate_report from stored backtest
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def test_raises_without_backtest_data(self, tmp_path):
        backend = _make_backend(tmp_path)
        with pytest.raises(ValueError, match="No backtest_results"):
            generate_report(storage=backend)

    def test_reads_stored_backtest(self, tmp_path):
        backend = _make_backend(tmp_path)
        # Seed a backtest_results snapshot
        nav_df = _make_nav_series(n_days=30)
        as_of = datetime.now(tz=UTC)
        rows = nav_df.to_dict(orient="records")
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

        report = generate_report(storage=backend)

        assert isinstance(report, PerformanceReport)
        assert report.total_return != 0.0 or report.volatility >= 0.0


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_minimum_two_points(self, tmp_path):
        backend = _make_backend(tmp_path)
        nav_df = pd.DataFrame({"date": [pd.Timestamp("2025-01-02")], "nav": [100.0]})

        with pytest.raises(ValueError, match="at least 2"):
            compute_report_from_nav(nav_series=nav_df, storage=backend)

    def test_two_point_series(self, tmp_path):
        backend = _make_backend(tmp_path)
        nav_df = pd.DataFrame(
            {
                "date": pd.bdate_range("2025-01-02", periods=2),
                "nav": [100.0, 105.0],
            }
        )

        report = compute_report_from_nav(nav_series=nav_df, storage=backend)

        assert report.total_return == pytest.approx(0.05, abs=0.001)
