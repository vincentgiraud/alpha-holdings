"""Tests for refresh orchestration and universe loading."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from alpha_holdings.data.refresh import load_universe_tickers, refresh_prices
from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.domain.models import DataQuality, FundamentalSnapshot, PriceBar


class _StubProvider:
    def __init__(self, source_id: str, should_fail: bool = False):
        self.source_id = source_id
        self.should_fail = should_fail

    def resolve_ticker(self, canonical, *, country=""):  # noqa: ARG002
        return canonical

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


class _StubFundamentalsProvider:
    source_id = "edgar"

    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    def get_fundamentals(self, ticker, *, limit=8):
        _ = limit
        if self.should_fail:
            raise RuntimeError("provider failure")
        return [
            FundamentalSnapshot(
                security_id=ticker,
                period_end_date=datetime(2024, 12, 31, tzinfo=UTC),
                period_type="FY",
                revenue=Decimal("1000.0"),
                net_income=Decimal("120.0"),
                eps=Decimal("5.5"),
                free_cash_flow=Decimal("140.0"),
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


def test_refresh_prices_uses_fallback_and_writes_metadata(tmp_path, monkeypatch):
    universe = tmp_path / "universe.csv"
    universe.write_text("symbol\nAAPL\n", encoding="utf-8")
    storage = LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )

    monkeypatch.setattr(
        "alpha_holdings.data.refresh._default_fundamentals_provider",
        lambda: _StubFundamentalsProvider(should_fail=True),
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
    assert summary.price_snapshots_written == 1
    assert summary.fundamentals_snapshots_written == 0
    assert summary.snapshots_written == 1

    with duckdb.connect(str(tmp_path / "alpha.duckdb")) as con:
        rows = con.execute("SELECT dataset, metadata_json FROM snapshots").fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "aapl_prices"
    assert '"source": "stooq"' in rows[0][1]


def test_refresh_prices_writes_fundamentals_snapshot_when_available(tmp_path, monkeypatch):
    universe = tmp_path / "universe.csv"
    universe.write_text("symbol\nAAPL\n", encoding="utf-8")
    storage = LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )

    monkeypatch.setattr(
        "alpha_holdings.data.refresh._default_fundamentals_provider",
        lambda: _StubFundamentalsProvider(),
    )

    summary = refresh_prices(
        universe_path=Path(universe),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        storage=storage,
        preferred_source="yahoo",
        fallback_source=None,
        providers={
            "yahoo": _StubProvider("yahoo", should_fail=False),
        },
    )

    assert summary.price_snapshots_written == 1
    assert summary.fundamentals_snapshots_written == 1
    assert summary.snapshots_written == 2

    with duckdb.connect(str(tmp_path / "alpha.duckdb")) as con:
        rows = con.execute(
            "SELECT dataset, metadata_json FROM snapshots ORDER BY dataset"
        ).fetchall()

    assert [row[0] for row in rows] == ["aapl_fundamentals", "aapl_prices"]
    assert '"source": "edgar"' in rows[0][1]


def test_refresh_prices_ignores_fundamentals_failures(tmp_path, monkeypatch):
    universe = tmp_path / "universe.csv"
    universe.write_text("symbol\nAAPL\n", encoding="utf-8")
    storage = LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )

    monkeypatch.setattr(
        "alpha_holdings.data.refresh._default_fundamentals_provider",
        lambda: _StubFundamentalsProvider(should_fail=True),
    )

    summary = refresh_prices(
        universe_path=Path(universe),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        storage=storage,
        preferred_source="yahoo",
        fallback_source=None,
        providers={
            "yahoo": _StubProvider("yahoo", should_fail=False),
        },
    )

    assert summary.tickers_succeeded == 1
    assert summary.price_snapshots_written == 1
    assert summary.fundamentals_snapshots_written == 0
