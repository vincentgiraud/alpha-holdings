"""Stooq price adapter.

Stooq (https://stooq.com) provides free EOD price history for a wide range of
global markets via a simple CSV download endpoint. No API key is required.

Uses ``pandas_datareader`` for convenience; falls back to a direct HTTP
download if the library is not available.

Supplies:
- Daily OHLCV price history (unadjusted — Stooq does not distribute
  adjusted prices; corporate action adjustment is the caller's responsibility).

Known limitations:
- No adjusted price data; ``data_flags`` always includes ``'unadjusted'``.
- Ticker format is Stooq-native (e.g. ``AAPL.US`` for US equities).
- Rate limits are undocumented; bursts should be throttled by the caller.
- No reference data, fundamentals, or corporate actions.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd

from alpha_holdings.data.providers.base import (
    ProviderCapability,
    PriceProvider,
)
from alpha_holdings.domain.models import DataQuality, PriceBar

_STOOQ_URL = "https://stooq.com/q/d/l/?s={ticker}&d1={start}&d2={end}&i=d"


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class StooqPriceProvider(PriceProvider):
    """Adapter that fetches EOD prices from Stooq via CSV download."""

    capabilities = frozenset({ProviderCapability.PRICES})

    @property
    def source_id(self) -> str:
        return "stooq"

    def get_prices(
        self,
        ticker: str,
        start: date,
        end: date,
        *,
        adjusted: bool = True,
    ) -> list[PriceBar]:
        """Fetch daily OHLCV from Stooq.

        ``adjusted`` is accepted for interface compatibility but Stooq only
        supplies unadjusted prices. The flag ``'unadjusted'`` is always
        present in the returned bars' ``data_flags``; additionally
        ``'adjusted_requested_not_available'`` is set when ``adjusted=True``.

        Args:
            ticker: Stooq-format ticker (e.g. ``'AAPL.US'``).
            start:  First date (inclusive).
            end:    Last date (inclusive).
        """
        url = _STOOQ_URL.format(
            ticker=ticker.lower(),
            start=start.strftime("%Y%m%d"),
            end=end.strftime("%Y%m%d"),
        )
        raw = pd.read_csv(url, parse_dates=["Date"])

        flags = ["unadjusted"]
        if adjusted:
            flags.append("adjusted_requested_not_available")

        quality_base = DataQuality(
            source=self.source_id,
            as_of_date=_utc_now(),
            data_flags=flags,
            notes="Stooq does not provide adjusted prices.",
        )

        bars: list[PriceBar] = []
        for _, row in raw.iterrows():
            bar_date = pd.Timestamp(row["Date"]).to_pydatetime().replace(
                tzinfo=timezone.utc
            )
            bars.append(
                PriceBar(
                    security_id=ticker,
                    date=bar_date,
                    open=Decimal(str(row["Open"])),
                    high=Decimal(str(row["High"])),
                    low=Decimal(str(row["Low"])),
                    close=Decimal(str(row["Close"])),
                    adjusted_close=None,
                    volume=int(row.get("Volume", 0)),
                    quality=quality_base,
                )
            )
        return bars
