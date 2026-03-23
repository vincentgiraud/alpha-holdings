"""Universe construction from persisted price snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from alpha_holdings.data.storage import StorageBackend


DEFAULT_SEED_UNIVERSE_PATH = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "seed_universe.csv"


@dataclass(slots=True)
class UniverseBuildResult:
    """Result of a liquidity-filtered universe build."""

    symbols: list[str]
    diagnostics: pd.DataFrame
    members: pd.DataFrame


def build_liquid_universe_from_snapshots(
    *,
    storage: StorageBackend,
    as_of: str,
    lookback_days: int,
    min_avg_dollar_volume: float,
    seed_universe_path: Path | None = DEFAULT_SEED_UNIVERSE_PATH,
    base_currency: str = "USD",
) -> UniverseBuildResult:
    """Build a ticker universe from price snapshots filtered by average dollar volume."""
    seed_members = _load_seed_universe(seed_universe_path)
    allowed_symbols = set(seed_members["symbol"].tolist()) if not seed_members.empty else None
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

        metadata = snapshot["metadata"]
        symbol = _normalized_symbol(snapshot)
        if allowed_symbols is not None and symbol not in allowed_symbols:
            continue

        local_currency = str(metadata.get("currency") or base_currency).upper()
        fx_rate = _coerce_positive_float(metadata.get("fx_rate_to_usd"), default=1.0)
        base_fx_rate = _resolve_base_currency_rate(
            local_currency=local_currency,
            base_currency=base_currency,
            fx_rate_to_usd=fx_rate,
        )
        avg_dollar_volume_local = float((recent["close"] * recent["volume"]).mean())
        avg_dollar_volume_base = avg_dollar_volume_local * base_fx_rate
        passes = avg_dollar_volume_base >= min_avg_dollar_volume
        if passes:
            selected.append(symbol)

        member = _lookup_member(seed_members, symbol)

        rows.append(
            {
                "symbol": symbol,
                "security_id": str(metadata.get("security_id") or symbol),
                "provider_ticker": str(metadata.get("ticker") or symbol),
                "dataset": snapshot["dataset"],
                "snapshot_as_of": str(snapshot["as_of"]),
                "rows": int(len(frame)),
                "country": member.get("country"),
                "region": member.get("region"),
                "currency": local_currency,
                "base_currency": base_currency.upper(),
                "fx_rate_to_base": base_fx_rate,
                "avg_dollar_volume_local": avg_dollar_volume_local,
                "avg_dollar_volume": avg_dollar_volume_base,
                "passes_liquidity": passes,
            }
        )

    diagnostics = pd.DataFrame(rows)
    selected = sorted(set(selected))
    members = diagnostics.loc[diagnostics["passes_liquidity"]].copy() if not diagnostics.empty else pd.DataFrame()
    return UniverseBuildResult(symbols=selected, diagnostics=diagnostics, members=members)


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


def _load_seed_universe(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame(columns=["symbol", "security_id", "country", "currency", "region"])
    frame = pd.read_csv(path)
    if "symbol" not in frame.columns:
        raise ValueError(f"Seed universe file {path} must include a 'symbol' column.")
    normalized = frame.copy()
    normalized["symbol"] = normalized["symbol"].astype(str).str.upper().str.strip()
    if "security_id" not in normalized.columns:
        normalized["security_id"] = normalized["symbol"]
    return normalized


def _normalized_symbol(snapshot: dict[str, object]) -> str:
    metadata = snapshot["metadata"]
    return str(
        metadata.get("canonical_symbol")
        or metadata.get("symbol")
        or metadata.get("ticker")
        or str(snapshot["dataset"]).replace("_prices", "")
    ).upper()


def _lookup_member(seed_members: pd.DataFrame, symbol: str) -> dict[str, object]:
    if seed_members.empty:
        return {}
    matches = seed_members.loc[seed_members["symbol"] == symbol]
    if matches.empty:
        return {}
    return matches.iloc[0].to_dict()


def _coerce_positive_float(value: object, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _resolve_base_currency_rate(*, local_currency: str, base_currency: str, fx_rate_to_usd: float) -> float:
    normalized_base = base_currency.upper()
    normalized_local = local_currency.upper()
    if normalized_local == normalized_base:
        return 1.0
    if normalized_base != "USD":
        raise ValueError("Universe builder currently supports USD as the base currency only.")
    return fx_rate_to_usd
