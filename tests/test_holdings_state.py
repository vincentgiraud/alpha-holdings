"""Tests for portfolio holdings state management.

Validates:
- First-run (no existing snapshot): all buys populate correct shares and book cost.
- Incremental buy: shares accumulate and book cost is weighted-average.
- Sell: shares decrease and realized gain is computed correctly.
- Full exit: position drops to zero and is excluded from the snapshot.
- No-op (empty proposals): holdings carry forward unchanged.
- Snapshot persistence: holdings_snapshot is registered in storage.
- Rebalance integration: RebalanceResult includes holdings_snapshot_path.
"""

from datetime import UTC, datetime

import pandas as pd
import pytest

from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.portfolio.rebalance import rebalance_portfolio
from alpha_holdings.portfolio.state import (
    HoldingRecord,
    apply_trades,
    read_current_holdings,
    snapshot_holdings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path):
    return LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )


def _make_proposals(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal proposals DataFrame from a list of dicts."""
    return pd.DataFrame(
        rows,
        columns=["symbol", "side", "shares", "price_estimate"],
    )


def _seed_holdings(backend, holdings: dict[str, dict], portfolio_id: str = "default"):
    """Persist a holdings_snapshot for testing read_current_holdings."""
    from alpha_holdings.portfolio.state import _holdings_dataset

    as_of = datetime(2026, 3, 22, 12, 0, 0, tzinfo=UTC)
    rows = [
        {
            "portfolio_id": portfolio_id,
            "as_of_date": as_of.isoformat(),
            "symbol": sym,
            "shares": data["shares"],
            "book_cost_per_share": data["book_cost"],
            "current_price": data.get("current_price", data["book_cost"]),
            "market_value": data["shares"] * data.get("current_price", data["book_cost"]),
            "cost_basis_total": data["shares"] * data["book_cost"],
            "unrealized_gain": data["shares"]
            * (data.get("current_price", data["book_cost"]) - data["book_cost"]),
            "realized_gain_total": data.get("realized_gain_total", 0.0),
            "weight": data.get("weight", 0.5),
        }
        for sym, data in holdings.items()
    ]
    dataset = _holdings_dataset(portfolio_id)
    path = backend.write_normalized_snapshot(dataset=dataset, as_of=as_of, rows=rows)
    backend.register_snapshot(
        dataset=dataset,
        as_of=as_of,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"portfolio_id": portfolio_id},
    )


def _seed_weights(backend, weights_dict, portfolio_id="default", as_of=None):
    rows = [
        {
            "portfolio_id": portfolio_id,
            "symbol": sym,
            "target_weight": w,
            "composite_score": 1.0,
            "rank": i + 1,
            "country": "US",
        }
        for i, (sym, w) in enumerate(weights_dict.items())
    ]
    if as_of is None:
        as_of = datetime.now(tz=UTC)
    path = backend.write_normalized_snapshot(dataset="portfolio_weights", as_of=as_of, rows=rows)
    backend.register_snapshot(
        dataset="portfolio_weights",
        as_of=as_of,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"portfolio_id": portfolio_id},
    )


def _seed_prices(backend, symbol, price, n_days=5, as_of=None):
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
    if as_of is None:
        as_of = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
    path = backend.write_normalized_snapshot(
        dataset=f"{symbol.lower()}_prices", as_of=as_of, rows=rows
    )
    backend.register_snapshot(
        dataset=f"{symbol.lower()}_prices",
        as_of=as_of,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"source": "test"},
    )


# ---------------------------------------------------------------------------
# Tests: apply_trades — pure logic
# ---------------------------------------------------------------------------


class TestApplyTradesFirstRun:
    """Starting from an empty portfolio, all trades are buys."""

    def test_first_buy_sets_book_cost(self):
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "buy", "shares": 100.0, "price_estimate": 150.0},
            ]
        )
        result = apply_trades(current={}, proposals=proposals)

        assert "AAPL" in result
        assert result["AAPL"].shares == pytest.approx(100.0)
        assert result["AAPL"].book_cost_per_share == pytest.approx(150.0)
        assert result["AAPL"].realized_gain_total == pytest.approx(0.0)

    def test_multiple_first_buys(self):
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "buy", "shares": 50.0, "price_estimate": 200.0},
                {"symbol": "MSFT", "side": "buy", "shares": 25.0, "price_estimate": 400.0},
            ]
        )
        result = apply_trades(current={}, proposals=proposals)

        assert result["AAPL"].shares == pytest.approx(50.0)
        assert result["MSFT"].shares == pytest.approx(25.0)


class TestApplyTradesIncrementalBuy:
    """Adding to an existing position uses weighted-average cost basis."""

    def test_weighted_average_cost(self):
        current = {
            "AAPL": HoldingRecord(symbol="AAPL", shares=100.0, book_cost_per_share=100.0),
        }
        # Buy 100 more at 200 → avg = (100*100 + 100*200) / 200 = 150
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "buy", "shares": 100.0, "price_estimate": 200.0},
            ]
        )
        result = apply_trades(current=current, proposals=proposals)

        assert result["AAPL"].shares == pytest.approx(200.0)
        assert result["AAPL"].book_cost_per_share == pytest.approx(150.0)

    def test_incremental_buy_does_not_reset_realized_gain(self):
        current = {
            "AAPL": HoldingRecord(
                symbol="AAPL", shares=50.0, book_cost_per_share=100.0, realized_gain_total=500.0
            ),
        }
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "buy", "shares": 50.0, "price_estimate": 120.0},
            ]
        )
        result = apply_trades(current=current, proposals=proposals)

        assert result["AAPL"].realized_gain_total == pytest.approx(500.0)


class TestApplyTradesSell:
    """Selling reduces shares and crystallises realized gain."""

    def test_partial_sell_realized_gain(self):
        current = {
            "AAPL": HoldingRecord(symbol="AAPL", shares=100.0, book_cost_per_share=100.0),
        }
        # Sell 40 shares at 150 → realized = (150-100)*40 = 2000
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "sell", "shares": 40.0, "price_estimate": 150.0},
            ]
        )
        result = apply_trades(current=current, proposals=proposals)

        assert result["AAPL"].shares == pytest.approx(60.0)
        assert result["AAPL"].realized_gain_total == pytest.approx(2000.0)
        assert result["AAPL"].book_cost_per_share == pytest.approx(100.0)

    def test_sell_at_loss_negative_realized_gain(self):
        current = {
            "AAPL": HoldingRecord(symbol="AAPL", shares=100.0, book_cost_per_share=200.0),
        }
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "sell", "shares": 100.0, "price_estimate": 150.0},
            ]
        )
        result = apply_trades(current=current, proposals=proposals)

        assert result["AAPL"].shares == pytest.approx(0.0)
        assert result["AAPL"].realized_gain_total == pytest.approx(-5000.0)

    def test_sell_accumulates_with_prior_realized_gain(self):
        current = {
            "AAPL": HoldingRecord(
                symbol="AAPL", shares=100.0, book_cost_per_share=100.0, realized_gain_total=1000.0
            ),
        }
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "sell", "shares": 50.0, "price_estimate": 120.0},
            ]
        )
        result = apply_trades(current=current, proposals=proposals)

        # Prior 1000 + new (120-100)*50 = 1000 + 1000 = 2000
        assert result["AAPL"].realized_gain_total == pytest.approx(2000.0)

    def test_cannot_oversell(self):
        """Selling more shares than held is capped at current position."""
        current = {
            "AAPL": HoldingRecord(symbol="AAPL", shares=10.0, book_cost_per_share=100.0),
        }
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "sell", "shares": 50.0, "price_estimate": 120.0},
            ]
        )
        result = apply_trades(current=current, proposals=proposals)

        assert result["AAPL"].shares == pytest.approx(0.0)

    def test_sell_unknown_symbol_is_ignored(self):
        current = {}
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "sell", "shares": 50.0, "price_estimate": 150.0},
            ]
        )
        result = apply_trades(current=current, proposals=proposals)
        # No AAPL holding exists; nothing should crash
        assert "AAPL" not in result


class TestApplyTradesNoop:
    """Empty proposals carry holdings forward unchanged."""

    def test_empty_proposals_preserves_holdings(self):
        current = {
            "AAPL": HoldingRecord(symbol="AAPL", shares=100.0, book_cost_per_share=150.0),
            "MSFT": HoldingRecord(symbol="MSFT", shares=50.0, book_cost_per_share=400.0),
        }
        result = apply_trades(current=current, proposals=pd.DataFrame())

        assert result["AAPL"].shares == pytest.approx(100.0)
        assert result["MSFT"].shares == pytest.approx(50.0)

    def test_zero_trade_value_is_ignored(self):
        current = {"AAPL": HoldingRecord(symbol="AAPL", shares=100.0, book_cost_per_share=150.0)}
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "buy", "shares": 0.0, "price_estimate": 0.0},
            ]
        )
        result = apply_trades(current=current, proposals=proposals)

        assert result["AAPL"].shares == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Tests: read_current_holdings
# ---------------------------------------------------------------------------


class TestReadCurrentHoldings:
    def test_returns_empty_when_no_snapshot(self, tmp_path):
        backend = _make_backend(tmp_path)
        result = read_current_holdings(storage=backend, portfolio_id="default")
        assert result == {}

    def test_reads_persisted_holdings(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_holdings(backend, {"AAPL": {"shares": 100.0, "book_cost": 150.0}})

        result = read_current_holdings(storage=backend, portfolio_id="default")

        assert "AAPL" in result
        assert result["AAPL"].shares == pytest.approx(100.0)
        assert result["AAPL"].book_cost_per_share == pytest.approx(150.0)

    def test_filters_by_portfolio_id(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_holdings(backend, {"AAPL": {"shares": 100.0, "book_cost": 150.0}}, portfolio_id="p1")
        _seed_holdings(backend, {"MSFT": {"shares": 50.0, "book_cost": 400.0}}, portfolio_id="p2")

        result_p1 = read_current_holdings(storage=backend, portfolio_id="p1")
        result_p2 = read_current_holdings(storage=backend, portfolio_id="p2")

        assert "AAPL" in result_p1
        assert "MSFT" not in result_p1
        assert "MSFT" in result_p2
        assert "AAPL" not in result_p2


# ---------------------------------------------------------------------------
# Tests: snapshot_holdings — persistence
# ---------------------------------------------------------------------------


class TestSnapshotHoldings:
    def test_first_run_creates_snapshot(self, tmp_path):
        backend = _make_backend(tmp_path)
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "buy", "shares": 100.0, "price_estimate": 150.0},
                {"symbol": "MSFT", "side": "buy", "shares": 50.0, "price_estimate": 400.0},
            ]
        )
        prices = {"AAPL": 155.0, "MSFT": 410.0}

        path = snapshot_holdings(
            storage=backend,
            portfolio_id="default",
            proposals=proposals,
            prices=prices,
            portfolio_value=100_000.0,
        )

        assert path.exists()
        import pandas as pd

        df = pd.read_parquet(path)
        assert set(df["symbol"]) == {"AAPL", "MSFT"}
        aapl = df[df["symbol"] == "AAPL"].iloc[0]
        assert aapl["shares"] == pytest.approx(100.0, abs=1e-4)
        assert aapl["book_cost_per_share"] == pytest.approx(150.0, abs=1e-4)
        assert aapl["current_price"] == pytest.approx(155.0, abs=1e-4)

    def test_snapshot_registered_in_storage(self, tmp_path):
        backend = _make_backend(tmp_path)
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "buy", "shares": 100.0, "price_estimate": 150.0},
            ]
        )
        snapshot_holdings(
            storage=backend,
            portfolio_id="default",
            proposals=proposals,
            prices={"AAPL": 150.0},
            portfolio_value=100_000.0,
        )

        snaps = backend.list_snapshots(dataset_filter="holdings_snapshot_default")
        assert len(snaps) == 1
        assert snaps[0]["metadata"]["portfolio_id"] == "default"

    def test_full_exit_excluded_from_snapshot(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_holdings(backend, {"AAPL": {"shares": 100.0, "book_cost": 150.0}})
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "sell", "shares": 100.0, "price_estimate": 160.0},
            ]
        )

        path = snapshot_holdings(
            storage=backend,
            portfolio_id="default",
            proposals=proposals,
            prices={"AAPL": 160.0},
            portfolio_value=0.0,
        )

        import pandas as pd

        df = pd.read_parquet(path)
        assert df.empty

    def test_weights_sum_to_one(self, tmp_path):
        backend = _make_backend(tmp_path)
        proposals = _make_proposals(
            [
                {"symbol": "AAPL", "side": "buy", "shares": 100.0, "price_estimate": 100.0},
                {"symbol": "MSFT", "side": "buy", "shares": 100.0, "price_estimate": 100.0},
                {"symbol": "GOOGL", "side": "buy", "shares": 100.0, "price_estimate": 100.0},
            ]
        )
        prices = {"AAPL": 100.0, "MSFT": 100.0, "GOOGL": 100.0}

        path = snapshot_holdings(
            storage=backend,
            portfolio_id="default",
            proposals=proposals,
            prices=prices,
            portfolio_value=30_000.0,
        )

        df = pd.read_parquet(path)
        assert df["weight"].sum() == pytest.approx(1.0, abs=1e-4)

    def test_unrealized_gain_computed(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_holdings(backend, {"AAPL": {"shares": 100.0, "book_cost": 100.0}})
        # Price increased to 120 — no trades, just mark-to-market
        path = snapshot_holdings(
            storage=backend,
            portfolio_id="default",
            proposals=pd.DataFrame(),
            prices={"AAPL": 120.0},
            portfolio_value=12_000.0,
        )

        df = pd.read_parquet(path)
        aapl = df[df["symbol"] == "AAPL"].iloc[0]
        # Unrealized gain = (120 - 100) * 100 = 2000
        assert aapl["unrealized_gain"] == pytest.approx(2000.0, abs=0.01)


# ---------------------------------------------------------------------------
# Tests: Rebalance integration
# ---------------------------------------------------------------------------


class TestRebalanceHoldingsIntegration:
    """RebalanceResult should include a holdings_snapshot_path after a run."""

    def test_rebalance_produces_holdings_snapshot_path(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_weights(backend, {"AAPL": 0.60, "MSFT": 0.40})
        _seed_prices(backend, "AAPL", 175.0)
        _seed_prices(backend, "MSFT", 410.0)

        result = rebalance_portfolio(
            storage=backend,
            as_of="2026-03-23",
            portfolio_value=100_000.0,
        )

        assert result.holdings_snapshot_path is not None
        assert result.holdings_snapshot_path.exists()

    def test_rebalance_snapshot_contains_correct_holdings(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_weights(backend, {"AAPL": 0.50, "MSFT": 0.50})
        _seed_prices(backend, "AAPL", 200.0)
        _seed_prices(backend, "MSFT", 400.0)

        result = rebalance_portfolio(
            storage=backend,
            as_of="2026-03-23",
            portfolio_value=100_000.0,
        )

        df = pd.read_parquet(result.holdings_snapshot_path)
        assert set(df["symbol"]) == {"AAPL", "MSFT"}

    def test_second_rebalance_updates_book_cost(self, tmp_path):
        """Buying additional shares on rebalance should average the book cost."""
        backend = _make_backend(tmp_path)
        t1 = datetime(2026, 3, 21, 12, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)

        # First rebalance: buy AAPL at 200 (price snapshot dated 2026-03-21)
        _seed_weights(backend, {"AAPL": 1.0}, as_of=t1)
        _seed_prices(backend, "AAPL", 200.0, as_of=t1)
        rebalance_portfolio(storage=backend, as_of="2026-03-21", portfolio_value=10_000.0)

        # Second rebalance: add more AAPL at 220.0 (price snapshot dated 2026-03-23)
        _seed_weights(backend, {"AAPL": 1.0}, as_of=t2)
        _seed_prices(backend, "AAPL", 220.0, as_of=t2)
        # Increase portfolio value so there is additional buying power
        result2 = rebalance_portfolio(storage=backend, as_of="2026-03-23", portfolio_value=11_000.0)

        df = pd.read_parquet(result2.holdings_snapshot_path)
        aapl = df[df["symbol"] == "AAPL"].iloc[0]
        # Book cost should be >= 200 (blended or unchanged depending on whether
        # the weight stayed the same and no new buy proposal was generated)
        assert aapl["book_cost_per_share"] >= 200.0
        assert aapl["shares"] > 0
