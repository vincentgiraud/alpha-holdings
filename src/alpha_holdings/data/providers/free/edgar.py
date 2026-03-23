"""SEC EDGAR fundamentals adapter.

Pulls quarterly and annual financial data from the SEC EDGAR company facts API,
which is free and does not require an API key. The endpoint is:

    https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json

Supplies:
- Fundamental snapshots derived from XBRL filings (10-K, 10-Q).

Known limitations:
- Covers US public companies only (those required to file with the SEC).
- Ticker → CIK mapping is resolved via the EDGAR company search endpoint;
  the mapping is cached in-memory for the lifetime of the adapter instance.
- XBRL concepts vary by company: not every income-statement or balance-sheet
  field is available for every filer. Missing fields are returned as ``None``.
- Data is as-filed; there is no point-in-time protection. Restated figures
  will silently replace the original. ``data_flags`` includes
  ``'no_point_in_time'`` to make this explicit.
- Rate limit: SEC fair-use policy requests ≤10 requests/second. Build
  appropriate throttling into batch workflows.
"""

from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache

import requests

from alpha_holdings.data.providers.base import (
    FundamentalsProvider,
    ProviderCapability,
)
from alpha_holdings.domain.models import DataQuality, FundamentalSnapshot

_EDGAR_TICKER_URL = "https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2000-01-01&enddt=2099-01-01&forms=10-K"
_EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_EDGAR_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

_HEADERS = {
    "User-Agent": "alpha-holdings research (contact@example.com)",
    "Accept-Encoding": "gzip",
}

# Map XBRL concept names to FundamentalSnapshot fields.
_CONCEPT_MAP: dict[str, str] = {
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "OperatingIncomeLoss": "operating_income",
    "NetIncomeLoss": "net_income",
    "EarningsPerShareDiluted": "eps",
    "BookValuePerShare": "book_value_per_share",
    "DebtToEquityRatio": "debt_to_equity",
    "NetCashProvidedByUsedInOperatingActivities": "free_cash_flow",
    "CommonStockSharesOutstanding": "shares_outstanding",
}


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class EdgarFundamentalsProvider(FundamentalsProvider):
    """Adapter that fetches fundamental data from SEC EDGAR XBRL filings."""

    capabilities = frozenset({ProviderCapability.FUNDAMENTALS})

    @property
    def source_id(self) -> str:
        return "edgar"

    def get_fundamentals(
        self,
        ticker: str,
        *,
        limit: int = 8,
    ) -> list[FundamentalSnapshot]:
        """Return up to *limit* most-recent fundamental snapshots for *ticker*.

        Snapshots are derived from 10-K (annual) and 10-Q (quarterly) filings.
        Each snapshot carries ``data_flags=['no_point_in_time']`` to indicate
        that restated figures may be present.
        """
        cik = self._resolve_cik(ticker)
        facts = self._fetch_facts(cik)
        snapshots = _build_snapshots(ticker, facts, limit)
        return snapshots

    @lru_cache(maxsize=512)  # noqa: B019
    def _resolve_cik(self, ticker: str) -> str:
        """Return zero-padded 10-digit CIK for *ticker*."""
        resp = requests.get(_EDGAR_TICKERS_URL, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == upper:
                return str(entry["cik_str"]).zfill(10)
        raise ValueError(f"Ticker '{ticker}' not found in EDGAR company tickers.")

    def _fetch_facts(self, cik: str) -> dict:
        url = _EDGAR_FACTS_URL.format(cik=cik)
        resp = requests.get(url, headers=_HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.json()


def _build_snapshots(
    ticker: str,
    facts: dict,
    limit: int,
) -> list[FundamentalSnapshot]:
    """Parse EDGAR XBRL facts JSON into ``FundamentalSnapshot`` objects."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    # Collect period-keyed data from each concept we care about.
    # period_key → {field: value}
    period_data: dict[str, dict] = {}

    for concept, field in _CONCEPT_MAP.items():
        concept_data = us_gaap.get(concept, {})
        units = concept_data.get("units", {})
        # Prefer USD-denominated values; fall back to shares/pure for per-share items.
        values = units.get("USD") or units.get("shares") or units.get("pure") or []
        for entry in values:
            form = entry.get("form", "")
            if form not in {"10-K", "10-Q"}:
                continue
            end = entry.get("end", "")
            if not end:
                continue
            period_data.setdefault(end, {})
            # Only record the first (most recently filed) value for each period.
            if field not in period_data[end]:
                period_data[end][field] = Decimal(str(entry["val"]))
            # Capture form type to determine period_type later.
            period_data[end].setdefault("_form", form)

    quality = DataQuality(
        source="edgar",
        as_of_date=_utc_now(),
        data_flags=["no_point_in_time"],
        notes="Figures are as-filed; restated values may replace originals.",
    )

    snapshots: list[FundamentalSnapshot] = []
    for end_str in sorted(period_data.keys(), reverse=True)[:limit]:
        row = period_data[end_str]
        form = row.get("_form", "10-K")
        period_type = _infer_period_type(end_str, form)

        snapshots.append(
            FundamentalSnapshot(
                security_id=ticker,
                period_end_date=datetime.fromisoformat(end_str).replace(tzinfo=UTC),
                period_type=period_type,
                revenue=row.get("revenue"),
                operating_income=row.get("operating_income"),
                net_income=row.get("net_income"),
                eps=row.get("eps"),
                book_value_per_share=row.get("book_value_per_share"),
                debt_to_equity=row.get("debt_to_equity"),
                free_cash_flow=row.get("free_cash_flow"),
                shares_outstanding=row.get("shares_outstanding"),
                quality=quality,
            )
        )
    return snapshots


def _infer_period_type(end_date_str: str, form: str) -> str:
    if form == "10-K":
        return "FY"
    month = int(end_date_str[5:7])
    quarter_map = {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}
    return quarter_map.get(month, "Q?")
