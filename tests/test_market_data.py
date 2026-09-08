from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from alpha_holdings import fundamentals
from alpha_holdings.models import Fundamentals, FundamentalsResult, MarketDataStatus


class SuccessfulProvider:
    def fetch(self, ticker: str) -> Fundamentals:
        return Fundamentals(ticker=ticker, current_price=125.0, high_52w=150.0)


class FailingProvider:
    def fetch(self, ticker: str) -> Fundamentals:
        raise ConnectionError(f"provider offline for {ticker}")


class EmptyProvider:
    def fetch(self, ticker: str) -> Fundamentals:
        return Fundamentals(ticker=ticker)


class IdentityOnlyProvider:
    def fetch(self, ticker: str) -> Fundamentals:
        return Fundamentals(
            ticker=ticker,
            provider_symbol=ticker,
            name="Identity Only Corporation",
            quote_type="EQUITY",
            source="fixture",
        )


class MixedProvider:
    def fetch(self, ticker: str) -> Fundamentals:
        if ticker == "GOOD":
            return Fundamentals(ticker=ticker, current_price=42.0)
        raise TimeoutError("timed out")


def test_fetch_returns_an_available_timestamped_observation(tmp_path) -> None:
    now = datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc)

    result = fundamentals.fetch(
        "ACME",
        provider=SuccessfulProvider(),
        clock=lambda: now,
        cache_dir=tmp_path,
    )

    assert result.status is MarketDataStatus.AVAILABLE
    assert result.data == Fundamentals(
        ticker="ACME",
        current_price=125.0,
        high_52w=150.0,
        source="SuccessfulProvider",
        fetched_at=now,
    )
    assert result.observed_at == now
    assert result.as_of == now
    assert result.from_cache is False
    assert result.age == 0.0


def test_fetch_exposes_fresh_cache_age_and_as_of_time(tmp_path) -> None:
    as_of = datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc)
    fundamentals.fetch(
        "ACME",
        provider=SuccessfulProvider(),
        clock=lambda: as_of,
        cache_dir=tmp_path,
    )

    result = fundamentals.fetch(
        "ACME",
        provider=FailingProvider(),
        clock=lambda: as_of + timedelta(hours=2),
        cache_dir=tmp_path,
    )

    assert result.status is MarketDataStatus.AVAILABLE
    assert result.data is not None
    assert result.data.current_price == 125.0
    assert result.observed_at == as_of + timedelta(hours=2)
    assert result.as_of == as_of
    assert result.from_cache is True
    assert result.age == 7200.0


def test_fetch_does_not_treat_an_empty_fresh_cache_as_available(tmp_path) -> None:
    now = datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc)
    (tmp_path / "EMPTY.json").write_text(
        Fundamentals(ticker="EMPTY", fetched_at=now).model_dump_json()
    )

    result = fundamentals.fetch(
        "EMPTY",
        provider=FailingProvider(),
        clock=lambda: now,
        cache_dir=tmp_path,
    )

    assert result.status is MarketDataStatus.UNAVAILABLE
    assert result.data is None


def test_fetch_returns_stale_cache_when_the_provider_is_unavailable(tmp_path) -> None:
    as_of = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
    fundamentals.fetch(
        "ACME",
        provider=SuccessfulProvider(),
        clock=lambda: as_of,
        cache_dir=tmp_path,
    )

    result = fundamentals.fetch(
        "ACME",
        provider=FailingProvider(),
        clock=lambda: as_of + timedelta(hours=25),
        cache_dir=tmp_path,
    )

    assert result.status is MarketDataStatus.STALE
    assert result.data is not None
    assert result.as_of == as_of
    assert result.from_cache is True
    assert result.age == 90000.0
    assert result.reason == "provider offline for ACME"


