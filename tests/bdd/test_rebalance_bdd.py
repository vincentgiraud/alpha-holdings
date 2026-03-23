"""BDD scenarios for portfolio rebalancing behavior."""

from datetime import UTC, datetime

from pytest_bdd import given, parsers, scenarios, then, when

from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.portfolio.rebalance import rebalance_portfolio

scenarios("features/rebalance.feature")

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


def _seed_weights(backend, weights: dict[str, float], as_of=None):
    if as_of is None:
        as_of = AS_OF_DT
    rows = [
        {
            "portfolio_id": "default",
            "symbol": sym,
            "target_weight": w,
            "composite_score": 1.0,
            "rank": i + 1,
            "country": "US",
        }
        for i, (sym, w) in enumerate(weights.items())
    ]
    path = backend.write_normalized_snapshot(dataset="portfolio_weights", as_of=as_of, rows=rows)
    backend.register_snapshot(
        dataset="portfolio_weights",
        as_of=as_of,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"portfolio_id": "default"},
    )


def _seed_prices(backend, symbol: str, price: float, n_days: int = 5):
    rows = [
        {
            "date": f"2026-03-{19 + i:02d}",
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "adjusted_close": price,
            "volume": 1_000_000,
        }
        for i in range(n_days)
    ]
    path = backend.write_normalized_snapshot(
        dataset=f"{symbol.lower()}_prices", as_of=AS_OF_DT, rows=rows
    )
    backend.register_snapshot(
        dataset=f"{symbol.lower()}_prices",
        as_of=AS_OF_DT,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"source": "test"},
    )


# ---------------------------------------------------------------------------
# Scenario 1: first rebalance — all buys
# ---------------------------------------------------------------------------


@given(
    'a storage backend with target weights for "AAPL" 0.60 and "MSFT" 0.40',
    target_fixture="backend",
)
def given_target_weights_aapl_msft(tmp_path):
    backend = _make_backend(tmp_path)
    _seed_weights(backend, {"AAPL": 0.60, "MSFT": 0.40})
    return backend


@given('prices exist for "AAPL" at 175.0 and "MSFT" at 410.0')
def given_prices_aapl_msft(backend):
    _seed_prices(backend, "AAPL", 175.0)
    _seed_prices(backend, "MSFT", 410.0)


@given("no prior portfolio weights exist")
def given_no_prior_weights():
    """No-op: the backend was seeded with only one weights snapshot above."""


@when(
    parsers.parse("I rebalance the portfolio with value {value:d}"),
    target_fixture="rebalance_result",
)
def when_rebalance(backend, value):
    return rebalance_portfolio(storage=backend, as_of=AS_OF_STR, portfolio_value=float(value))


@then(parsers.parse('all trade proposals have side "{side}"'))
def then_all_proposals_side(rebalance_result, side):
    assert not rebalance_result.proposals.empty
    assert (rebalance_result.proposals["side"] == side).all()


@then('the trade proposals cover "AAPL" and "MSFT"')
def then_proposals_cover_aapl_msft(rebalance_result):
    symbols = set(rebalance_result.proposals["symbol"])
    assert {"AAPL", "MSFT"}.issubset(symbols)


# ---------------------------------------------------------------------------
# Scenario 2: rebalance with weight shift — buys and sells
# ---------------------------------------------------------------------------


@given(
    'a storage backend with prior weights for "AAPL" 0.50 and "MSFT" 0.50',
    target_fixture="backend",
)
def given_prior_weights_aapl_msft(tmp_path):
    backend = _make_backend(tmp_path)
    prior_as_of = datetime(2026, 3, 22, 12, 0, 0, tzinfo=UTC)
    _seed_weights(backend, {"AAPL": 0.50, "MSFT": 0.50}, as_of=prior_as_of)
    return backend


@given('new target weights for "AAPL" 0.30 and "MSFT" 0.30 and "GOOGL" 0.40')
def given_new_target_weights_three(backend):
    _seed_weights(backend, {"AAPL": 0.30, "MSFT": 0.30, "GOOGL": 0.40}, as_of=AS_OF_DT)


@given('prices exist for "AAPL" at 175.0 and "MSFT" at 410.0 and "GOOGL" at 165.0')
def given_prices_three(backend):
    _seed_prices(backend, "AAPL", 175.0)
    _seed_prices(backend, "MSFT", 410.0)
    _seed_prices(backend, "GOOGL", 165.0)


@then(parsers.parse('the trade proposals include at least one "{side1}" and one "{side2}"'))
def then_includes_both_sides(rebalance_result, side1, side2):
    sides = set(rebalance_result.proposals["side"])
    assert side1 in sides
    assert side2 in sides


@then(parsers.parse('"{symbol}" has a "{side}" proposal'))
def then_symbol_has_side(rebalance_result, symbol, side):
    rows = rebalance_result.proposals[rebalance_result.proposals["symbol"] == symbol]
    assert not rows.empty
    assert (rows["side"] == side).any()


# ---------------------------------------------------------------------------
# Scenario 3: unchanged weight — no trade
# ---------------------------------------------------------------------------


@given('new target weights for "AAPL" 0.30 and "MSFT" 0.50 and "GOOGL" 0.20')
def given_new_targets_msft_unchanged(backend):
    _seed_weights(backend, {"AAPL": 0.30, "MSFT": 0.50, "GOOGL": 0.20}, as_of=AS_OF_DT)


@then(parsers.parse('"{symbol}" does not appear in the trade proposals'))
def then_symbol_absent(rebalance_result, symbol):
    if rebalance_result.proposals.empty:
        return
    assert symbol not in rebalance_result.proposals["symbol"].values
