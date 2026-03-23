"""BDD scenarios for portfolio holdings state behavior."""

from datetime import UTC, datetime

import pandas as pd
from pytest_bdd import given, parsers, scenarios, then, when

from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.portfolio.rebalance import rebalance_portfolio
from alpha_holdings.portfolio.state import HoldingRecord, apply_trades

scenarios("features/holdings_state.feature")

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


def _proposals(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["symbol", "side", "shares", "price_estimate"])


def _seed_weights(backend, weights: dict[str, float]):
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
    path = backend.write_normalized_snapshot(dataset="portfolio_weights", as_of=AS_OF_DT, rows=rows)
    backend.register_snapshot(
        dataset="portfolio_weights",
        as_of=AS_OF_DT,
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
# Scenario 1: first buy sets book cost
# ---------------------------------------------------------------------------


@given("an empty holdings state", target_fixture="holdings")
def given_empty_holdings():
    return {}


@when(
    parsers.parse('I apply a buy of {shares:g} shares of "{symbol}" at {price:g}'),
    target_fixture="holdings",
)
def when_apply_buy(holdings, shares, symbol, price):
    proposals = _proposals(
        [{"symbol": symbol, "side": "buy", "shares": shares, "price_estimate": price}]
    )
    return apply_trades(current=holdings, proposals=proposals)


@then(parsers.parse('"{symbol}" has {shares:g} shares with book cost {book_cost:g} per share'))
def then_has_shares_and_book_cost(holdings, symbol, shares, book_cost):
    assert symbol in holdings
    import pytest

    assert holdings[symbol].shares == pytest.approx(shares, abs=1e-4)
    assert holdings[symbol].book_cost_per_share == pytest.approx(book_cost, abs=1e-4)


# ---------------------------------------------------------------------------
# Scenario 2: weighted-average book cost on incremental buy
# ---------------------------------------------------------------------------


@given(
    parsers.parse(
        'a holdings state with {shares:g} shares of "{symbol}" at book cost {book_cost:g}'
    ),
    target_fixture="holdings",
)
def given_existing_holding(shares, symbol, book_cost):
    return {symbol: HoldingRecord(symbol=symbol, shares=shares, book_cost_per_share=book_cost)}


# Scenario 2 reuses when_apply_buy (same step text)


# ---------------------------------------------------------------------------
# Scenario 3: sell crystallises realized gain
# ---------------------------------------------------------------------------


@when(
    parsers.parse('I apply a sell of {shares:g} shares of "{symbol}" at {price:g}'),
    target_fixture="holdings",
)
def when_apply_sell(holdings, shares, symbol, price):
    proposals = _proposals(
        [{"symbol": symbol, "side": "sell", "shares": shares, "price_estimate": price}]
    )
    return apply_trades(current=holdings, proposals=proposals)


@then(parsers.parse('"{symbol}" has {shares:g} shares remaining'))
def then_shares_remaining(holdings, symbol, shares):
    import pytest

    assert holdings[symbol].shares == pytest.approx(shares, abs=1e-4)


@then(parsers.parse('the realized gain for "{symbol}" is {gain:g}'))
def then_realized_gain(holdings, symbol, gain):
    import pytest

    assert holdings[symbol].realized_gain_total == pytest.approx(gain, abs=0.01)


# ---------------------------------------------------------------------------
# Scenario 5: rebalance persists holdings snapshot
# ---------------------------------------------------------------------------


@given(
    'a portfolio with target weights for "AAPL" 0.50 and "MSFT" 0.50',
    target_fixture="backend",
)
def given_portfolio_aapl_msft(tmp_path):
    backend = _make_backend(tmp_path)
    _seed_weights(backend, {"AAPL": 0.50, "MSFT": 0.50})
    return backend


@given('prices exist for "AAPL" at 200.0 and "MSFT" at 400.0')
def given_prices_aapl_msft_holdings(backend):
    _seed_prices(backend, "AAPL", 200.0)
    _seed_prices(backend, "MSFT", 400.0)


@when(
    parsers.parse("I rebalance the portfolio with value {value:d}"),
    target_fixture="rebalance_result",
)
def when_rebalance_holdings(backend, value):
    return rebalance_portfolio(storage=backend, as_of=AS_OF_STR, portfolio_value=float(value))


@then("a holdings snapshot is persisted for the portfolio")
def then_snapshot_persisted(rebalance_result):
    assert rebalance_result.holdings_snapshot_path is not None
    assert rebalance_result.holdings_snapshot_path.exists()


@then("each position in the snapshot has a non-negative unrealized gain field")
def then_unrealized_gain_non_negative(rebalance_result):
    import pandas as pd

    df = pd.read_parquet(rebalance_result.holdings_snapshot_path)
    assert not df.empty
    # Unrealized gain = market_value - cost_basis_total; at execution price this is 0
    assert (df["unrealized_gain"] >= -1e-4).all()
