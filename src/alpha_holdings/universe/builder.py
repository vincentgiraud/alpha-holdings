"""Universe construction from persisted price snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from alpha_holdings.data.storage import StorageBackend


@dataclass(slots=True)
class UniverseBuildResult:
    """Result of a liquidity-filtered universe build."""

    symbols: list[str]
    diagnostics: pd.DataFrame


def build_liquid_universe_from_snapshots(
    *,
    storage: StorageBackend,
    as_of: str,
    lookback_days: int,
    min_avg_dollar_volume: float,
) -> UniverseBuildResult:
    """Build a ticker universe from price snapshots filtered by average dollar volume."""
    selected: list[str] = []
    rows: list[dict[str, object]] = []

    for snapshot in _select_latest_price_snapshots(storage=storage, as_of=as_of):
        frame = storage.read_snapshot(dataset=snapshot["dataset"], as_of=as_of)
        if frame.empty or "close" not in frame.columns or "volume" not in frame.columns:
            continue

        sorted_frame = frame.sort_values("date") if "date" in frame.columns else frame
        recent = sorted_frame.tail(lookback_days)
        if recent.empty:
            continue

        avg_dollar_volume = float((recent["close"] * recent["volume"]).mean())
        symbol = str(snapshot["metadata"].get("ticker") or snapshot["dataset"].replace("_prices", "")).upper()
        passes = avg_dollar_volume >= min_avg_dollar_volume
        if passes:
            selected.append(symbol)

        rows.append(
            {
                "symbol": symbol,
                "dataset": snapshot["dataset"],
                "snapshot_as_of": str(snapshot["as_of"]),
                "rows": int(len(frame)),
                "avg_dollar_volume": avg_dollar_volume,
                "passes_liquidity": passes,
            }
        )

    diagnostics = pd.DataFrame(rows)
    selected = sorted(set(selected))
    return UniverseBuildResult(symbols=selected, diagnostics=diagnostics)


def _select_latest_price_snapshots(*, storage: StorageBackend, as_of: str) -> list[dict[str, object]]:
    snapshots = storage.list_snapshots()
    filtered = [
        snap
        for snap in snapshots
        if str(snap["dataset"]).endswith("_prices") and str(snap["as_of"]).startswith(as_of)
    ]
    latest_by_dataset: dict[str, dict[str, object]] = {}
    for snap in filtered:
        dataset = str(snap["dataset"])
        existing = latest_by_dataset.get(dataset)
        if existing is None or _to_datetime(snap["as_of"]) > _to_datetime(existing["as_of"]):
            latest_by_dataset[dataset] = snap
    return sorted(latest_by_dataset.values(), key=lambda row: str(row["dataset"]))


def _to_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