def test_fetch_returns_stale_cache_when_the_refresh_payload_is_invalid(tmp_path) -> None:
    as_of = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
    fundamentals.fetch(
        "ACME",
        provider=SuccessfulProvider(),
        clock=lambda: as_of,
        cache_dir=tmp_path,
    )

    result = fundamentals.fetch(
        "ACME",
        provider=EmptyProvider(),
        clock=lambda: as_of + timedelta(hours=25),
        cache_dir=tmp_path,
    )

    assert result.status is MarketDataStatus.STALE
    assert result.data is not None
    assert result.as_of == as_of
    assert result.reason == "provider returned no market data"


def test_fetch_returns_unavailable_when_provider_fails_without_cache(tmp_path) -> None:
    now = datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc)

    result = fundamentals.fetch(
        "MISSING",
        provider=FailingProvider(),
        clock=lambda: now,
        cache_dir=tmp_path,
    )

    assert result.status is MarketDataStatus.UNAVAILABLE
    assert result.data is None
    assert result.observed_at == now
    assert result.as_of is None
    assert result.from_cache is False
    assert result.age is None
    assert result.reason == "provider offline for MISSING"


def test_default_provider_failure_is_exposed_as_unavailable(monkeypatch, tmp_path) -> None:
    now = datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc)

    def fail_to_create_ticker(_ticker: str):
        raise ConnectionError("yfinance offline")

    monkeypatch.setattr(fundamentals.yf, "Ticker", fail_to_create_ticker)

    result = fundamentals.fetch(
        "MISSING",
        provider=fundamentals.YahooFinanceProvider(),
        clock=lambda: now,
        cache_dir=tmp_path,
    )

    assert result.status is MarketDataStatus.UNAVAILABLE
    assert result.reason == "yfinance offline"


def test_fetch_marks_empty_provider_payload_as_invalid(tmp_path) -> None:
    now = datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc)

    result = fundamentals.fetch(
        "EMPTY",
        provider=EmptyProvider(),
        clock=lambda: now,
        cache_dir=tmp_path,
    )

    assert result.status is MarketDataStatus.INVALID
    assert result.data is None
    assert result.as_of is None
    assert result.reason == "provider returned no market data"
    assert not list(tmp_path.iterdir())


def test_fetch_marks_identity_only_payload_as_invalid(tmp_path) -> None:
    result = fundamentals.fetch(
        "EMPTY",
        provider=IdentityOnlyProvider(),
        clock=lambda: datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc),
        cache_dir=tmp_path,
    )

    assert result.status is MarketDataStatus.INVALID
    assert result.reason == "provider returned no market data"


def test_fetch_batch_preserves_each_tickers_explicit_outcome(tmp_path) -> None:
    now = datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc)

    results = fundamentals.fetch_batch(
        ["GOOD", "BAD"],
        provider=MixedProvider(),
        clock=lambda: now,
        cache_dir=tmp_path,
    )

    assert results["GOOD"].status is MarketDataStatus.AVAILABLE
    assert results["GOOD"].data is not None
    assert results["GOOD"].data.current_price == 42.0
    assert results["BAD"].status is MarketDataStatus.UNAVAILABLE
    assert results["BAD"].reason == "timed out"


@pytest.mark.parametrize(
    "values",
    [
        {"status": MarketDataStatus.AVAILABLE},
        {"status": MarketDataStatus.STALE},
        {
            "status": MarketDataStatus.UNAVAILABLE,
            "data": Fundamentals(ticker="ACME", current_price=1),
        },
        {"status": MarketDataStatus.INVALID, "as_of": datetime(2026, 9, 8, tzinfo=timezone.utc)},
    ],
)
def test_market_data_result_rejects_contradictory_states(values) -> None:
    with pytest.raises(ValidationError):
        FundamentalsResult(
            ticker="ACME",
            observed_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
            **values,
        )


def test_market_data_result_rejects_negative_age() -> None:
    with pytest.raises(ValidationError):
        FundamentalsResult(
            ticker="ACME",
            status=MarketDataStatus.UNAVAILABLE,
            observed_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
            age=-1,
        )
