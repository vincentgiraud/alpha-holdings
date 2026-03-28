"""BDD scenarios for historical backtesting behavior."""

from datetime import UTC, datetime

import pandas as pd
from pytest_bdd import given, parsers, scenarios, then, when

from alpha_holdings.backtest.runner import run_backtest
from alpha_holdings.data.storage import LocalStorageBackend

scenarios("features/backtest.feature")

AS_OF = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)


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


def _make_seed_universe(tmp_path, symbols):
    csv_path = tmp_path / "universe.csv"
    lines = ["symbol,security_id,isin,name,country,currency,region,benchmark"]
    for sym in symbols:
        lines.append(f"{sym},{sym},XX,{sym} Inc.,US,USD,US,SPY")
    csv_path.write_text("\n".join(lines))
    return csv_path


def _uptrend_prices(n, base=100.0, step=0.5):
    return [base + i * step for i in range(n)]


# ---------------------------------------------------------------------------
# Scenario 1: Walk-forward positive return
# ---------------------------------------------------------------------------


@given(
    "a storage backend with uptrending price histories for 3 symbols over 60 days",
    target_fixture="ctx",
)
def given_3_uptrending_symbols(tmp_path):
    backend = _make_backend(tmp_path)
    symbols = ["AAA", "BBB", "CCC"]
    for i, sym in enumerate(symbols):
        _seed_price_history(backend, sym, _uptrend_prices(60, base=100.0 + i * 10))
    return {"backend": backend, "symbols": symbols}


@given("a seed universe CSV for those symbols", target_fixture="seed_path")
def given_seed_csv(tmp_path, ctx):
    return _make_seed_universe(tmp_path, ctx["symbols"])


@when(
    parsers.parse('I run a backtest from "{start}" to "{end}" with monthly rebalancing'),
    target_fixture="backtest_result",
)
def when_run_monthly_backtest(ctx, seed_path, start, end):
    return run_backtest(
        storage=ctx["backend"],
        start_date=start,
        end_date=end,
        rebalance_freq="monthly",
        seed_universe_path=seed_path,
        lookback_days=10,
    )


@then("the backtest total return is positive")
def then_positive_return(backtest_result):
    assert backtest_result.total_return > 0


@then(parsers.parse("the backtest completed at least {n:d} rebalance"))
def then_min_rebalances(backtest_result, n):
    assert backtest_result.rebalance_count >= n


@then("the NAV series contains more than 1 row")
def then_nav_has_rows(backtest_result):
    assert len(backtest_result.nav_series) > 1


# ---------------------------------------------------------------------------
# Scenario 2: Benchmark total return
# ---------------------------------------------------------------------------


@given(
    "a storage backend with uptrending price histories for 2 symbols and a benchmark over 60 days",
    target_fixture="ctx",
)
def given_2_symbols_plus_benchmark(tmp_path):
    backend = _make_backend(tmp_path)
    symbols = ["PP", "QQ"]
    for i, sym in enumerate(symbols):
        _seed_price_history(backend, sym, _uptrend_prices(60, base=100.0 + i * 5))
    _seed_price_history(backend, "SPY", _uptrend_prices(60, base=100.0, step=0.3))
    return {"backend": backend, "symbols": symbols}


@when(
    parsers.parse('I run a backtest from "{start}" to "{end}" with benchmark "{bm}"'),
    target_fixture="backtest_result",
)
def when_run_backtest_with_benchmark(ctx, seed_path, start, end, bm):
    return run_backtest(
        storage=ctx["backend"],
        start_date=start,
        end_date=end,
        benchmark_symbol=bm,
        seed_universe_path=seed_path,
        lookback_days=10,
    )


@then("the backtest benchmark total return is not None")
def then_benchmark_not_none(backtest_result):
    assert backtest_result.benchmark_total_return is not None


# ---------------------------------------------------------------------------
# Scenario 3: Degraded-data warning
# ---------------------------------------------------------------------------


