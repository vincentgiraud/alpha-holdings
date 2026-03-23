"""Core domain models and data contracts for alpha-holdings.

All models use Pydantic v2 for validation and serialization. Each model includes
explicit metadata for data provenance (source, as-of date, publish date, currency)
and quality flags to enable vendor comparisons and upgrade path validation.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class DataQuality(BaseModel):
    """Metadata about data quality and provenance."""

    source: str = Field(
        ..., description="Data source identifier (e.g., 'yahoo', 'edgar', 'bloomberg')"
    )
    as_of_date: datetime = Field(..., description="Date/time at which the data is current")
    publish_date: datetime | None = Field(
        None, description="Date the data was published by the source"
    )
    currency: str = Field(default="USD", description="Currency code (ISO 4217)")
    data_flags: list[str] = Field(
        default_factory=list,
        description="Quality flags (e.g., 'estimated', 'preliminary', 'adjusted')",
    )
    notes: str | None = Field(None, description="Free-form notes about data quality or gaps")


class Security(BaseModel):
    """Canonical representation of a security (stock, ETF, bond, etc.)."""

    internal_id: str = Field(
        ..., description="Internal unique identifier managed by alpha-holdings"
    )
    ticker: str = Field(..., description="Primary ticker symbol")
    isin: str | None = Field(None, description="International Securities Identification Number")
    cusip: str | None = Field(
        None, description="Committee on Uniform Security Identification Procedures"
    )
    name: str = Field(..., description="Security name/description")
    security_type: str = Field(..., description="Type: equity, etf, bond, crypto, etc.")
    exchange: str = Field(..., description="Primary exchange (e.g., 'NASDAQ', 'NYSE')")
    currency: str = Field(default="USD", description="Denominated currency (ISO 4217)")
    country: str = Field(..., description="Country of domicile (ISO 3166-1 alpha-2)")
    sector: str | None = Field(None, description="GICS sector classification")
    industry: str | None = Field(None, description="GICS industry classification")
    quality: DataQuality = Field(..., description="Data provenance and quality metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class IdentifierMap(BaseModel):
    """Maps a security across identifiers and vendor sources."""

    internal_id: str = Field(..., description="Internal unique identifier")
    ticker_map: dict[str, str] = Field(
        ...,
        description="Mapping of source to ticker (e.g., {'yahoo': 'AAPL', 'bloomberg': 'AAPL US Equity'})",
    )
    isin: str | None = Field(None, description="ISIN if available")
    cusip: str | None = Field(None, description="CUSIP if available")
    deprecated_identifiers: list[str] = Field(
        default_factory=list, description="Historical or vendor-specific identifiers"
    )
    quality: DataQuality = Field(..., description="Data provenance metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class PriceBar(BaseModel):
    """OHLCV price bar for a security on a given date."""

    security_id: str = Field(..., description="Internal security identifier")
    date: datetime = Field(..., description="Trading date (date only, no intraday)")
    open: Decimal = Field(..., description="Opening price")
    high: Decimal = Field(..., description="High price")
    low: Decimal = Field(..., description="Low price")
    close: Decimal = Field(..., description="Closing price (or last traded)")
    adjusted_close: Decimal | None = Field(
        None, description="Adjusted close (after splits/dividends)"
    )
    volume: int = Field(..., description="Trading volume in shares")
    dividend: Decimal = Field(default=Decimal(0), description="Dividend per share for the day")
    split_factor: Decimal = Field(
        default=Decimal(1), description="Split factor for the day (e.g., 2.0 for 2:1 split)"
    )
    quality: DataQuality = Field(..., description="Data provenance and adjustment metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("open", "high", "low", "close", "adjusted_close", "dividend", "split_factor")
    def serialize_decimal_fields(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class CorporateAction(BaseModel):
    """Dividend, split, or other corporate action."""

    security_id: str = Field(..., description="Internal security identifier")
    action_date: datetime = Field(..., description="Effective date of the action")
    action_type: str = Field(..., description="Type: dividend, split, merger, etc.")
    value: Decimal = Field(..., description="Dividend per share or split factor")
    currency: str = Field(default="USD", description="Currency for dividends (ISO 4217)")
    description: str | None = Field(None, description="Details of the action")
    quality: DataQuality = Field(..., description="Data provenance metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("value")
    def serialize_value(self, value: Decimal) -> str:
        return str(value)


class FundamentalSnapshot(BaseModel):
    """Quarterly or annual fundamental data snapshot for a security."""

    security_id: str = Field(..., description="Internal security identifier")
    period_end_date: datetime = Field(..., description="End of the reporting period")
    period_type: str = Field(..., description="Period type: Q1, Q2, Q3, Q4, FY")
    revenue: Decimal | None = Field(None, description="Total revenue")
    operating_income: Decimal | None = Field(None, description="Operating income")
    net_income: Decimal | None = Field(None, description="Net income")
    eps: Decimal | None = Field(None, description="Earnings per share (diluted preferred)")
    book_value_per_share: Decimal | None = Field(None, description="Tangible book value per share")
    debt_to_equity: Decimal | None = Field(None, description="Total debt / total equity ratio")
    current_ratio: Decimal | None = Field(None, description="Current assets / current liabilities")
    free_cash_flow: Decimal | None = Field(None, description="Free cash flow for the period")
    shares_outstanding: Decimal | None = Field(None, description="Diluted shares outstanding")
    custom_factors: dict[str, Decimal] = Field(
        default_factory=dict, description="Additional factor data"
    )
    currency: str = Field(default="USD", description="Currency of numeric fields (ISO 4217)")
    quality: DataQuality = Field(..., description="Data provenance and source metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer(
        "revenue",
        "operating_income",
        "net_income",
        "eps",
        "book_value_per_share",
        "debt_to_equity",
        "current_ratio",
        "free_cash_flow",
        "shares_outstanding",
    )
    def serialize_optional_decimal_fields(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    @field_serializer("custom_factors")
    def serialize_custom_factors(self, value: dict[str, Decimal]) -> dict[str, str]:
        return {key: str(item) for key, item in value.items()}


class BenchmarkConstituent(BaseModel):
    """Member of a benchmark index at a given rebalance date."""

    benchmark_id: str = Field(..., description="Benchmark identifier (e.g., 'SPY', 'MSCI_USA')")
    security_id: str = Field(..., description="Internal security identifier")
    effective_date: datetime = Field(..., description="Date this constituent became effective")
    weight: Decimal = Field(..., description="Weight in benchmark (0.0 to 1.0)")
    shares_held: Decimal | None = Field(
        None, description="Shares included in benchmark calculation"
    )
    quality: DataQuality = Field(..., description="Data provenance metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("weight", "shares_held")
    def serialize_decimal_fields(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class Holding(BaseModel):
    """Current or historical holding in a portfolio."""

    portfolio_id: str = Field(..., description="Unique portfolio identifier")
    security_id: str = Field(..., description="Internal security identifier")
    as_of_date: datetime = Field(..., description="Date as of which the holding is recorded")
    shares: Decimal = Field(..., description="Number of shares held")
    book_cost_per_share: Decimal | None = Field(None, description="Average cost basis per share")
    current_price: Decimal = Field(..., description="Current market price per share")
    market_value: Decimal = Field(..., description="Current market value = shares * current_price")
    target_weight: Decimal | None = Field(
        None, description="Target weight in portfolio (0.0 to 1.0)"
    )
    quality: DataQuality = Field(..., description="Data provenance metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer(
        "shares", "book_cost_per_share", "current_price", "market_value", "target_weight"
    )
    def serialize_decimal_fields(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class TargetWeight(BaseModel):
    """Target portfolio weight for a security from the construction engine."""

    portfolio_id: str = Field(..., description="Unique portfolio identifier")
    security_id: str = Field(..., description="Internal security identifier")
    target_date: datetime = Field(..., description="Date as of which this target is effective")
    target_weight: Decimal = Field(..., description="Target weight in portfolio (0.0 to 1.0)")
    min_weight: Decimal | None = Field(None, description="Minimum allowed weight")
    max_weight: Decimal | None = Field(None, description="Maximum allowed weight")
    reason: str | None = Field(
        None, description="Justification for the weight (e.g., 'score_rank_5')"
    )
    quality: DataQuality = Field(..., description="Data provenance metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("target_weight", "min_weight", "max_weight")
    def serialize_decimal_fields(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class TradeProposal(BaseModel):
    """Proposed trade from rebalance engine."""

    portfolio_id: str = Field(..., description="Unique portfolio identifier")
    trade_date: datetime = Field(..., description="Proposed trade execution date")
    security_id: str = Field(..., description="Internal security identifier")
    side: str = Field(..., description="Trade side: buy or sell")
    shares: Decimal = Field(..., description="Number of shares to trade")
    price_estimate: Decimal = Field(..., description="Estimated execution price")
    estimated_value: Decimal = Field(
        ..., description="Estimated trade value = shares * price_estimate"
    )
    reason: str | None = Field(None, description="Justification (e.g., 'rebalance_to_target')")
    priority: int = Field(
        default=0, description="Execution priority (0=critical, higher=lower priority)"
    )
    quality: DataQuality = Field(..., description="Data provenance metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("shares", "price_estimate", "estimated_value")
    def serialize_decimal_fields(self, value: Decimal) -> str:
        return str(value)


class PerformanceSnapshot(BaseModel):
    """Portfolio performance metrics at a point in time."""

    portfolio_id: str = Field(..., description="Unique portfolio identifier")
    as_of_date: datetime = Field(..., description="Date of performance measurement")
    period_start_date: datetime = Field(..., description="Start of the measurement period")
    total_return: Decimal = Field(
        ..., description="Total return over period (decimal, e.g., 0.05 for 5%)"
    )
    annualized_return: Decimal | None = Field(
        None, description="Annualized return if period > 1 year"
    )
    volatility: Decimal = Field(..., description="Annualized volatility (standard deviation)")
    sharpe_ratio: Decimal | None = Field(None, description="Excess return / volatility")
    max_drawdown: Decimal = Field(..., description="Maximum peak-to-trough drawdown over period")
    benchmark_id: str | None = Field(None, description="Benchmark used for attribution")
    benchmark_return: Decimal | None = Field(None, description="Benchmark total return over period")
    excess_return: Decimal | None = Field(None, description="Portfolio return - benchmark return")
    total_value: Decimal = Field(..., description="Current portfolio market value")
    time_weighted_return: Decimal | None = Field(None, description="TWR if cash flows occurred")
    money_weighted_return: Decimal | None = Field(None, description="MWR if cash flows occurred")
    currency: str = Field(default="USD", description="Currency of numeric fields (ISO 4217)")
    quality: DataQuality = Field(..., description="Data provenance and calculation metadata")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer(
        "total_return",
        "annualized_return",
        "volatility",
        "sharpe_ratio",
        "max_drawdown",
        "benchmark_return",
        "excess_return",
        "total_value",
        "time_weighted_return",
        "money_weighted_return",
    )
    def serialize_decimal_fields(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)
