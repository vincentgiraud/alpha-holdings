"""Canonical normalization helpers for provider payloads.

The provider adapters can return canonical models directly, but the refresh
pipeline also needs a deterministic normalization layer to transform raw payload
rows into the domain contracts used by downstream scoring and portfolio logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import NotRequired, TypedDict

from alpha_holdings.domain.models import DataQuality, FundamentalSnapshot, PriceBar

_MISSING = object()


class YahooPriceRow(TypedDict):
    """Expected Yahoo-like EOD price row schema."""

    Date: date | datetime | str
    Open: float | int | str | Decimal
    High: float | int | str | Decimal
    Low: float | int | str | Decimal
    Close: float | int | str | Decimal
    Volume: int | float | str
    AdjClose: NotRequired[float | int | str | Decimal]
    Adj_Close: NotRequired[float | int | str | Decimal]
    AdjCloseRaw: NotRequired[float | int | str | Decimal]
    Dividends: NotRequired[float | int | str | Decimal]
    StockSplits: NotRequired[float | int | str | Decimal]


class StooqPriceRow(TypedDict):
    """Expected Stooq-like EOD price row schema."""

    Date: date | datetime | str
    Open: float | int | str | Decimal
    High: float | int | str | Decimal
    Low: float | int | str | Decimal
    Close: float | int | str | Decimal
    Volume: NotRequired[int | float | str]


class EdgarFundamentalRow(TypedDict):
    """Expected EDGAR-normalized period row schema."""

    period_end_date: date | datetime | str
    period_type: NotRequired[str]
    form: NotRequired[str]
    revenue: NotRequired[float | int | str | Decimal]
    operating_income: NotRequired[float | int | str | Decimal]
    net_income: NotRequired[float | int | str | Decimal]
    eps: NotRequired[float | int | str | Decimal]
    book_value_per_share: NotRequired[float | int | str | Decimal]
    debt_to_equity: NotRequired[float | int | str | Decimal]
    current_ratio: NotRequired[float | int | str | Decimal]
    free_cash_flow: NotRequired[float | int | str | Decimal]
    shares_outstanding: NotRequired[float | int | str | Decimal]
    publish_date: NotRequired[date | datetime | str]


def normalize_yahoo_price_rows(
    security_id: str,
    rows: list[Mapping[str, object]],
    *,
    as_of_date: datetime | None = None,
    currency: str = "USD",
) -> list[PriceBar]:
    """Normalize Yahoo-like rows into canonical ``PriceBar`` objects."""
    normalized: list[PriceBar] = []
    as_of = as_of_date or _utc_now()

    for row in rows:
        date_value = _pick(row, "Date", "date")
        open_value = _pick(row, "Open", "open")
        high_value = _pick(row, "High", "high")
        low_value = _pick(row, "Low", "low")
        close_value = _pick(row, "Close", "close")
        volume_value = _pick(row, "Volume", "volume", default=0)
        adjusted_close = _pick(
            row,
            "Adj Close",
            "AdjClose",
            "adj_close",
            "adjusted_close",
            default=None,
        )

        data_flags: list[str] = ["normalized", "yahoo"]
        if adjusted_close is None:
            data_flags.append("adjusted_close_missing")

        quality = DataQuality(
            source="yahoo",
            as_of_date=as_of,
            currency=currency,
            data_flags=data_flags,
        )

        normalized.append(
            PriceBar(
                security_id=security_id,
                date=_to_datetime_utc(date_value),
                open=_to_decimal(open_value),
                high=_to_decimal(high_value),
                low=_to_decimal(low_value),
                close=_to_decimal(close_value),
                adjusted_close=_to_decimal(adjusted_close),
                volume=_to_int(volume_value),
                dividend=_to_decimal(_pick(row, "Dividends", "dividend", default=0)),
                split_factor=_to_decimal(
                    _pick(row, "Stock Splits", "StockSplits", "split_factor", default=1)
                ),
                quality=quality,
            )
        )

    return sorted(normalized, key=lambda item: item.date)


def normalize_stooq_price_rows(
    security_id: str,
    rows: list[Mapping[str, object]],
    *,
    as_of_date: datetime | None = None,
    currency: str = "USD",
) -> list[PriceBar]:
    """Normalize Stooq-like rows into canonical ``PriceBar`` objects."""
    normalized: list[PriceBar] = []
    as_of = as_of_date or _utc_now()

    for row in rows:
        quality = DataQuality(
            source="stooq",
            as_of_date=as_of,
            currency=currency,
            data_flags=["normalized", "unadjusted", "stooq"],
        )

        normalized.append(
            PriceBar(
                security_id=security_id,
                date=_to_datetime_utc(_pick(row, "Date", "date")),
                open=_to_decimal(_pick(row, "Open", "open")),
                high=_to_decimal(_pick(row, "High", "high")),
                low=_to_decimal(_pick(row, "Low", "low")),
                close=_to_decimal(_pick(row, "Close", "close")),
                adjusted_close=None,
                volume=_to_int(_pick(row, "Volume", "volume", default=0)),
                quality=quality,
            )
        )

    return sorted(normalized, key=lambda item: item.date)


def normalize_edgar_fundamental_rows(
    security_id: str,
    rows: list[Mapping[str, object]],
    *,
    as_of_date: datetime | None = None,
    currency: str = "USD",
) -> list[FundamentalSnapshot]:
    """Normalize EDGAR-like period rows into canonical ``FundamentalSnapshot`` objects."""
    normalized: list[FundamentalSnapshot] = []
    as_of = as_of_date or _utc_now()

    for row in rows:
        publish_date = _pick(row, "publish_date", "filed", default=None)
        quality = DataQuality(
            source="edgar",
            as_of_date=as_of,
            publish_date=_to_datetime_utc(publish_date) if publish_date is not None else None,
            currency=currency,
            data_flags=["normalized", "no_point_in_time", "edgar"],
        )

        period_type = _normalize_period_type(
            _pick(row, "period_type", default=None),
            _pick(row, "form", default=None),
            _pick(row, "period_end_date", "end"),
        )

        normalized.append(
            FundamentalSnapshot(
                security_id=security_id,
                period_end_date=_to_datetime_utc(_pick(row, "period_end_date", "end")),
                period_type=period_type,
                revenue=_to_decimal(_pick(row, "revenue", default=None)),
                operating_income=_to_decimal(_pick(row, "operating_income", default=None)),
                net_income=_to_decimal(_pick(row, "net_income", default=None)),
                eps=_to_decimal(_pick(row, "eps", default=None)),
                book_value_per_share=_to_decimal(_pick(row, "book_value_per_share", default=None)),
                debt_to_equity=_to_decimal(_pick(row, "debt_to_equity", default=None)),
                current_ratio=_to_decimal(_pick(row, "current_ratio", default=None)),
                free_cash_flow=_to_decimal(_pick(row, "free_cash_flow", default=None)),
                shares_outstanding=_to_decimal(_pick(row, "shares_outstanding", default=None)),
                currency=currency,
                quality=quality,
            )
        )

    return sorted(normalized, key=lambda item: item.period_end_date, reverse=True)


def _pick(
    payload: Mapping[str, object],
    *keys: str,
    default: object = _MISSING,
) -> object | None:
    for key in keys:
        if key in payload:
            return payload[key]
    if default is not _MISSING or len(keys) == 0:
        return default
    joined = ", ".join(keys)
    raise KeyError(f"Missing required key. Tried: {joined}")


def _to_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _to_int(value: object | None) -> int:
    if value is None:
        return 0
    return int(float(str(value)))


def _to_datetime_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    raise TypeError(f"Unsupported datetime value type: {type(value)!r}")


def _normalize_period_type(
    period_type: object | None,
    form: object | None,
    period_end_date: object,
) -> str:
    if isinstance(period_type, str) and period_type:
        normalized = period_type.upper()
        if normalized in {"FY", "Q1", "Q2", "Q3", "Q4", "Q?"}:
            return normalized

    if isinstance(form, str):
        if form == "10-K":
            return "FY"
        if form == "10-Q":
            month = _to_datetime_utc(period_end_date).month
            return {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}.get(month, "Q?")

    return "Q?"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
