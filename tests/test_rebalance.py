"""Tests for the rebalance engine.

Validates trade proposal generation: initial construction (all buys),
rebalance with changes (buys + sells), no-op when weights unchanged,
output schema, and snapshot persistence.
"""

from datetime import UTC, datetime

import pytest

from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.portfolio.rebalance import rebalance_portfolio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path):
    """Create a local storage backend in tmp_path."""
    return LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )


def _seed_weights(backend, weights_dict, portfolio_id="default", as_of=None):
    """Write a portfolio_weights snapshot."""
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
    path = backend.write_normalized_snapshot(
        dataset="portfolio_weights",
        as_of=as_of,
        rows=rows,
    )
    backend.register_snapshot(
        dataset="portfolio_weights",
        as_of=as_of,
        snapshot_path=path,
        row_count=len(rows),
        metadata={"portfolio_id": portfolio_id},
    )
    return as_of


def _seed_prices(backend, symbol, price, n_days=5):
    """Write a price snapshot for a symbol."""
    rows = []
    for i in range(n_days):
        rows.append(
            {
                "date": f"2026-03-{19 + i:02d}",
                "open": float(price),
                "high": float(price) * 1.01,
                "low": float(price) * 0.99,
                "close": float(price),
                "adjusted_close": float(price),
                "volume": 1000000,
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


# ---------------------------------------------------------------------------
# Tests: Initial Rebalance (all buys)
# ---------------------------------------------------------------------------


class TestInitialRebalance:
    """When no prior weights exist, all trades should be buys."""

    def test_initial_rebalance_all_buys(self, tmp_path):
        backend = _make_backend(tmp_path)
        weights = {"AAPL": 0.40, "MSFT": 0.35, "GOOGL": 0.25}
        _seed_weights(backend, weights)
        for sym, price in [("AAPL", 175.0), ("MSFT", 410.0), ("GOOGL", 165.0)]:
            _seed_prices(backend, sym, price)

        result = rebalance_portfolio(
            storage=backend,
            as_of="2026-03-23",
            portfolio_value=1_000_000.0,
        )

        assert result.trades_count == 3
        assert result.buys == 3
        assert result.sells == 0
        assert all(result.proposals["side"] == "buy")

    def test_initial_rebalance_share_counts(self, tmp_path):
        backend = _make_backend(tmp_path)
        weights = {"AAPL": 0.50, "MSFT": 0.50}
        _seed_weights(backend, weights)
        _seed_prices(backend, "AAPL", 200.0)
        _seed_prices(backend, "MSFT", 400.0)

        result = rebalance_portfolio(
            storage=backend,
            as_of="2026-03-23",
            portfolio_value=100_000.0,
        )

        aapl = result.proposals[result.proposals["symbol"] == "AAPL"].iloc[0]
        msft = result.proposals[result.proposals["symbol"] == "MSFT"].iloc[0]
        # AAPL: 50% of 100k = 50k / 200 = 250 shares
        assert aapl["shares"] == pytest.approx(250.0, abs=0.1)
        # MSFT: 50% of 100k = 50k / 400 = 125 shares
        assert msft["shares"] == pytest.approx(125.0, abs=0.1)


# ---------------------------------------------------------------------------
# Tests: Rebalance with Changes
# ---------------------------------------------------------------------------


class TestRebalanceWithChanges:
    """When prior weights exist, should generate mixed buys and sells."""

    def test_rebalance_buys_and_sells(self, tmp_path):
        backend = _make_backend(tmp_path)
        # Prior weights (earlier timestamp)
        t1 = datetime(2026, 3, 22, 12, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
        _seed_weights(backend, {"AAPL": 0.50, "MSFT": 0.30, "GOOGL": 0.20}, as_of=t1)
        # New target weights (shift from AAPL to GOOGL)
        _seed_weights(backend, {"AAPL": 0.30, "MSFT": 0.30, "GOOGL": 0.40}, as_of=t2)
        for sym, price in [("AAPL", 175.0), ("MSFT", 410.0), ("GOOGL", 165.0)]:
            _seed_prices(backend, sym, price)

        result = rebalance_portfolio(
            storage=backend,
            as_of="2026-03-23",
            portfolio_value=1_000_000.0,
        )

        assert result.buys > 0
        assert result.sells > 0
        # AAPL should be a sell (0.50 → 0.30)
        aapl_row = result.proposals[result.proposals["symbol"] == "AAPL"].iloc[0]
        assert aapl_row["side"] == "sell"
        # GOOGL should be a buy (0.20 → 0.40)
        googl_row = result.proposals[result.proposals["symbol"] == "GOOGL"].iloc[0]
        assert googl_row["side"] == "buy"

    def test_no_trade_for_unchanged_weight(self, tmp_path):
        backend = _make_backend(tmp_path)
        t1 = datetime(2026, 3, 22, 12, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
        _seed_weights(backend, {"AAPL": 0.50, "MSFT": 0.50}, as_of=t1)
        _seed_weights(backend, {"AAPL": 0.30, "MSFT": 0.50, "GOOGL": 0.20}, as_of=t2)
        for sym, price in [("AAPL", 175.0), ("MSFT", 410.0), ("GOOGL", 165.0)]:
            _seed_prices(backend, sym, price)

        result = rebalance_portfolio(
            storage=backend,
            as_of="2026-03-23",
            portfolio_value=1_000_000.0,
        )

        # MSFT weight unchanged — should not appear in proposals
        msft_rows = result.proposals[result.proposals["symbol"] == "MSFT"]
        assert msft_rows.empty


# ---------------------------------------------------------------------------
# Tests: Edge Cases
# ---------------------------------------------------------------------------


class TestRebalanceEdgeCases:
    """Edge cases: missing prices, small portfolio value."""

    def test_missing_prices_still_generates_proposals(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_weights(backend, {"AAPL": 0.50, "UNKNOWN": 0.50})
        _seed_prices(backend, "AAPL", 175.0)
        # UNKNOWN has no prices

        result = rebalance_portfolio(
            storage=backend,
            as_of="2026-03-23",
            portfolio_value=100_000.0,
        )

        assert result.trades_count == 2
        # UNKNOWN should have 0 shares but still appear
        unknown_row = result.proposals[result.proposals["symbol"] == "UNKNOWN"].iloc[0]
        assert unknown_row["shares"] == 0.0

    def test_raises_without_portfolio_weights(self, tmp_path):
        backend = _make_backend(tmp_path)
        with pytest.raises(ValueError, match="No portfolio_weights snapshot found"):
            rebalance_portfolio(
                storage=backend,
                as_of="2026-03-23",
            )


# ---------------------------------------------------------------------------
# Tests: Output Schema and Persistence
# ---------------------------------------------------------------------------


class TestRebalanceOutput:
    """Output DataFrame schema and snapshot persistence."""

    def test_output_columns(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_weights(backend, {"AAPL": 0.60, "MSFT": 0.40})
        _seed_prices(backend, "AAPL", 175.0)
        _seed_prices(backend, "MSFT", 410.0)

        result = rebalance_portfolio(
            storage=backend,
            as_of="2026-03-23",
        )

        expected_cols = {
            "portfolio_id",
            "trade_date",
            "symbol",
            "side",
            "weight_change",
            "abs_weight_change",
            "shares",
            "price_estimate",
            "estimated_value",
            "reason",
        }
        assert expected_cols.issubset(set(result.proposals.columns))

    def test_snapshot_persisted(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_weights(backend, {"AAPL": 1.0})
        _seed_prices(backend, "AAPL", 175.0)

        result = rebalance_portfolio(
            storage=backend,
            as_of="2026-03-23",
        )

        assert result.snapshot_path.exists()
        snaps = backend.list_snapshots(dataset_filter="trade_proposals")
        assert len(snaps) >= 1

    def test_turnover_calculation(self, tmp_path):
        backend = _make_backend(tmp_path)
        t1 = datetime(2026, 3, 22, 12, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
        # Full rotation: AAPL → MSFT
        _seed_weights(backend, {"AAPL": 1.0}, as_of=t1)
        _seed_weights(backend, {"MSFT": 1.0}, as_of=t2)
        _seed_prices(backend, "AAPL", 175.0)
        _seed_prices(backend, "MSFT", 410.0)

        result = rebalance_portfolio(
            storage=backend,
            as_of="2026-03-23",
        )

        # Full rotation = 100% turnover
        assert result.estimated_turnover == pytest.approx(1.0, abs=0.01)
