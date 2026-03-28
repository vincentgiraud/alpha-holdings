"""BDD scenarios for factor attribution behavior."""

import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from pytest_bdd import given, parsers, scenarios, then, when

from alpha_holdings.analytics.attribution import compute_factor_attribution
from alpha_holdings.data.storage import LocalStorageBackend

scenarios("features/attribution.feature")

AS_OF = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
SYMBOLS = ["AA", "BB", "CC", "DD"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path):
    return LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )


def _seed_price_history(backend, symbol, prices, start_date="2025-01-02"):
    dates = pd.bdate_range(start=start_date, periods=len(prices))
    rows = [
        {
            "date": str(dt.date()),
            "open": float(p),
            "high": float(p) * 1.01,
            "low": float(p) * 0.99,
            "close": float(p),
            "adjusted_close": float(p),
            "volume": 1_000_000,
        }
        for dt, p in zip(dates, prices, strict=True)
    ]
    path = backend.write_normalized_snapshot(
        dataset=f"{symbol.lower()}_prices", as_of=AS_OF, rows=rows
    )
    backend.register_snapshot(
        dataset=f"{symbol.lower()}_prices",
        as_of=AS_OF,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"source": "test"},
    )


def _seed_backtest_results(backend, n_days=60, start_date="2025-01-02"):
    """Create a synthetic backtest NAV series."""
    np.random.seed(42)
    dates = pd.bdate_range(start=start_date, periods=n_days)
    nav = 1_000_000.0
    rows = []
    for i, dt in enumerate(dates):
        if i > 0:
            dr = np.random.normal(0.0005, 0.01)
            nav *= 1.0 + dr
        else:
            dr = 0.0
        rows.append(
            {
                "date": str(dt.date()),
                "nav": round(nav, 2),
                "daily_return": round(dr, 6),
                "benchmark_return": 0.0,
            }
        )
    path = backend.write_normalized_snapshot(dataset="backtest_results", as_of=AS_OF, rows=rows)
    backend.register_snapshot(
        dataset="backtest_results",
        as_of=AS_OF,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"portfolio_id": "test"},
    )


def _make_seed_universe(tmp_path, symbols):
    csv_path = tmp_path / "universe.csv"
    lines = ["symbol,security_id,isin,name,country,currency,region,benchmark"]
    for sym in symbols:
        lines.append(f"{sym},{sym},XX,{sym} Inc.,US,USD,US,SPY")
    csv_path.write_text("\n".join(lines))
    return csv_path


# ---------------------------------------------------------------------------
# Shared given/when/then
# ---------------------------------------------------------------------------


@given(
    "a storage backend with a backtest NAV series and price data for 4 symbols over 60 days",
    target_fixture="ctx",
)
def given_backtest_and_prices(tmp_path):
    backend = _make_backend(tmp_path)
    np.random.seed(7)
    for sym in SYMBOLS:
        prices = [100.0]
        for _ in range(59):
            prices.append(prices[-1] * (1 + np.random.normal(0.001, 0.015)))
        _seed_price_history(backend, sym, prices)
    _seed_backtest_results(backend)
    return {"backend": backend, "symbols": SYMBOLS}


@given("a seed universe CSV for those symbols", target_fixture="seed_path")
def given_seed_csv(tmp_path, ctx):
    return _make_seed_universe(tmp_path, ctx["symbols"])


@when(
    parsers.parse('I compute factor attribution from "{start}" to "{end}"'),
    target_fixture="attribution_result",
)
def when_compute_attribution(ctx, seed_path, start, end):
    return compute_factor_attribution(
        storage=ctx["backend"],
        start_date=start,
        end_date=end,
        seed_universe_path=seed_path,
        lookback_days=10,
    )


# ---------------------------------------------------------------------------
# Scenario 1: Three named factors
# ---------------------------------------------------------------------------


@then("the attribution result contains exactly 3 factor exposures")
def then_3_factors(attribution_result):
    assert len(attribution_result.factors) == 3


@then('the factor names are "momentum", "low_volatility", and "liquidity"')
def then_factor_names(attribution_result):
    names = {f.name for f in attribution_result.factors}
    assert names == {"momentum", "low_volatility", "liquidity"}


# ---------------------------------------------------------------------------
# Scenario 2: R-squared bounds
# ---------------------------------------------------------------------------


@then("the R-squared is between 0.0 and 1.0")
def then_r2_bounded(attribution_result):
    assert 0.0 <= attribution_result.r_squared <= 1.0


# ---------------------------------------------------------------------------
# Scenario 3: Contributions + alpha finite
# ---------------------------------------------------------------------------


@then("the sum of factor contributions plus alpha is a finite number")
def then_contributions_finite(attribution_result):
    total = attribution_result.alpha_ann + sum(
        f.contribution_ann for f in attribution_result.factors
    )
    assert math.isfinite(total)
