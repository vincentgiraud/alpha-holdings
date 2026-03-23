"""Tests for storage backend abstraction and local implementation."""

from datetime import datetime, timezone

import duckdb
import pandas as pd
import pytest

from alpha_holdings.data.storage import (
    AzureBlobStorageBackend,
    LocalStorageBackend,
    build_storage_backend,
)


def test_local_storage_writes_raw_and_parquet_and_registers_metadata(tmp_path):
    root = tmp_path / "data"
    db = tmp_path / "meta" / "alpha.duckdb"
    backend = LocalStorageBackend(root_path=root, database_path=db)
    as_of = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)

    raw_path = backend.write_raw_payload(
        provider="yahoo",
        dataset="prices",
        as_of=as_of,
        payload={"ticker": "AAPL", "rows": 2},
    )
    snapshot_path = backend.write_normalized_snapshot(
        dataset="prices",
        as_of=as_of,
        rows=[
            {"security_id": "AAPL", "close": 100.0},
            {"security_id": "MSFT", "close": 200.0},
        ],
    )
    backend.register_snapshot(
        dataset="prices",
        as_of=as_of,
        snapshot_path=snapshot_path,
        row_count=2,
        metadata={"source": "test"},
    )

    assert raw_path.exists()
    assert snapshot_path.exists()

    frame = pd.read_parquet(snapshot_path)
    assert len(frame) == 2
    assert set(frame.columns) == {"security_id", "close"}

    with duckdb.connect(str(db)) as con:
        rows = con.execute("SELECT dataset, row_count FROM snapshots").fetchall()
    assert rows == [("prices", 2)]


def test_build_storage_backend_returns_local_backend(tmp_path):
    backend = build_storage_backend(
        backend="local",
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
    )
    assert isinstance(backend, LocalStorageBackend)


def test_build_storage_backend_requires_azure_settings(tmp_path):
    with pytest.raises(ValueError):
        build_storage_backend(
            backend="azure_blob",
            root_path=tmp_path / "data",
            database_path=tmp_path / "alpha.duckdb",
        )


def test_build_storage_backend_returns_azure_seam(tmp_path):
    backend = build_storage_backend(
        backend="azure_blob",
        root_path=tmp_path / "data",
        database_path=tmp_path / "alpha.duckdb",
        azure_account_url="https://examplestorage.blob.core.windows.net",
        azure_container="snapshots",
    )
    assert isinstance(backend, AzureBlobStorageBackend)
