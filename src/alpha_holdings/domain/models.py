"""Core domain models and data contracts for alpha-holdings.

All models use Pydantic v2 for validation and serialization. Each model includes
explicit metadata for data provenance (source, as-of date, publish date, currency)
and quality flags to enable vendor comparisons and upgrade path validation.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, validator


class DataQuality(BaseModel):
    """Metadata about data quality and provenance."""

    source: str = Field(..., description="Data source identifier (e.g., 'yahoo', 'edgar', 'bloomberg')")
    as_of_date: datetime = Field(..., description="Date/time at which the data is current")
    publish_date: Optional[datetime] = Field(None, description="Date the data was published by the source")
    currency: str = Field(default="USD", description="Currency code (ISO 4217)")
    data_flags: list[str] = Field(default_factory=list, description="Quality flags (e.g., 'estimated', 'preliminary', 'adjusted')")
    notes: Optional[str] = Field(None, description="Free-form notes about data quality or gaps")


class Security(BaseModel):
    """Canonical representation of a security (stock, ETF, bond, etc.)."""

    internal_id: str = Field(..., description="Internal unique identifier managed by alpha-holdings")
    ticker: str = Field(..., description="Primary ticker symbol")
    isin: Optional[str] = Field(None, description="International Securities Identification Number")
    cusip: Optional[str] = Field(None, description="Committee on Uniform Security Identification Procedures")
    name: str = Field(..., description="Security name/description")
    security_type: str = Field(..., description="Type: equity, etf, bond, crypto, etc.")
    exchange: str = Field(..., description="Primary exchange (e.g., 'NASDAQ', 'NYSE')")
    currency: str = Field(default="USD", description="Denominated currency (ISO 4217)")
    country: str = Field(..., description="Country of domicile (ISO 3166-1 alpha-2)")
    sector: Optional[str] = Field(None, description="GICS sector classification")
    industry: Optional[str] = Field(None, description="GICS industry classification")
    quality: DataQuality = Field(..., description="Data provenance and quality metadata")

    class Config:
        arbitrary_types_allowed = True


class IdentifierMap(BaseModel):
    """Maps a security across identifiers and vendor sources."""

    internal_id: str = Field(..., description="Internal unique identifier")
    ticker_map: dict[str, str] = Field(..., description="Mapping of source to ticker (e.g., {'yahoo': 'AAPL', 'bloomberg': 'AAPL US Equity'})")
    isin: Optional[str] = Field(None, description="ISIN if available")
    cusip: Optional[str] = Field(None, description="CUSIP if available")
    deprecated_identifiers: list[str] = Field(default_factory=list, description="Historical or vendor-specific identifiers")
    quality: DataQuality = Field(..., description="Data provenance metadata")

    class Config:
        arbitrary_types_allowed = True


class PriceBar(BaseModel):
    """OHLCV price bar for a security on a given date."""

    security_id: str = Field(..., description="Internal security identifier")
    date: datetime = Field(..., description="Trading date (date only, no intraday)")
    open: Decimal = Field(..., description="Opening price")
    high: Decimal = Field(..., description="High price")
    low: Decimal = Field(..., description="Low price")
    close: Decimal = Field(..., description="Closing price (or last traded)")
    adjusted_close: Optional[Decimal] = Field(None, description="Adjusted close (after splits/dividends)")
    volume: int = Field(..., description="Trading volume in shares")
    dividend: Decimal = Field(default=Decimal(0), description="Dividend per share for the day")
    split_factor: Decimal = Field(default=Decimal(1), description="Split factor for the day (e.g., 2.0 for 2:1 split)")
    quality: DataQuality = Field(..., description="Data provenance and adjustment metadata")

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {Decimal: str}


class CorporateAction(BaseModel):
    """Dividend, split, or other corporate action."""

    security_id: str = Field(..., description="Internal security identifier")
    action_date: datetime = Field(..., description="Effective date of the action")
    action_type: str = Field(..., description="Type: dividend, split, merger, etc.")
    value: Decimal = Field(..., description="Dividend per share or split factor")
    currency: str = Field(default="USD", description="Currency for dividends (ISO 4217)")
    description: Optional[str] = Field(None, description="Details of the action")
    quality: DataQuality = Field(..., description="Data provenance metadata")

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {Decimal: str}


class FundamentalSnapshot(BaseModel):
    """Quarterly or annual fundamental data snapshot for a security."""

    security_id: str = Field(..., description="Internal security identifier")
    period_end_date: datetime = Field(..., description="End of the reporting period")
    period_type: str = Field(..., description="Period type: Q1, Q2, Q3, Q4, FY")
    revenue: Optional[Decimal] = Field(None, description="Total revenue")
    operating_income: Optional[Decimal] = Field(None, description="Operating income")
    net_income: Optional[Decimal] = Field(None, description="Net income")
    eps: Optional[Decimal] = Field(None, description="Earnings per share (diluted preferred)")
    book_value_per_share: Optional[Decimal] = Field(None, description="Tangible book value per share")
    debt_to_equity: Optional[Decimal] = Field(None, description="Total debt / total equity ratio")
    current_ratio: Optional[Decimal] = Field(None, description="Current assets / current liabilities")
    free_cash_flow: Optional[Decimal] = Field(None, description="Free cash flow for the period")
    shares_outstanding: Optional[Decimal] = Field(None, description="Diluted shares outstanding")
    custom_factors: dict[str, Decimal] = Field(default_factory=dict, description="Additional factor data")
    currency: str = Field(default="USD", description="Currency of numeric fields (ISO 4217)")
    quality: DataQuality = Field(..., description="Data provenance and source metadata")

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {Decimal: str}


class BenchmarkConstituent(BaseModel):
    """Member of a benchmark index at a given rebalance date."""

    benchmark_id: str = Field(..., description="Benchmark identifier (e.g., 'SPY', 'MSCI_USA')")
    security_id: str = Field(..., description="Internal security identifier")
    effective_date: datetime = Field(..., description="Date this constituent became effective")
    weight: Decimal = Field(..., description="Weight in benchmark (0.0 to 1.0)")
    shares_held: Optional[Decimal] = Field(None, description="Shares included in benchmark calculation")
    quality: DataQuality = Field(..., description="Data provenance metadata")

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {Decimal: str}


class Holding(BaseModel):
    """Current or historical holding in a portfolio."""

    portfolio_id: str = Field(..., description="Unique portfolio identifier")
    security_id: str = Field(..., description="Internal security identifier")
    as_of_date: datetime = Field(..., description="Date as of which the holding is recorded")
    shares: Decimal = Field(..., description="Number of shares held")
    book_cost_per_share: Optional[Decimal] = Field(None, description="Average cost basis per share")
    current_price: Decimal = Field(..., description="Current market price per share")
    market_value: Decimal = Field(..., description="Current market value = shares * current_price")
    target_weight: Optional[Decimal] = Field(None, description="Target weight in portfolio (0.0 to 1.0)")
    quality: DataQuality = Field(..., description="Data provenance metadata")

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {Decimal: str}


class TargetWeight(BaseModel):
    """Target portfolio weight for a security from the construction engine."""

    portfolio_id: str = Field(..., description="Unique portfolio identifier")
    security_id: str = Field(..., description="Internal security identifier")
    target_date: datetime = Field(..., description="Date as of which this target is effective")
    target_weight: Decimal = Field(..., description="Target weight in portfolio (0.0 to 1.0)")
    min_weight: Optional[Decimal] = Field(None, description="Minimum allowed weight")
    max_weight: Optional[Decimal] = Field(None, description="Maximum allowed weight")
    reason: Optional[str] = Field(None, description="Justification for the weight (e.g., 'score_rank_5')")
    quality: DataQuality = Field(..., description="Data provenance metadata")

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {Decimal: str}


class TradeProposal(BaseModel):
    """Proposed trade from rebalance engine."""

    portfolio_id: str = Field(..., description="Unique portfolio identifier")
    trade_date: datetime = Field(..., description="Proposed trade execution date")
    security_id: str = Field(..., description="Internal security identifier")
    side: str = Field(..., description="Trade side: buy or sell")
    shares: Decimal = Field(..., description="Number of shares to trade")
    price_estimate: Decimal = Field(..., description="Estimated execution price")
    estimated_value: Decimal = Field(..., description="Estimated trade value = shares * price_estimate")
    reason: Optional[str] = Field(None, description="Justification (e.g., 'rebalance_to_target')")
    priority: int = Field(default=0, description="Execution priority (0=critical, higher=lower priority)")
    quality: DataQuality = Field(..., description="Data provenance metadata")

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {Decimal: str}


class PerformanceSnapshot(BaseModel):
    """Portfolio performance metrics at a point in time."""

    portfolio_id: str = Field(..., description="Unique portfolio identifier")
    as_of_date: datetime = Field(..., description="Date of performance measurement")
    period_start_date: datetime = Field(..., description="Start of the measurement period")
    total_return: Decimal = Field(..., description="Total return over period (decimal, e.g., 0.05 for 5%)")
    annualized_return: Optional[Decimal] = Field(None, description="Annualized return if period > 1 year")
    volatility: Decimal = Field(..., description="Annualized volatility (standard deviation)")
    sharpe_ratio: Optional[Decimal] = Field(None, description="Excess return / volatility")
    max_drawdown: Decimal = Field(..., description="Maximum peak-to-trough drawdown over period")
    benchmark_id: Optional[str] = Field(None, description="Benchmark used for attribution")
    benchmark_return: Optional[Decimal] = Field(None, description="Benchmark total return over period")
    excess_return: Optional[Decimal] = Field(None, description="Portfolio return - benchmark return")
    total_value: Decimal = Field(..., description="Current portfolio market value")
    time_weighted_return: Optional[Decimal] = Field(None, description="TWR if cash flows occurred")
    money_weighted_return: Optional[Decimal] = Field(None, description="MWR if cash flows occurred")
    currency: str = Field(default="USD", description="Currency of numeric fields (ISO 4217)")
    quality: DataQuality = Field(..., description="Data provenance and calculation metadata")

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {Decimal: str}
