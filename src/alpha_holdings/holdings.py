"""Existing holdings awareness — overlap detection with discovered themes."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from datetime import UTC, datetime, timedelta
from typing import Optional, Protocol

import yfinance as yf
from pydantic import BaseModel, Field, field_validator, model_validator

from alpha_holdings.models import InstrumentType

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Holdings model
# ---------------------------------------------------------------------------

class Holding(BaseModel):
    """A single holding in the user's portfolio."""
    ticker: str
    shares: float = Field(default=0, ge=0)
    avg_cost: Optional[float] = Field(default=None, gt=0)
    weight_pct: Optional[float] = Field(default=None, ge=0, le=100)
    currency: Optional[str] = None
    price_as_of: Optional[datetime] = None
    instrument_type: Optional[InstrumentType] = None

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, value: str) -> str:
        ticker = value.strip().upper()
        if not ticker:
            raise ValueError("ticker must not be empty")
        return ticker

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        currency = value.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter ISO code")
        return currency

    @model_validator(mode="after")
    def _validate_input_mode(self):
        if self.weight_pct is None and self.shares <= 0:
            raise ValueError("share-based holding requires positive shares")
        if self.weight_pct is not None and self.shares != 0:
            raise ValueError("holding cannot mix shares and an explicit weight")
        return self


