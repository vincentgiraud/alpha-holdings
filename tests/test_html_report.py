"""Tests for HTML report generation.

Validates HTML output structure, chart rendering, attribution section,
weight history visualization, and file persistence.
"""

import numpy as np
import pandas as pd

from alpha_holdings.analytics.attribution import AttributionResult, FactorExposure
from alpha_holdings.analytics.html_report import render_html_report
from alpha_holdings.analytics.performance import compute_report_from_nav
from alpha_holdings.data.storage import LocalStorageBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path):
    return LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )


def _make_nav_series(n_days=60, with_benchmark=False):
    dates = pd.bdate_range("2025-01-02", periods=n_days)
    np.random.seed(42)
    daily_returns = np.concatenate([[0.0], np.random.normal(0.0005, 0.01, n_days - 1)])
    nav = [1_000_000.0]
    for r in daily_returns[1:]:
        nav.append(nav[-1] * (1 + r))

    df = pd.DataFrame({"date": dates, "nav": nav[:n_days], "daily_return": daily_returns})
    if with_benchmark:
        bm_rets = np.concatenate([[0.0], np.random.normal(0.0003, 0.008, n_days - 1)])
        df["benchmark_return"] = bm_rets
    return df


def _make_report(tmp_path, nav_df=None):
    backend = _make_backend(tmp_path)
    if nav_df is None:
        nav_df = _make_nav_series()
    return compute_report_from_nav(nav_series=nav_df, storage=backend, portfolio_id="test"), nav_df


def _make_attribution():
    return AttributionResult(
        start_date="2025-01-02",
        end_date="2025-03-28",
        alpha_ann=0.025,
        r_squared=0.42,
        residual_vol_ann=0.12,
        factors=[
            FactorExposure(
                name="momentum",
                beta=0.35,
                mean_factor_return_ann=0.05,
                contribution_ann=0.0175,
                t_stat=2.1,
            ),
            FactorExposure(
                name="low_volatility",
                beta=-0.20,
                mean_factor_return_ann=0.03,
                contribution_ann=-0.006,
                t_stat=-1.5,
            ),
            FactorExposure(
                name="liquidity",
                beta=0.15,
                mean_factor_return_ann=0.02,
                contribution_ann=0.003,
                t_stat=0.9,
            ),
        ],
    )


def _make_weight_history():
    dates = pd.bdate_range("2025-01-02", periods=3)
    return pd.DataFrame(
        {
            "date": dates,
            "AAPL": [0.10, 0.12, 0.11],
            "MSFT": [0.08, 0.09, 0.10],
            "GOOG": [0.05, 0.04, 0.06],
            "AMZN": [0.04, 0.05, 0.04],
            "META": [0.03, 0.03, 0.03],
        }
    )


# ---------------------------------------------------------------------------
# Tests: Basic HTML output
# ---------------------------------------------------------------------------


class TestHTMLBasicOutput:
    def test_creates_file(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        out = tmp_path / "report.html"

        result = render_html_report(report=report, nav_series=nav_df, output_path=out)

        assert result.exists()
        assert result == out

    def test_html_structure(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, output_path=out)
        html = out.read_text()

        assert "<!DOCTYPE html>" in html
        assert "<title>" in html
        assert "Performance Report" in html
        assert report.portfolio_id in html

    def test_metrics_table_present(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, output_path=out)
        html = out.read_text()

        assert "Total Return" in html
        assert "Sharpe Ratio" in html
        assert "Max Drawdown" in html

    def test_nav_chart_present(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, output_path=out)
        html = out.read_text()

        assert "<svg" in html
        assert "polyline" in html
        assert "Portfolio NAV" in html

    def test_drawdown_chart_present(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, output_path=out)
        html = out.read_text()

        assert "Drawdown" in html
        assert "polygon" in html

    def test_creates_parent_directory(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        out = tmp_path / "subdir" / "deep" / "report.html"

        render_html_report(report=report, nav_series=nav_df, output_path=out)

        assert out.exists()


# ---------------------------------------------------------------------------
# Tests: Benchmark overlay
# ---------------------------------------------------------------------------


class TestBenchmarkOverlay:
    def test_benchmark_line_when_present(self, tmp_path):
        nav_df = _make_nav_series(with_benchmark=True)
        report, nav_df = _make_report(tmp_path, nav_df=nav_df)
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, output_path=out)
        html = out.read_text()

        assert "Benchmark" in html
        assert "stroke-dasharray" in html

    def test_no_benchmark_line_when_absent(self, tmp_path):
        nav_df = _make_nav_series(with_benchmark=False)
        report, nav_df = _make_report(tmp_path, nav_df=nav_df)
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, output_path=out)
        html = out.read_text()

        assert "stroke-dasharray" not in html


# ---------------------------------------------------------------------------
# Tests: Attribution section
# ---------------------------------------------------------------------------


class TestHTMLAttribution:
    def test_attribution_section_included(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        attr = _make_attribution()
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, attribution=attr, output_path=out)
        html = out.read_text()

        assert "Factor Attribution" in html
        assert "momentum" in html
        assert "Alpha" in html
        assert "R²" in html or "R&sup2;" in html or "R²" in html

    def test_attribution_absent_when_none(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, attribution=None, output_path=out)
        html = out.read_text()

        assert "Factor Attribution" not in html

    def test_attribution_bar_chart(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        attr = _make_attribution()
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, attribution=attr, output_path=out)
        html = out.read_text()

        # Bar chart should have rectangles
        assert "<rect" in html


# ---------------------------------------------------------------------------
# Tests: Weight history
# ---------------------------------------------------------------------------


class TestHTMLWeightHistory:
    def test_weight_section_included(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        wh = _make_weight_history()
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, weight_history=wh, output_path=out)
        html = out.read_text()

        assert "Weight History" in html
        assert "AAPL" in html

    def test_weight_section_absent_when_none(self, tmp_path):
        report, nav_df = _make_report(tmp_path)
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, weight_history=None, output_path=out)
        html = out.read_text()

        assert "Weight History" not in html


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestHTMLEdgeCases:
    def test_minimal_nav_series(self, tmp_path):
        """Two-point NAV should still render."""
        nav_df = pd.DataFrame(
            {
                "date": pd.bdate_range("2025-01-02", periods=2),
                "nav": [1_000_000.0, 1_050_000.0],
            }
        )
        report, _ = _make_report(tmp_path, nav_df=nav_df)
        out = tmp_path / "report.html"

        render_html_report(report=report, nav_series=nav_df, output_path=out)

        assert out.exists()
        html = out.read_text()
        assert "Portfolio NAV" in html

    def test_full_report_with_everything(self, tmp_path):
        """Full report with all sections should render without error."""
        nav_df = _make_nav_series(n_days=60, with_benchmark=True)
        report, _ = _make_report(tmp_path, nav_df=nav_df)
        attr = _make_attribution()
        wh = _make_weight_history()
        out = tmp_path / "report.html"

        render_html_report(
            report=report,
            nav_series=nav_df,
            attribution=attr,
            weight_history=wh,
            output_path=out,
        )

        html = out.read_text()
        assert "Performance Report" in html
        assert "Factor Attribution" in html
        assert "Weight History" in html
        assert "Drawdown" in html
