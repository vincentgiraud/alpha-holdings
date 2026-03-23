"""Storage backends for snapshots and metadata.

Design goal:
- Keep downstream workflows backend-agnostic.
- Implement local backend first.
- Preserve a clear seam for Azure/object-store backends later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

import duckdb
import pandas as pd


class StorageBackend(Protocol):
    """Storage backend contract for normalized and raw datasets."""

    def write_raw_payload(
        self,
        *,
        provider: str,
        dataset: str,
        as_of: datetime,
        payload: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> Path:
        """Persist raw provider payload and return persisted path."""

    def write_normalized_snapshot(
        self,
        *,
        dataset: str,
        as_of: datetime,
        rows: list[Mapping[str, Any]],
    ) -> Path:
        """Persist normalized rows as parquet and return persisted path."""

    def register_snapshot(
        self,
        *,
        dataset: str,
        as_of: datetime,
        snapshot_path: Path,
        row_count: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Register snapshot metadata in relational index."""

    def list_snapshots(self, *, dataset_filter: str | None = None) -> list[dict[str, Any]]:
        """Return registered snapshots, optionally filtered by dataset name."""

    def read_snapshot(self, *, dataset: str, as_of: str) -> pd.DataFrame:
        """Read a specific snapshot Parquet file and return its contents."""