class HoldingsPortfolio(BaseModel):
    """User's existing portfolio loaded from data/holdings.json."""
    holdings: list[Holding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MarketQuote(BaseModel):
    """A dated price in the instrument's trading currency."""

    ticker: str
    price: float = Field(gt=0)
    currency: str
    as_of: datetime
    source: str


class FXQuote(BaseModel):
    """A dated conversion rate from one currency into another."""

    from_currency: str
    to_currency: str
    rate: float = Field(gt=0)
    as_of: datetime
    source: str


class ETFComposition(BaseModel):
    """Dated, potentially incomplete constituent weights for one ETF."""

    ticker: str
    holdings: dict[str, float] = Field(default_factory=dict)
    as_of: datetime
    source: str
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _validate_holdings(self):
        if any(
            not math.isfinite(weight) or weight <= 0
            for weight in self.holdings.values()
        ):
            raise ValueError("ETF holding weights must be finite and positive")
        if sum(self.holdings.values()) > 100:
            raise ValueError("ETF holding weights cannot exceed 100%")
        return self


class ExposureAnalysis(BaseModel):
    """Effective portfolio exposure plus its observable data coverage."""

    exposures: dict[str, float] = Field(default_factory=dict)
    coverage_pct: float = Field(ge=0, le=100)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_total(self):
        if sum(self.exposures.values()) > 100.000001:
            raise ValueError("effective exposure cannot exceed 100%")
        return self


class HoldingsDataProvider(Protocol):
    def get_quote(self, ticker: str, as_of: datetime) -> MarketQuote: ...

    def get_fx_rate(
        self,
        from_currency: str,
        to_currency: str,
        as_of: datetime,
    ) -> FXQuote: ...

    def get_etf_composition(
        self,
        ticker: str,
        as_of: datetime,
    ) -> ETFComposition | None: ...


class YahooHoldingsProvider:
    """Market-data adapter used by holdings analysis."""

    def get_quote(self, ticker: str, as_of: datetime) -> MarketQuote:
        instrument = yf.Ticker(ticker)
        history = instrument.history(
            start=as_of.strftime("%Y-%m-%d"),
            end=(as_of + timedelta(days=1)).strftime("%Y-%m-%d"),
            auto_adjust=False,
        )
        if history is None or history.empty:
            raise ValueError("dated quote is unavailable")
        column = "Adj Close" if "Adj Close" in history.columns else "Close"
        series = history[column].dropna()
        if series.empty:
            raise ValueError("dated quote is unavailable")
        info = instrument.info or {}
        currency = (info.get("currency") or "").strip().upper()
        if len(currency) != 3:
            raise ValueError("quote currency is unavailable")
        observed = series.index[-1]
        quote_time = (
            observed.to_pydatetime()
            if hasattr(observed, "to_pydatetime")
            else observed
        )
        return MarketQuote(
            ticker=ticker,
            price=float(series.iloc[-1]),
            currency=currency,
            as_of=quote_time,
            source="yfinance",
        )

    def get_etf_composition(
        self,
        ticker: str,
        as_of: datetime,
    ) -> ETFComposition | None:
        from alpha_holdings.etfs import parse_provider_holdings

        instrument = yf.Ticker(ticker)
        info = instrument.info or {}
        if (info.get("quoteType") or "").strip().upper() not in {
            "ETF",
            "MUTUALFUND",
        }:
            return None
        try:
            table = instrument.funds_data.top_holdings
            composition = (
                parse_provider_holdings(table)
                if table is not None and not table.empty
                else {}
            )
        except Exception as exc:
            log.debug("ETF composition fetch failed for %s: %s", ticker, exc)
            composition = {}
        if composition:
            return ETFComposition(
                ticker=ticker,
                holdings=composition,
                as_of=as_of,
                source="yfinance",
            )
        fallback = _FALLBACK_COMPOSITIONS.get(ticker.upper())
        if fallback:
            return ETFComposition(
                ticker=ticker,
                holdings=fallback,
                as_of=as_of,
                source="built-in",
                reason="using approximate built-in ETF composition",
            )
        return ETFComposition(
            ticker=ticker,
            as_of=as_of,
            source="unavailable",
            reason="ETF constituent data is unavailable",
        )

    def get_fx_rate(
        self,
        from_currency: str,
        to_currency: str,
        as_of: datetime,
    ) -> FXQuote:
        if from_currency == to_currency:
            return FXQuote(
                from_currency=from_currency,
                to_currency=to_currency,
                rate=1,
                as_of=as_of,
                source="identity",
            )
        quote = self.get_quote(f"{from_currency}{to_currency}=X", as_of)
        return FXQuote(
            from_currency=from_currency,
            to_currency=to_currency,
            rate=quote.price,
            as_of=quote.as_of,
            source=quote.source,
        )


DEFAULT_PROVIDER: HoldingsDataProvider = YahooHoldingsProvider()


# ---------------------------------------------------------------------------
# Disclosed ETF composition fallback
# ---------------------------------------------------------------------------

# Approximate top holdings used only when live provider composition is unavailable.
_FALLBACK_COMPOSITIONS: dict[str, dict[str, float]] = {
    "VT": {"AAPL": 4.2, "MSFT": 3.8, "NVDA": 3.2, "AMZN": 2.5, "GOOGL": 1.5, "META": 1.5, "AVGO": 1.0, "2330.TW": 0.8, "005930.KS": 0.5},
    "VOO": {"AAPL": 7.0, "MSFT": 6.5, "NVDA": 5.5, "AMZN": 4.0, "GOOGL": 2.5, "META": 2.5, "AVGO": 1.7, "JPM": 1.5},
    "SPY": {"AAPL": 7.0, "MSFT": 6.5, "NVDA": 5.5, "AMZN": 4.0, "GOOGL": 2.5, "META": 2.5, "AVGO": 1.7, "JPM": 1.5},
    "IWDA.AS": {"AAPL": 5.0, "MSFT": 4.5, "NVDA": 3.8, "AMZN": 3.0, "GOOGL": 1.8, "META": 1.8, "AVGO": 1.2},
    "VWCE.DE": {"AAPL": 4.2, "MSFT": 3.8, "NVDA": 3.2, "AMZN": 2.5, "GOOGL": 1.5, "META": 1.5, "AVGO": 1.0},
    "SMH": {"NVDA": 20.0, "TSM": 12.0, "AVGO": 8.0, "ASML": 5.0, "TXN": 5.0, "AMD": 4.5, "QCOM": 4.0, "AMAT": 4.0, "MU": 3.5, "INTC": 3.0},
    "SOXX": {"NVDA": 9.0, "AVGO": 8.5, "AMD": 7.5, "QCOM": 5.5, "TXN": 5.0, "MU": 4.5, "AMAT": 4.0, "INTC": 3.5, "MRVL": 3.5, "TSM": 3.0},
    "XLK": {"AAPL": 16.0, "MSFT": 14.0, "NVDA": 13.0, "AVGO": 5.0, "CRM": 2.5, "AMD": 2.0, "ADBE": 2.0, "ORCL": 2.0, "ACN": 2.0},
    "QQQ": {"AAPL": 9.0, "MSFT": 8.0, "NVDA": 7.5, "AMZN": 5.5, "META": 4.5, "AVGO": 4.0, "GOOGL": 3.0, "GOOG": 2.5, "TSLA": 2.5, "COST": 2.5},
    "URA": {"CCJ": 18.0, "NXE": 7.0, "UUUU": 5.5, "DNN": 4.5, "LEU": 4.0, "PDN.AX": 3.5, "DYL.AX": 3.0, "SRUUF": 3.0, "UEC": 3.0, "FCU.TO": 2.5},
    "HACK": {"CRWD": 6.5, "FTNT": 6.0, "PANW": 5.5, "ZS": 4.0, "OKTA": 3.5, "CYBR": 3.5, "CHKP": 3.0, "MNDT": 3.0, "RPD": 2.5, "NET": 2.5},
    "XME": {"NUE": 5.5, "STLD": 5.0, "FCX": 5.0, "AA": 4.5, "CLF": 4.0, "RS": 3.5, "CMC": 3.0, "ATI": 3.0, "MP": 2.5, "CRS": 2.5},
    "XLE": {"XOM": 23.0, "CVX": 17.0, "COP": 5.0, "SLB": 4.5, "EOG": 4.0, "MPC": 4.0, "PXD": 3.5, "PSX": 3.5, "VLO": 3.0, "OXY": 2.5},
    "ITA": {"RTX": 18.0, "LMT": 6.0, "GE": 5.0, "BA": 5.0, "NOC": 4.5, "GD": 4.0, "LHX": 4.0, "TDG": 3.5, "HII": 3.0, "TXT": 2.5},
}

# ---------------------------------------------------------------------------
# Load & analyze
# ---------------------------------------------------------------------------

def load_holdings(path: str | Path) -> HoldingsPortfolio:
    """Load holdings from a JSON file.

    Accepts two formats:
    - Holdings list: [{"ticker": "...", "shares": N, "avg_cost": X}, ...]
    - Allocation file: {"entries": [...], ...} (auto-detected from data/allocations/)
    """
    p = Path(path)
    if not p.exists():
        log.warning("Holdings file not found: %s", p)
        return HoldingsPortfolio()

    data = json.loads(p.read_text())
    if isinstance(data, list):
        return HoldingsPortfolio(holdings=[Holding(**h) for h in data])
    # Auto-detect allocation format (current positions or legacy grouped entries).
    if isinstance(data, dict) and ("positions" in data or "entries" in data):
        return _allocation_to_portfolio(data)
    return HoldingsPortfolio(**data)


def _allocation_to_portfolio(alloc_data: dict) -> HoldingsPortfolio:
    """Convert a PortfolioAllocation dict into a HoldingsPortfolio.

    Extracts tickers from allocation entries and uses entry_prices as avg_cost.
    """
    positions = alloc_data.get("positions") or []
    if positions:
        return HoldingsPortfolio(
            holdings=[
                Holding(
                    ticker=position["ticker"],
                    weight_pct=position.get("weight_pct"),
                    currency=position.get("currency"),
                    avg_cost=position.get("entry_price"),
                    price_as_of=position.get("price_timestamp"),
                    instrument_type=position.get("instrument_type"),
                )
                for position in positions
                if position.get("instrument_type") != "cash"
            ]
        )

    holdings: list[Holding] = []
    warnings: list[str] = []
    for entry in alloc_data.get("entries", []):
        entry_prices = entry.get("entry_prices", {})
        tickers = [
            ticker.strip().upper()
            for ticker in entry.get(
                "tickers",
                entry.get("vehicle", "").split(","),
            )
            if ticker.strip()
        ]
        if not tickers:
            continue
        allocation_pct = float(entry.get("pct_allocation", 0))
        ticker_weight = allocation_pct / len(tickers)
        if len(tickers) > 1:
            warnings.append(
                f"Legacy allocation entry '{entry.get('theme', '?')}' has no "
                f"per-ticker weights; its {allocation_pct:.1f}% was divided "
                f"equally across {len(tickers)} tickers."
            )
        vehicle_type = entry.get("vehicle_type", "")
        instrument_type = (
            InstrumentType.ETF
            if vehicle_type == "etf"
            else InstrumentType.STOCK
        )
        for ticker in tickers:
            holdings.append(Holding(
                ticker=ticker,
                weight_pct=ticker_weight,
                avg_cost=entry_prices.get(ticker),
                instrument_type=instrument_type,
            ))
    return HoldingsPortfolio(holdings=holdings, warnings=warnings)


def get_existing_exposure(
    holdings: HoldingsPortfolio,
    *,
    base_currency: str = "USD",
    as_of: datetime | None = None,
    provider: HoldingsDataProvider | None = None,
) -> ExposureAnalysis:
    """Compute dated, base-currency exposure with explicit coverage warnings."""
    if not holdings.holdings:
        return ExposureAnalysis(coverage_pct=100)

    data_provider = provider or DEFAULT_PROVIDER
    analysis_time = as_of or datetime.now(UTC)
    warnings = list(holdings.warnings)
    etf_tickers = {
        holding.ticker
        for holding in holdings.holdings
        if holding.instrument_type is InstrumentType.ETF
    }
    explicit = [holding for holding in holdings.holdings if holding.weight_pct is not None]
    position_weights: dict[str, float] = {}

    if explicit:
        if len(explicit) != len(holdings.holdings):
            return ExposureAnalysis(
                coverage_pct=0,
                warnings=["Cannot mix explicit weights with share-based holdings."],
            )
        for holding in explicit:
            position_weights[holding.ticker] = (
                position_weights.get(holding.ticker, 0) + (holding.weight_pct or 0)
            )
        if sum(position_weights.values()) > 100:
            return ExposureAnalysis(
                coverage_pct=0,
                warnings=["Explicit holding weights exceed 100%."],
            )
    else:
        lots: dict[str, list[Holding]] = {}
        for holding in holdings.holdings:
            lots.setdefault(holding.ticker, []).append(holding)
        values: dict[str, float] = {}
        for ticker, ticker_lots in lots.items():
            shares = sum(holding.shares for holding in ticker_lots)
            if shares <= 0:
                warnings.append(f"{ticker}: position has no positive share quantity.")
                continue
            try:
                quote = data_provider.get_quote(ticker, analysis_time)
                fx_rate = 1.0
                if quote.currency != base_currency.upper():
                    fx_rate = data_provider.get_fx_rate(
                        quote.currency,
                        base_currency.upper(),
                        analysis_time,
                    ).rate
            except Exception as exc:
                warnings.append(f"{ticker}: quote or FX unavailable ({exc}).")
                continue
            values[ticker] = shares * quote.price * fx_rate
        total_value = sum(values.values())
        if total_value <= 0:
            return ExposureAnalysis(coverage_pct=0, warnings=warnings)
        position_weights = {
            ticker: value / total_value * 100
            for ticker, value in values.items()
        }

    position_coverage = (
        min(round(sum(position_weights.values()), 4), 100)
        if explicit
        else round(len(position_weights) / len(lots) * 100, 4)
    )
    exposure: dict[str, float] = {}
    decomposed_coverage = 0.0
    for ticker, weight_pct in position_weights.items():
        composition_failed = False
        try:
            composition = data_provider.get_etf_composition(ticker, analysis_time)
        except Exception as exc:
            warnings.append(f"{ticker}: ETF composition unavailable ({exc}).")
            composition = None
            composition_failed = True
        if composition is not None:
            for constituent, constituent_weight in composition.holdings.items():
                exposure[constituent] = exposure.get(constituent, 0) + (
                    weight_pct * constituent_weight / 100
                )
            reported_weight = sum(composition.holdings.values())
            decomposed_coverage += weight_pct * reported_weight / 100
            residual_weight = max(100 - reported_weight, 0)
            if residual_weight:
                residual_ticker = f"{ticker}:UNKNOWN/OTHER"
                exposure[residual_ticker] = exposure.get(residual_ticker, 0) + (
                    weight_pct * residual_weight / 100
                )
                if not composition.reason:
                    warnings.append(
                        f"{ticker}: ETF decomposition covers "
                        f"{reported_weight:.1f}%; residual retained."
                    )
            if composition.reason:
                warnings.append(f"{ticker}: {composition.reason}.")
        elif ticker in etf_tickers:
            exposure[f"{ticker}:UNKNOWN/OTHER"] = weight_pct
            if not composition_failed:
                warnings.append(f"{ticker}: ETF constituent data is unavailable.")
        else:
            exposure[ticker] = exposure.get(ticker, 0) + weight_pct
            decomposed_coverage += weight_pct

    rounded = {ticker: round(weight, 4) for ticker, weight in exposure.items()}
    rounding_excess = round(sum(rounded.values()) - 100, 4)
    if rounding_excess > 0 and rounded:
        largest = max(rounded, key=rounded.get)
        rounded[largest] = round(rounded[largest] - rounding_excess, 4)
    coverage = min(position_coverage, round(decomposed_coverage, 4))
    return ExposureAnalysis(
        exposures=rounded,
        coverage_pct=coverage,
        warnings=warnings,
    )


def analyze_overlap(
    existing_exposure: ExposureAnalysis,
    proposed_exposure: ExposureAnalysis,
) -> list[dict]:
    """Find named securities shared by existing and proposed effective exposure.

    Returns list of overlaps with existing weight and new weight.
    """
    overlaps = []
    shared = sorted(
        set(existing_exposure.exposures) & set(proposed_exposure.exposures)
    )
    for ticker in shared:
        if ticker.endswith(":UNKNOWN/OTHER"):
            continue
        existing_pct = existing_exposure.exposures[ticker]
        new_pct = proposed_exposure.exposures[ticker]
        if existing_pct > 0 and new_pct > 0:
            overlaps.append({
                "ticker": ticker,
                "existing_pct": round(existing_pct, 2),
                "new_pct": round(new_pct, 2),
                "combined_pct": round(existing_pct + new_pct, 2),
            })
    return overlaps
