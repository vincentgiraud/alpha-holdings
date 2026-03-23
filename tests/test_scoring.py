"""Tests for Phase 3 universe filtering and scoring workflow."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.scoring import score_equities_from_snapshots
from alpha_holdings.universe import build_liquid_universe_from_snapshots

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def test_build_liquid_universe_filters_low_liquidity(tmp_path):
    backend = LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )
    as_of = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)

    _write_price_snapshot(
        backend, "aapl_prices", "AAPL", as_of, base_close=100.0, base_volume=1_000_000
    )
    _write_price_snapshot(backend, "tiny_prices", "TINY", as_of, base_close=10.0, base_volume=1_000)

    universe = build_liquid_universe_from_snapshots(
        storage=backend,
        as_of="2026-03-23",
        lookback_days=5,
        min_avg_dollar_volume=1_000_000,
        seed_universe_path=None,
    )

    assert universe.symbols == ["AAPL"]
    assert len(universe.diagnostics) == 2
    assert set(universe.diagnostics["passes_liquidity"].tolist()) == {True, False}


def test_build_liquid_universe_uses_seed_membership_and_currency_normalization(tmp_path):
    backend = LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )
    as_of = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)

    _write_price_snapshot(
        backend,
        "novn_prices",
        "NOVN.SW",
        as_of,
        base_close=95.0,
        base_volume=20_000,
        metadata={
            "canonical_symbol": "NOVN",
            "security_id": "CH0002005267",
            "currency": "CHF",
            "fx_rate_to_usd": 1.12,
        },
    )
    _write_price_snapshot(
        backend,
        "shop_prices",
        "SHOP.TO",
        as_of,
        base_close=50.0,
        base_volume=20_000,
        metadata={
            "canonical_symbol": "SHOP",
            "security_id": "CA82509L1076",
            "currency": "CAD",
            "fx_rate_to_usd": 0.74,
        },
    )

    universe = build_liquid_universe_from_snapshots(
        storage=backend,
        as_of="2026-03-23",
        lookback_days=5,
        min_avg_dollar_volume=2_000_000,
        seed_universe_path=FIXTURES_DIR / "seed_universe.csv",
    )

    assert universe.symbols == ["NOVN"]
    assert universe.members["security_id"].tolist() == ["CH0002005267"]
    assert universe.members["currency"].tolist() == ["CHF"]
    assert round(float(universe.members.iloc[0]["avg_dollar_volume"]), 2) > 2_000_000
    assert set(universe.diagnostics["symbol"].tolist()) == {"NOVN", "SHOP"}
    assert (
        universe.diagnostics.loc[
            universe.diagnostics["symbol"] == "SHOP", "passes_liquidity"
        ].item()
        is False
    )


def test_score_equities_from_snapshots_computes_factor_contributions_and_registers_snapshot(
    tmp_path,
):
    backend = LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )
    as_of = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)

    _write_price_snapshot(
        backend, "aapl_prices", "AAPL", as_of, base_close=100.0, base_volume=1_000_000
    )
    _write_price_snapshot(
        backend, "msft_prices", "MSFT", as_of, base_close=120.0, base_volume=900_000
    )

    summary = score_equities_from_snapshots(
        storage=backend,
        as_of="2026-03-23",
        lookback_days=5,
        min_avg_dollar_volume=100_000,
    )

    assert summary.securities_scored == 2
    assert summary.universe_size == 2
    assert summary.snapshot_path.exists()
    assert "composite_score" in summary.scores.columns
    assert "factor_momentum" in summary.scores.columns
    assert "factor_low_volatility" in summary.scores.columns
    assert "factor_liquidity" in summary.scores.columns

    with duckdb.connect(str(tmp_path / "alpha.duckdb")) as con:
        rows = con.execute(
            "SELECT dataset, row_count, metadata_json FROM snapshots WHERE dataset = 'equity_scores'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "equity_scores"
    assert rows[0][1] == 2
    assert '"requested_as_of": "2026-03-23"' in rows[0][2]


def _write_price_snapshot(
    backend: LocalStorageBackend,
    dataset: str,
    ticker: str,
    as_of: datetime,
    *,
    base_close: float,
    base_volume: int,
    metadata: dict[str, object] | None = None,
) -> None:
    rows = []
    for idx in range(10):
        day = as_of - timedelta(days=10 - idx)
        rows.append(
            {
                "security_id": ticker,
                "date": day,
                "close": base_close + idx,
                "adjusted_close": base_close + idx,
                "volume": base_volume + (idx * 1000),
            }
        )

    snapshot_path = backend.write_normalized_snapshot(dataset=dataset, as_of=as_of, rows=rows)
    backend.register_snapshot(
        dataset=dataset,
        as_of=as_of,
        snapshot_path=snapshot_path,
        row_count=len(rows),
        metadata={"ticker": ticker, "source": "test", **(metadata or {})},
    )
