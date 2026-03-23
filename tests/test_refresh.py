"""Tests for refresh orchestration and universe loading."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from alpha_holdings.data.refresh import load_universe_tickers, refresh_prices
from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.domain.models import DataQuality, PriceBar


class _StubProvider:
    def __init__(self, source_id: str, should_fail: bool = False):
        self.source_id = source_id
        self.should_fail = should_fail

    def get_prices(self, ticker, start, end, *, adjusted=True):
        _ = (start, end, adjusted)
        if self.should_fail:
            raise RuntimeError("provider failure")
        return [
            PriceBar(
                security_id=ticker,
                date=datetime(2025, 1, 2, tzinfo=UTC),
                open=Decimal("100.0"),
                high=Decimal("101.0"),
                low=Decimal("99.0"),
                close=Decimal("100.5"),
                volume=123,
                quality=DataQuality(
                    source=self.source_id,
                    as_of_date=datetime.now(tz=UTC),
                ),
            )
        ]


def test_load_universe_tickers_uses_ticker_column_and_deduplicates(tmp_path):
    universe = tmp_path / "universe.csv"
    universe.write_text("ticker,name\nAAPL,Apple\nMSFT,Microsoft\nAAPL,Apple\n", encoding="utf-8")

    tickers = load_universe_tickers(universe)

    assert tickers == ["AAPL", "MSFT"]


def test_refresh_prices_uses_fallback_and_writes_metadata(tmp_path):
    universe = tmp_path / "universe.csv"
    universe.write_text("symbol\nAAPL\n", encoding="utf-8")
    storage = LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )

    summary = refresh_prices(
        universe_path=Path(universe),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        storage=storage,
        preferred_source="yahoo",
        fallback_source="stooq",
        providers={
            "yahoo": _StubProvider("yahoo", should_fail=True),
            "stooq": _StubProvider("stooq", should_fail=False),
        },
    )

    assert summary.tickers_requested == 1
    assert summary.tickers_succeeded == 1
    assert summary.tickers_failed == 0
    assert summary.snapshots_written == 1

    with duckdb.connect(str(tmp_path / "alpha.duckdb")) as con:
        rows = con.execute("SELECT dataset, metadata_json FROM snapshots").fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "aapl_prices"
    assert '"source": "stooq"' in rows[0][1]
