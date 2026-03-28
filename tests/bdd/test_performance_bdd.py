"""BDD scenarios for performance reporting behavior."""

import pandas as pd
from pytest_bdd import given, scenarios, then, when

from alpha_holdings.analytics.performance import compute_report_from_nav
from alpha_holdings.data.storage import LocalStorageBackend

scenarios("features/performance.feature")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path):
    return LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )


def _build_nav_series(n_days, daily_gain, benchmark_gain=None):
    """Build a NAV DataFrame with optional benchmark returns.

    Adds tiny random noise to daily returns to produce non-zero volatility.
    """
    import random

    random.seed(42)
    dates = pd.bdate_range("2025-01-02", periods=n_days)
    nav = 1_000_000.0
    rows = []
    for i, dt in enumerate(dates):
        bm_ret = 0.0
        if i == 0:
            dr = 0.0
            bm_ret = 0.0
        else:
            dr = daily_gain + random.gauss(0, 0.0001)
            bm_ret = benchmark_gain if benchmark_gain is not None else 0.0
        nav *= 1.0 + dr
        row = {"date": dt, "nav": round(nav, 2), "daily_return": round(dr, 6)}
        if benchmark_gain is not None:
            row["benchmark_return"] = round(bm_ret, 6)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Scenario 1: Positive Sharpe from uptrend
# ---------------------------------------------------------------------------


@given(
    "a NAV series with consistent daily gains of 0.1% over 60 days",
    target_fixture="nav_ctx",
)
def given_uptrend_nav(tmp_path):
    return {
        "nav_series": _build_nav_series(60, daily_gain=0.001),
        "backend": _make_backend(tmp_path),
        "degraded": None,
    }


@when("I compute a performance report", target_fixture="report")
def when_compute_report(nav_ctx):
    return compute_report_from_nav(
        nav_series=nav_ctx["nav_series"],
        storage=nav_ctx["backend"],
        degraded_assumptions=nav_ctx.get("degraded"),
    )


@then("the Sharpe ratio is positive")
def then_sharpe_positive(report):
    assert report.sharpe_ratio > 0


@then("the volatility is positive")
def then_volatility_positive(report):
    assert report.volatility > 0


# ---------------------------------------------------------------------------
# Scenario 2: Benchmark-relative metrics present
# ---------------------------------------------------------------------------


@given(
    "a NAV series with daily gains of 0.2% and benchmark gains of 0.1% over 60 days",
    target_fixture="nav_ctx",
)
def given_nav_with_benchmark(tmp_path):
    return {
        "nav_series": _build_nav_series(60, daily_gain=0.002, benchmark_gain=0.001),
        "backend": _make_backend(tmp_path),
        "degraded": None,
    }


@then("excess return is not None")
def then_excess_not_none(report):
    assert report.excess_return is not None


@then("tracking error is not None")
def then_te_not_none(report):
    assert report.tracking_error is not None


@then("information ratio is not None")
def then_ir_not_none(report):
    assert report.information_ratio is not None


# ---------------------------------------------------------------------------
# Scenario 3: No benchmark
# ---------------------------------------------------------------------------


@given(
    "a NAV series with daily gains of 0.1% over 60 days and no benchmark",
    target_fixture="nav_ctx",
)
def given_nav_no_benchmark(tmp_path):
    return {
        "nav_series": _build_nav_series(60, daily_gain=0.001, benchmark_gain=None),
        "backend": _make_backend(tmp_path),
        "degraded": None,
    }


@then("excess return is None")
def then_excess_none(report):
    assert report.excess_return is None


@then("tracking error is None")
def then_te_none(report):
    assert report.tracking_error is None


@then("information ratio is None")
def then_ir_none(report):
    assert report.information_ratio is None


# ---------------------------------------------------------------------------
# Scenario 4: Degraded assumptions surfaced
# ---------------------------------------------------------------------------


@given('degraded assumptions including "Free-source data warning"')
def given_degraded_assumptions(nav_ctx):
    nav_ctx["degraded"] = ["Free-source data warning"]


@when("I compute a performance report with those assumptions", target_fixture="report")
def when_compute_with_assumptions(nav_ctx):
    return compute_report_from_nav(
        nav_series=nav_ctx["nav_series"],
        storage=nav_ctx["backend"],
        degraded_assumptions=nav_ctx["degraded"],
    )


@then("the report degraded assumptions list is not empty")
def then_degraded_not_empty(report):
    assert len(report.degraded_assumptions) > 0


@then('the report degraded assumptions contain "Free-source data warning"')
def then_degraded_contains(report):
    assert "Free-source data warning" in report.degraded_assumptions