@given(
    "a storage backend with price histories for 2 symbols but no fundamentals",
    target_fixture="ctx",
)
def given_prices_no_fundamentals(tmp_path):
    backend = _make_backend(tmp_path)
    symbols = ["DD", "EE"]
    for sym in symbols:
        _seed_price_history(backend, sym, _uptrend_prices(60))
    return {"backend": backend, "symbols": symbols}


@then("the backtest warnings contain a free-source data notice")
def then_free_source_warning(backtest_result):
    assert any("free-source" in w.lower() for w in backtest_result.warnings)


# ---------------------------------------------------------------------------
# Scenario 4: Monthly vs quarterly rebalance count
# ---------------------------------------------------------------------------


@given(
    "a storage backend with uptrending price histories for 3 symbols over 120 days",
    target_fixture="ctx",
)
def given_3_symbols_120_days(tmp_path):
    backend = _make_backend(tmp_path)
    symbols = ["XX", "YY", "ZZ"]
    for i, sym in enumerate(symbols):
        _seed_price_history(backend, sym, _uptrend_prices(120, base=100.0 + i * 5))
    return {"backend": backend, "symbols": symbols}


@when(
    parsers.parse('I run a monthly backtest from "{start}" to "{end}"'),
    target_fixture="monthly_result",
)
def when_monthly(ctx, seed_path, start, end):
    return run_backtest(
        storage=ctx["backend"],
        start_date=start,
        end_date=end,
        rebalance_freq="monthly",
        seed_universe_path=seed_path,
        lookback_days=10,
    )


@when(
    parsers.parse('I run a quarterly backtest from "{start}" to "{end}"'),
    target_fixture="quarterly_result",
)
def when_quarterly(ctx, seed_path, start, end):
    return run_backtest(
        storage=ctx["backend"],
        start_date=start,
        end_date=end,
        rebalance_freq="quarterly",
        seed_universe_path=seed_path,
        lookback_days=10,
    )


@then("the monthly backtest has more rebalance events than the quarterly backtest")
def then_monthly_more_than_quarterly(monthly_result, quarterly_result):
    assert monthly_result.rebalance_count > quarterly_result.rebalance_count


# ---------------------------------------------------------------------------
# Scenario 5: Fundamentals degraded for missing symbols
# ---------------------------------------------------------------------------


@given(
    'a storage backend with price histories for "AAPL" and "MSFT" over 60 days',
    target_fixture="ctx",
)
def given_aapl_msft_prices(tmp_path):
    backend = _make_backend(tmp_path)
    symbols = ["AAPL", "MSFT"]
    _seed_price_history(backend, "AAPL", _uptrend_prices(60, base=150.0))
    _seed_price_history(backend, "MSFT", _uptrend_prices(60, base=350.0))
    return {"backend": backend, "symbols": symbols}


@given('fundamentals snapshots exist only for "AAPL" dated before the first rebalance')
def given_fundamentals_aapl_only(ctx):
    backend = ctx["backend"]
    as_of = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    rows = [
        {
            "security_id": "AAPL",
            "period_end_date": str(as_of.date()),
            "period_type": "FY",
            "revenue": 100.0,
            "net_income": 25.0,
            "debt_to_equity": 0.5,
            "current_ratio": 2.0,
            "free_cash_flow": 20.0,
            "source": "test",
        }
    ]
    path = backend.write_normalized_snapshot(dataset="aapl_fundamentals", as_of=as_of, rows=rows)
    backend.register_snapshot(
        dataset="aapl_fundamentals",
        as_of=as_of,
        snapshot_path=path,
        row_count=1,
        metadata={"ticker": "AAPL", "source": "test"},
    )


@then("the backtest warnings mention degraded execution for missing fundamentals")
def then_degraded_fundamentals_warning(backtest_result):
    assert any("degraded" in w.lower() for w in backtest_result.warnings)
