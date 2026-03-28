from datetime import UTC, date, datetime
from pathlib import Path

from alpha_holdings.cli import _write_run_manifest
from alpha_holdings.data.refresh import refresh_prices
from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.domain.models import DataQuality, FundamentalSnapshot, PriceBar


class _StubProvider:
    source_id = "stub"

    def resolve_ticker(self, canonical, *, country=""):  # noqa: ARG002
        return canonical

    def get_prices(self, ticker, start, end, *, adjusted=True):
        _ = (start, end, adjusted)
        return [
            PriceBar(
                security_id=ticker,
                date=datetime(2025, 1, 2, tzinfo=UTC),
                open=100,
                high=101,
                low=99,
                close=100.5,
                volume=123,
                quality=DataQuality(source=self.source_id, as_of_date=datetime.now(tz=UTC)),
            )
        ]


class _StubFundamentalsProvider:
    source_id = "stub_fund"

    def get_fundamentals(self, ticker, *, limit=8):  # noqa: ARG002
        return [
            FundamentalSnapshot(
                security_id=ticker,
                period_end_date=datetime(2024, 12, 31, tzinfo=UTC),
                period_type="FY",
                revenue=1000,
                net_income=120,
                quality=DataQuality(source=self.source_id, as_of_date=datetime.now(tz=UTC)),
            )
        ]


def test_write_run_manifest_persists_snapshot(tmp_path):
    backend = LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )

    path = _write_run_manifest(
        storage=backend,
        workflow="score",
        inputs={"as_of": "2026-03-23"},
        outputs={"snapshot_path": "/tmp/snapshot.parquet"},
        warnings=["skipped:AAPL"],
    )

    assert path.exists()
    snapshots = backend.list_snapshots(dataset_filter="run_manifests")
    assert len(snapshots) == 1
    row = backend.read_snapshot(dataset="run_manifests", as_of=str(snapshots[0]["as_of"])[:10])
    assert row.iloc[0]["workflow"] == "score"


def test_refresh_summary_includes_snapshot_paths(tmp_path, monkeypatch):
    backend = LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )
    universe = tmp_path / "universe.csv"
    universe.write_text("symbol\nAAPL\n", encoding="utf-8")

    monkeypatch.setattr(
        "alpha_holdings.data.refresh._default_fundamentals_provider",
        lambda: _StubFundamentalsProvider(),
    )

    summary = refresh_prices(
        universe_path=Path(universe),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        storage=backend,
        preferred_source="stub",
        providers={"stub": _StubProvider()},
    )

    assert summary.snapshots_written == 2
    assert len(summary.snapshot_paths) == 2
