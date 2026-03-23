"""Yahoo Finance price and reference-data adapter.

Uses ``yfinance`` (free, public). Supplies:
- Daily OHLCV price history with split/dividend adjustments.
- Corporate actions (dividends and splits).
- Basic reference metadata (Security).

Known limitations:
- No point-in-time fundamental data; reported figures may include look-ahead.
- No official benchmark constituent history.
- Ticker symbols are Yahoo-native and may differ from exchange-native tickers
  for non-US securities.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import yfinance as yf

from alpha_holdings.data.providers.base import (
    PriceProvider,
    ProviderCapability,
    ReferenceDataProvider,
)
from alpha_holdings.domain.models import (
    CorporateAction,
    DataQuality,
    PriceBar,
    Security,
)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


# Country → Yahoo exchange suffix for non-US markets.
# Only included where Yahoo uses the same alphabetical ticker with an exchange
# suffix. Japan is excluded because Yahoo uses numeric codes (e.g. 6758.T) for
# Tokyo-listed stocks; Japanese names in our universe are US-listed ADRs.
_YAHOO_EXCHANGE_SUFFIX: dict[str, str] = {
    "CH": ".SW",
    "DE": ".DE",
    "GB": ".L",
    "CA": ".TO",
    "AU": ".AX",
    "FR": ".PA",
    "HK": ".HK",
}


class YahooPriceProvider(PriceProvider, ReferenceDataProvider):
    """Adapter that wraps yfinance for prices and basic reference data."""

    capabilities = frozenset(
        {
            ProviderCapability.PRICES,
            ProviderCapability.CORPORATE_ACTIONS,
            ProviderCapability.REFERENCE_DATA,
        }
    )

    @property
    def source_id(self) -> str:
        return "yahoo"

    def resolve_ticker(self, canonical: str, *, country: str = "") -> str:
        """Map canonical symbol to Yahoo-native ticker.

        Handles two known divergences:
        1. Share-class dots → hyphens (e.g. ``BRK.B`` → ``BRK-B``).
        2. Non-US exchanges need a suffix (e.g. ``NOVN`` + CH → ``NOVN.SW``).
        """
        country = country.upper().strip()
        # Yahoo uses hyphens where canonical tickers use dots for share classes.
        # A dot followed by 1-2 uppercase letters at the end is a share class.
        if "." in canonical:
            parts = canonical.rsplit(".", 1)
            if len(parts[1]) <= 2 and parts[1].isalpha():
                canonical = f"{parts[0]}-{parts[1]}"

        suffix = _YAHOO_EXCHANGE_SUFFIX.get(country, "")
        if suffix and not canonical.endswith(suffix):
            return f"{canonical}{suffix}"
        return canonical

    # ------------------------------------------------------------------
    # PriceProvider
    # ------------------------------------------------------------------

    def get_prices(
        self,
        ticker: str,
        start: date,
        end: date,
        *,
        adjusted: bool = True,
    ) -> list[PriceBar]:
        """Fetch daily OHLCV from Yahoo Finance.

        When ``adjusted=True``, the ``adjusted_close`` field is populated from
        yfinance's ``Close`` column (which yfinance returns as adjusted when
        ``auto_adjust=True``). Raw open/high/low/close values are also adjusted
        in that mode; the ``data_flags`` field records ``'auto_adjusted'``.

        When ``adjusted=False``, raw prices are returned and flags contain
        ``'unadjusted'``.
        """
        tkr = yf.Ticker(ticker)
        raw = tkr.history(
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=adjusted,
            actions=False,
        )

        flags = ["auto_adjusted"] if adjusted else ["unadjusted"]
        quality_base = DataQuality(
            source=self.source_id,
            as_of_date=_utc_now(),
            data_flags=flags,
        )

        bars: list[PriceBar] = []
        for ts, row in raw.iterrows():
            bar_date = ts.to_pydatetime().replace(tzinfo=UTC)
            bars.append(
                PriceBar(
                    security_id=ticker,
                    date=bar_date,
                    open=Decimal(str(row["Open"])),
                    high=Decimal(str(row["High"])),
                    low=Decimal(str(row["Low"])),
                    close=Decimal(str(row["Close"])),
                    adjusted_close=Decimal(str(row["Close"])) if adjusted else None,
                    volume=int(row["Volume"]),
                    quality=quality_base,
                )
            )
        return bars

    def get_corporate_actions(
        self,
        ticker: str,
        start: date,
        end: date,
    ) -> list[CorporateAction]:
        """Return dividends and splits from yfinance."""
        tkr = yf.Ticker(ticker)
        raw = tkr.history(
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=False,
            actions=True,
        )

        quality = DataQuality(source=self.source_id, as_of_date=_utc_now())
        actions: list[CorporateAction] = []

        if "Dividends" in raw.columns:
            for ts, row in raw[raw["Dividends"] != 0].iterrows():
                actions.append(
                    CorporateAction(
                        security_id=ticker,
                        action_date=ts.to_pydatetime().replace(tzinfo=UTC),
                        action_type="dividend",
                        value=Decimal(str(row["Dividends"])),
                        quality=quality,
                    )
                )

        if "Stock Splits" in raw.columns:
            for ts, row in raw[raw["Stock Splits"] != 0].iterrows():
                actions.append(
                    CorporateAction(
                        security_id=ticker,
                        action_date=ts.to_pydatetime().replace(tzinfo=UTC),
                        action_type="split",
                        value=Decimal(str(row["Stock Splits"])),
                        quality=quality,
                    )
                )

        return actions

    # ------------------------------------------------------------------
    # ReferenceDataProvider
    # ------------------------------------------------------------------

    def get_security(self, ticker: str) -> Security:
        """Return a ``Security`` record populated from yfinance ``info``."""
        tkr = yf.Ticker(ticker)
        info = tkr.info or {}

        quality = DataQuality(
            source=self.source_id,
            as_of_date=_utc_now(),
            data_flags=["yahoo_info"],
            notes="Field availability depends on yfinance version and Yahoo API response.",
        )

        return Security(
            internal_id=ticker,
            ticker=ticker,
            isin=info.get("isin"),
            name=info.get("longName") or info.get("shortName") or ticker,
            security_type=_map_quote_type(info.get("quoteType", "").lower()),
            exchange=info.get("exchange") or "UNKNOWN",
            currency=info.get("currency") or "USD",
            country=info.get("country") or "US",
            sector=info.get("sector"),
            industry=info.get("industry"),
            quality=quality,
        )


def _map_quote_type(yf_type: str) -> str:
    mapping = {
        "equity": "equity",
        "etf": "etf",
        "mutualfund": "mutual_fund",
        "bond": "bond",
        "cryptocurrency": "crypto",
        "currency": "fx",
        "index": "index",
        "future": "future",
        "option": "option",
    }
    return mapping.get(yf_type, "unknown")