@dataclass(slots=True)
class LocalStorageBackend:
    """Local filesystem + DuckDB storage backend."""

    root_path: Path
    database_path: Path

    def __post_init__(self) -> None:
        self.root_path.mkdir(parents=True, exist_ok=True)
        self._raw_path.mkdir(parents=True, exist_ok=True)
        self._normalized_path.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @property
    def _raw_path(self) -> Path:
        return self.root_path / "raw"

    @property
    def _normalized_path(self) -> Path:
        return self.root_path / "normalized"

    def write_raw_payload(
        self,
        *,
        provider: str,
        dataset: str,
        as_of: datetime,
        payload: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> Path:
        safe_provider = _slug(provider)
        safe_dataset = _slug(dataset)
        dt_key = _format_dt_key(as_of)
        target_dir = self._raw_path / safe_provider / safe_dataset / f"as_of={dt_key}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "payload.json"

        with target.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, default=_json_default)

        return target

    def write_normalized_snapshot(
        self,
        *,
        dataset: str,
        as_of: datetime,
        rows: list[Mapping[str, Any]],
    ) -> Path:
        safe_dataset = _slug(dataset)
        dt_key = _format_dt_key(as_of)
        target_dir = self._normalized_path / safe_dataset / f"as_of={dt_key}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "snapshot.parquet"

        frame = pd.DataFrame(rows)
        frame.to_parquet(target, index=False)
        return target

    def register_snapshot(
        self,
        *,
        dataset: str,
        as_of: datetime,
        snapshot_path: Path,
        row_count: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        with duckdb.connect(str(self.database_path)) as con:
            con.execute(
                """
                INSERT INTO snapshots (
                    dataset,
                    as_of,
                    snapshot_path,
                    row_count,
                    metadata_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    _slug(dataset),
                    as_of.astimezone(timezone.utc),
                    str(snapshot_path),
                    row_count,
                    json.dumps(metadata or {}, sort_keys=True),
                    datetime.now(tz=timezone.utc),
                ],
            )

    def list_snapshots(self, *, dataset_filter: str | None = None) -> list[dict[str, Any]]:
        """Return registered snapshots, optionally filtered by dataset name."""
        with duckdb.connect(str(self.database_path)) as con:
            if dataset_filter:
                rows = con.execute(
                    "SELECT dataset, as_of, row_count, metadata_json, created_at "
                    "FROM snapshots WHERE dataset = ? ORDER BY as_of DESC",
                    [_slug(dataset_filter)],
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT dataset, as_of, row_count, metadata_json, created_at "
                    "FROM snapshots ORDER BY dataset, as_of DESC"
                ).fetchall()
        return [
            {
                "dataset": r[0],
                "as_of": r[1],
                "row_count": r[2],
                "metadata": json.loads(r[3]) if r[3] else {},
                "created_at": r[4],
            }
            for r in rows
        ]

    def read_snapshot(self, *, dataset: str, as_of: str) -> pd.DataFrame:
        """Read a specific snapshot Parquet file and return its contents."""
        with duckdb.connect(str(self.database_path)) as con:
            rows = con.execute(
                "SELECT snapshot_path FROM snapshots WHERE dataset = ? AND as_of::VARCHAR LIKE ? ORDER BY as_of DESC LIMIT 1",
                [_slug(dataset), f"{as_of}%"],
            ).fetchone()
        if not rows:
            with duckdb.connect(str(self.database_path)) as con2:
                known = [
                    r[0]
                    for r in con2.execute("SELECT DISTINCT dataset FROM snapshots ORDER BY dataset").fetchall()
                ]
            hint = f" Known datasets: {', '.join(known)}" if known else ""
            raise FileNotFoundError(
                f"No snapshot found for dataset={dataset!r} with as_of matching {as_of!r}.{hint}"
            )
        return pd.read_parquet(rows[0])

    def _ensure_schema(self) -> None:
        with duckdb.connect(str(self.database_path)) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    dataset VARCHAR NOT NULL,
                    as_of TIMESTAMP WITH TIME ZONE NOT NULL,
                    snapshot_path VARCHAR NOT NULL,
                    row_count BIGINT NOT NULL,
                    metadata_json VARCHAR,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
                """
            )


@dataclass(slots=True)
class AzureBlobStorageBackend:
    """Cloud backend seam for future Azure implementation.

    Intentionally not implemented in this phase. The contract exists now so
    refresh/scoring flows can depend on the interface rather than local IO.
    """

    account_url: str
    container: str
    prefix: str = "alpha-holdings"

    def write_raw_payload(
        self,
        *,
        provider: str,
        dataset: str,
        as_of: datetime,
        payload: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> Path:
        raise NotImplementedError("Azure blob backend will be implemented in DevOps phase.")

    def write_normalized_snapshot(
        self,
        *,
        dataset: str,
        as_of: datetime,
        rows: list[Mapping[str, Any]],
    ) -> Path:
        raise NotImplementedError("Azure blob backend will be implemented in DevOps phase.")

    def register_snapshot(
        self,
        *,
        dataset: str,
        as_of: datetime,
        snapshot_path: Path,
        row_count: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError("Azure metadata backend will be implemented in DevOps phase.")

    def list_snapshots(self, *, dataset_filter: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError("Azure metadata backend will be implemented in DevOps phase.")

    def read_snapshot(self, *, dataset: str, as_of: str) -> pd.DataFrame:
        raise NotImplementedError("Azure metadata backend will be implemented in DevOps phase.")


def build_storage_backend(
    *,
    backend: str,
    root_path: Path,
    database_path: Path,
    azure_account_url: str | None = None,
    azure_container: str | None = None,
    azure_prefix: str = "alpha-holdings",
) -> StorageBackend:
    """Build a configured storage backend by name."""
    key = backend.lower().strip()
    if key == "local":
        return LocalStorageBackend(root_path=root_path, database_path=database_path)
    if key == "azure_blob":
        if not azure_account_url or not azure_container:
            raise ValueError("azure_account_url and azure_container are required for azure_blob backend.")
        return AzureBlobStorageBackend(
            account_url=azure_account_url,
            container=azure_container,
            prefix=azure_prefix,
        )
    raise ValueError(f"Unsupported storage backend: {backend}")


def _slug(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _format_dt_key(value: datetime) -> str:
    utc = value.astimezone(timezone.utc)
    return utc.strftime("%Y%m%dT%H%M%SZ")


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)
