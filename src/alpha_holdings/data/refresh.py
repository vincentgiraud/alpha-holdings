"""Refresh orchestration for provider fetch -> normalization -> storage."""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from alpha_holdings.data.providers.base import FundamentalsProvider, PriceProvider
from alpha_holdings.data.providers.free import (
    EdgarFundamentalsProvider,
    StooqPriceProvider,
    YahooPriceProvider,
)
from alpha_holdings.data.storage import StorageBackend
from alpha_holdings.domain.models import FundamentalSnapshot, PriceBar


@dataclass(slots=True)
class RefreshSummary:
    """Outcome of a refresh run."""

    tickers_requested: int
    tickers_succeeded: int
    tickers_failed: int
    price_snapshots_written: int
    fundamentals_snapshots_written: int
    failures: list[str]

    @property
    def snapshots_written(self) -> int:
        """Backward-compatible total snapshot count."""
        return self.price_snapshots_written + self.fundamentals_snapshots_written


def refresh_prices(
    *,
    universe_path: Path,
    start_date: date,
    end_date: date,
    storage: StorageBackend,
    preferred_source: str,
    fallback_source: str | None = None,
    providers: Mapping[str, PriceProvider] | None = None,
) -> RefreshSummary:
    """Refresh price data for a universe and persist raw + normalized snapshots."""
    provider_map = dict(providers or _default_price_providers())
    primary = preferred_source.lower().strip()
    fallback = fallback_source.lower().strip() if fallback_source else None
    if primary not in provider_map:
        raise ValueError(f"Unknown preferred source: {preferred_source}")
    if fallback and fallback not in provider_map:
        raise ValueError(f"Unknown fallback source: {fallback_source}")

    universe_rows = _load_universe_rows(universe_path)
    failures: list[str] = []
    price_snapshots_written = 0
    fundamentals_snapshots_written = 0
    succeeded = 0
    run_as_of = datetime.now(tz=UTC)
    fundamentals_provider = _default_fundamentals_provider()

    for ticker, country in universe_rows:
        result = _fetch_with_fallback(
            ticker=ticker,
            country=country,
            start_date=start_date,
            end_date=end_date,
            primary_source=primary,
            fallback_source=fallback,
            providers=provider_map,
        )
        if result is None:
            failures.append(ticker)
            continue

        source_used, bars = result
        raw_payload = [bar.model_dump(mode="json") for bar in bars]
        normalized_rows = [_price_bar_to_snapshot_row(bar) for bar in bars]
        dataset_name = f"{ticker}_prices"

        storage.write_raw_payload(
            provider=source_used,
            dataset=dataset_name,
            as_of=run_as_of,
            payload=raw_payload,
        )
        snapshot_path = storage.write_normalized_snapshot(
            dataset=dataset_name,
            as_of=run_as_of,
            rows=normalized_rows,
        )
        storage.register_snapshot(
            dataset=dataset_name,
            as_of=run_as_of,
            snapshot_path=snapshot_path,
            row_count=len(normalized_rows),
            metadata={
                "ticker": ticker,
                "source": source_used,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
        price_snapshots_written += 1

        fundamentals = _fetch_fundamentals_if_available(
            ticker=ticker,
            provider=fundamentals_provider,
        )
        if fundamentals:
            fundamentals_payload = [item.model_dump(mode="json") for item in fundamentals]
            fundamentals_rows = [
                _fundamental_snapshot_to_row(snapshot) for snapshot in fundamentals
            ]
            fundamentals_dataset = f"{ticker}_fundamentals"
            storage.write_raw_payload(
                provider=fundamentals_provider.source_id,
                dataset=fundamentals_dataset,
                as_of=run_as_of,
                payload=fundamentals_payload,
            )
            fundamentals_snapshot_path = storage.write_normalized_snapshot(
                dataset=fundamentals_dataset,
                as_of=run_as_of,
                rows=fundamentals_rows,
            )
            storage.register_snapshot(
                dataset=fundamentals_dataset,
                as_of=run_as_of,
                snapshot_path=fundamentals_snapshot_path,
                row_count=len(fundamentals_rows),
                metadata={
                    "ticker": ticker,
                    "source": fundamentals_provider.source_id,
                },
            )
            fundamentals_snapshots_written += 1
        succeeded += 1

    return RefreshSummary(
        tickers_requested=len(universe_rows),
        tickers_succeeded=succeeded,
        tickers_failed=len(failures),
        price_snapshots_written=price_snapshots_written,
        fundamentals_snapshots_written=fundamentals_snapshots_written,
        failures=failures,
    )


def load_universe_tickers(path: Path) -> list[str]:
    """Load ticker symbols from a universe CSV."""
    if not path.exists():
        raise FileNotFoundError(f"Universe file does not exist: {path}")

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        if not headers:
            raise ValueError("Universe CSV must include a header row.")

        ticker_column = _resolve_ticker_column(headers)
        tickers = [row[ticker_column].strip() for row in reader if row.get(ticker_column)]

    if not tickers:
        raise ValueError("Universe CSV contains no ticker values.")

    # Preserve order but de-duplicate.
    seen: set[str] = set()
    ordered: list[str] = []
    for ticker in tickers:
        if ticker not in seen:
            seen.add(ticker)
            ordered.append(ticker)
    return ordered


def _load_universe_rows(path: Path) -> list[tuple[str, str]]:
    """Load (ticker, country) pairs from a universe CSV.

    Falls back to empty country when the column is absent, so the provider's
    ``resolve_ticker`` receives enough context to apply exchange-suffix rules.
    """
    if not path.exists():
        raise FileNotFoundError(f"Universe file does not exist: {path}")

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        if not headers:
            raise ValueError("Universe CSV must include a header row.")

        ticker_column = _resolve_ticker_column(headers)
        country_lookup = {h.lower().strip(): h for h in headers}
        country_column = country_lookup.get("country")

        rows: list[tuple[str, str]] = []
        for row in reader:
            ticker = (row.get(ticker_column) or "").strip()
            if not ticker:
                continue
            country = (row.get(country_column) if country_column else "") or ""
            rows.append((ticker, country.strip()))

    if not rows:
        raise ValueError("Universe CSV contains no ticker values.")

    seen: set[str] = set()
    ordered: list[tuple[str, str]] = []
    for ticker, country in rows:
        if ticker not in seen:
            seen.add(ticker)
            ordered.append((ticker, country))
    return ordered


def _resolve_ticker_column(headers: list[str]) -> str:
    aliases = ["ticker", "symbol", "security_id"]
    lookup = {header.lower().strip(): header for header in headers}
    for alias in aliases:
        if alias in lookup:
            return lookup[alias]
    return headers[0]


def _default_price_providers() -> Mapping[str, PriceProvider]:
    return {
        "yahoo": YahooPriceProvider(),
        "stooq": StooqPriceProvider(),
    }


def _default_fundamentals_provider() -> FundamentalsProvider:
    return EdgarFundamentalsProvider()


def _fetch_with_fallback(
    *,
    ticker: str,
    country: str,
    start_date: date,
    end_date: date,
    primary_source: str,
    fallback_source: str | None,
    providers: Mapping[str, PriceProvider],
) -> tuple[str, list[PriceBar]] | None:
    primary_provider = providers[primary_source]
    resolved = primary_provider.resolve_ticker(ticker, country=country)
    try:
        bars = primary_provider.get_prices(
            ticker=resolved,
            start=start_date,
            end=end_date,
            adjusted=True,
        )
        return primary_source, bars
    except Exception:
        if not fallback_source:
            return None
        fallback_provider = providers[fallback_source]
        resolved_fb = fallback_provider.resolve_ticker(ticker, country=country)
        try:
            bars = fallback_provider.get_prices(
                ticker=resolved_fb,
                start=start_date,
                end=end_date,
                adjusted=True,
            )
            return fallback_source, bars
        except Exception:
            return None


def _price_bar_to_snapshot_row(bar: PriceBar) -> dict[str, object]:
    return {
        "security_id": bar.security_id,
        "date": bar.date,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "adjusted_close": float(bar.adjusted_close) if bar.adjusted_close is not None else None,
        "volume": bar.volume,
        "dividend": float(bar.dividend),
        "split_factor": float(bar.split_factor),
        "source": bar.quality.source,
        "currency": bar.quality.currency,
    }


def _fetch_fundamentals_if_available(
    *,
    ticker: str,
    provider: FundamentalsProvider,
    limit: int = 8,
) -> list[FundamentalSnapshot]:
    try:
        return provider.get_fundamentals(ticker=ticker, limit=limit)
    except Exception:
        return []


def _fundamental_snapshot_to_row(snapshot: FundamentalSnapshot) -> dict[str, object]:
    return {
        "security_id": snapshot.security_id,
        "period_end_date": snapshot.period_end_date,
        "period_type": snapshot.period_type,
        "revenue": float(snapshot.revenue) if snapshot.revenue is not None else None,
        "operating_income": (
            float(snapshot.operating_income) if snapshot.operating_income is not None else None
        ),
        "net_income": float(snapshot.net_income) if snapshot.net_income is not None else None,
        "eps": float(snapshot.eps) if snapshot.eps is not None else None,
        "book_value_per_share": (
            float(snapshot.book_value_per_share)
            if snapshot.book_value_per_share is not None
            else None
        ),
        "debt_to_equity": (
            float(snapshot.debt_to_equity) if snapshot.debt_to_equity is not None else None
        ),
        "current_ratio": (
            float(snapshot.current_ratio) if snapshot.current_ratio is not None else None
        ),
        "free_cash_flow": (
            float(snapshot.free_cash_flow) if snapshot.free_cash_flow is not None else None
        ),
        "shares_outstanding": (
            float(snapshot.shares_outstanding) if snapshot.shares_outstanding is not None else None
        ),
        "currency": snapshot.currency,
        "source": snapshot.quality.source,
        "publish_date": snapshot.quality.publish_date,
        "data_flags": list(snapshot.quality.data_flags),
    }
