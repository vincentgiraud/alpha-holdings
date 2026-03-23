"""BDD scenarios for equity scoring behavior."""

from datetime import UTC, datetime, timedelta

from pytest_bdd import given, parsers, scenarios, then, when

from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.scoring import score_equities_from_snapshots

scenarios("features/scoring.feature")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AS_OF_DT = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
AS_OF_STR = "2026-03-23"


def _write_prices(
    backend: LocalStorageBackend, ticker: str, *, base_close: float, base_volume: int
) -> None:
    rows = []
    for idx in range(10):
        day = AS_OF_DT - timedelta(days=10 - idx)
        rows.append(
            {
                "security_id": ticker,
                "date": day,
                "close": base_close + idx,
                "adjusted_close": base_close + idx,
                "volume": base_volume + (idx * 1000),
            }
        )
    path = backend.write_normalized_snapshot(
        dataset=f"{ticker.lower()}_prices", as_of=AS_OF_DT, rows=rows
    )
    backend.register_snapshot(
        dataset=f"{ticker.lower()}_prices",
        as_of=AS_OF_DT,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"ticker": ticker, "source": "test"},
    )


def _write_fundamentals(
    backend: LocalStorageBackend,
    ticker: str,
    *,
    revenue: float | None = None,
    net_income: float | None = None,
    debt_to_equity: float | None = None,
    current_ratio: float | None = None,
    free_cash_flow: float | None = None,
) -> None:
    row: dict[str, object] = {
        "security_id": ticker,
        "period_end_date": AS_OF_DT,
        "period_type": "FY",
        "source": "test",
    }
    if revenue is not None:
        row["revenue"] = revenue
    if net_income is not None:
        row["net_income"] = net_income
    if debt_to_equity is not None:
        row["debt_to_equity"] = debt_to_equity
    if current_ratio is not None:
        row["current_ratio"] = current_ratio
    if free_cash_flow is not None:
        row["free_cash_flow"] = free_cash_flow

    ds = f"{ticker.lower()}_fundamentals"
    path = backend.write_normalized_snapshot(dataset=ds, as_of=AS_OF_DT, rows=[row])
    backend.register_snapshot(
        dataset=ds,
        as_of=AS_OF_DT,
        snapshot_path=path,
        row_count=1,
        metadata={"ticker": ticker, "source": "test"},
    )


def _make_backend(tmp_path):
    return LocalStorageBackend(root_path=tmp_path / "data", database_path=tmp_path / "alpha.duckdb")


# ---------------------------------------------------------------------------
# Scenario 1 - degraded scoring
# ---------------------------------------------------------------------------


@given(
    'a storage backend with price snapshots for "AAPL" and "NOVN"',
    target_fixture="backend",
)
def given_prices_aapl_novn(tmp_path):
    backend = _make_backend(tmp_path)
    _write_prices(backend, "AAPL", base_close=100.0, base_volume=1_000_000)
    _write_prices(backend, "NOVN", base_close=90.0, base_volume=950_000)
    return backend


@given('a fundamentals snapshot exists only for "AAPL"')
def given_fundamentals_aapl_only(backend):
    _write_fundamentals(
        backend,
        "AAPL",
        revenue=1000.0,
        net_income=150.0,
        debt_to_equity=0.5,
        current_ratio=1.5,
        free_cash_flow=180.0,
    )


@when(
    parsers.parse('I score equities for as-of date "{as_of}"'),
    target_fixture="score_summary",
)
def when_score_equities(backend, as_of):
    return score_equities_from_snapshots(
        storage=backend,
        as_of=as_of,
        lookback_days=5,
        min_avg_dollar_volume=100_000,
    )


@then("both symbols appear in the scored output")
def then_both_symbols_scored(score_summary):
    symbols = set(score_summary.scores["symbol"])
    assert symbols == {"AAPL", "NOVN"}


@then(parsers.parse('"{symbol}" has fundamentals flag true'))
def then_has_fundamentals_true(score_summary, symbol):
    row = score_summary.scores.set_index("symbol").loc[symbol]
    assert bool(row["has_fundamentals"]) is True


@then(parsers.parse('"{symbol}" has fundamentals flag false'))
def then_has_fundamentals_false(score_summary, symbol):
    row = score_summary.scores.set_index("symbol").loc[symbol]
    assert bool(row["has_fundamentals"]) is False


@then(parsers.parse('"{symbol}" fundamentals factor contributions are all zero'))
def then_fundamentals_factors_zero(score_summary, symbol):
    row = score_summary.scores.set_index("symbol").loc[symbol]
    assert float(row["factor_profitability"]) == 0.0
    assert float(row["factor_balance_sheet_quality"]) == 0.0
    assert float(row["factor_cash_flow_quality"]) == 0.0


# ---------------------------------------------------------------------------
# Scenario 2 - fundamentals drive rank
# ---------------------------------------------------------------------------


@given(
    'a storage backend with price snapshots for "HIGH" and "LOW"',
    target_fixture="backend",
)
def given_prices_high_low(tmp_path):
    backend = _make_backend(tmp_path)
    # Identical price histories so only fundamentals differentiate
    _write_prices(backend, "HIGH", base_close=100.0, base_volume=1_000_000)
    _write_prices(backend, "LOW", base_close=100.0, base_volume=1_000_000)
    return backend


@given('"HIGH" has strong fundamentals and "LOW" has weak fundamentals')
def given_divergent_fundamentals(backend):
    _write_fundamentals(
        backend,
        "HIGH",
        revenue=1000.0,
        net_income=300.0,
        debt_to_equity=0.2,
        current_ratio=2.5,
        free_cash_flow=350.0,
    )
    _write_fundamentals(
        backend,
        "LOW",
        revenue=1000.0,
        net_income=10.0,
        debt_to_equity=2.0,
        current_ratio=0.5,
        free_cash_flow=5.0,
    )


@then(parsers.parse('"{better}" ranks above "{worse}" in composite score'))
def then_better_ranks_higher(score_summary, better, worse):
    rows = score_summary.scores.set_index("symbol")
    assert float(rows.loc[better, "composite_score"]) > float(rows.loc[worse, "composite_score"])


# ---------------------------------------------------------------------------
# Scenario 3 - partial fundamentals
# ---------------------------------------------------------------------------


@given(
    'a storage backend with a price snapshot for "PARTIAL"',
    target_fixture="backend",
)
def given_prices_partial(tmp_path):
    backend = _make_backend(tmp_path)
    _write_prices(backend, "PARTIAL", base_close=50.0, base_volume=800_000)
    return backend


@given('"PARTIAL" has a fundamentals snapshot with some fields missing')
def given_partial_fundamentals(backend):
    # Only revenue and net_income — debt_to_equity, current_ratio, free_cash_flow missing
    _write_fundamentals(backend, "PARTIAL", revenue=500.0, net_income=80.0)


@then(parsers.parse('"{symbol}" is scored without error'))
def then_scored_without_error(score_summary, symbol):
    assert symbol in score_summary.scores["symbol"].values
    assert score_summary.securities_scored >= 1
