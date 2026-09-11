"""Pydantic data models for all Alpha Holdings entities."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, ClassVar, Optional, Union
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MacroRegimeType(str, Enum):
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"


class SupplyChainTier(str, Enum):
    TIER_1_DEMAND_DRIVER = "tier_1_demand_driver"
    TIER_2_DIRECT_ENABLER = "tier_2_direct_enabler"
    TIER_3_PICKS_AND_SHOVELS = "tier_3_picks_and_shovels"


class MarketCapCategory(str, Enum):
    SMALL = "small"
    MID = "mid"
    LARGE = "large"


class ValuationLevel(str, Enum):
    CHEAP = "cheap"
    FAIR = "fair"
    EXPENSIVE = "expensive"


class OpportunityType(str, Enum):
    ON_SALE = "on_sale"
    STABILIZED = "stabilized"
    RECOVERING = "recovering"
    CAUTION = "caution"
    AVOID = "avoid"
    NO_SIGNAL = "no_signal"
    UNAVAILABLE = "unavailable"


class MarketDataStatus(str, Enum):
    AVAILABLE = "available"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class PriceBasis(str, Enum):
    ADJUSTED_CLOSE = "adjusted_close"


class ThesisStatus(str, Enum):
    STRENGTHENED = "strengthened"
    UNCHANGED = "unchanged"
    WEAKENED = "weakened"
    INVALIDATED = "invalidated"


class RebalanceAction(str, Enum):
    REDUCE_THEME = "reduce_theme"
    ROTATE_HOLDING = "rotate_holding"
    TRIM_CONCENTRATION = "trim_concentration"
    ADD_THEME = "add_theme"


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskAppetite(str, Enum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class TimeHorizon(str, Enum):
    SHORT = "3-5yr"
    MEDIUM = "5-10yr"
    LONG = "10yr+"


class EntryMethod(str, Enum):
    LUMP_SUM = "lump_sum"
    DCA = "dca"
    WAIT = "wait"


class InstrumentType(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    FUND = "fund"
    BOND = "bond"
    COMMODITY = "commodity"
    CASH = "cash"


class PortfolioConstructionMode(str, Enum):
    """Policy controlling which instrument types a fresh allocation may fund."""

    AUTOMATIC = "automatic"
    ETF_ONLY = "etf_only"


class PortfolioSleeve(str, Enum):
    THEMATIC = "thematic"
    CORE = "core"
    DEFENSIVE = "defensive"
    CASH = "cash"


class SnapshotSourceFormat(str, Enum):
    VERSIONED = "versioned"
    LEGACY = "legacy"


class ETFRecommendationType(str, Enum):
    ETF_SUFFICIENT = "etf_sufficient"
    STOCKS_BETTER = "stocks_better"
    NO_GOOD_ETF = "no_good_etf"


class DependencyRelationship(str, Enum):
    DRIVES_DEMAND_FOR = "drives_demand_for"
    AMPLIFIED_BY = "amplified_by"
    SHARES_INFRASTRUCTURE = "shares_infrastructure"


# ---------------------------------------------------------------------------
# Macro signals
# ---------------------------------------------------------------------------

class MacroSignal(BaseModel):
    model_config = {"populate_by_name": True}

    headline: str
    summary: str
    source: str
    signal_date: date | None = Field(default=None, alias="date")
    tags: list[str] = Field(default_factory=list)
    url: str | None = None

    @field_validator("signal_date", mode="before")
    @classmethod
    def _coerce_date(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v


class MacroRegime(BaseModel):
    regime: MacroRegimeType
    confidence: int = Field(ge=1, le=10)
    drivers: list[str]


# ---------------------------------------------------------------------------
# Theme discovery
# ---------------------------------------------------------------------------

class Company(BaseModel):
    ticker: str
    exchange_suffix: Optional[str] = None
    issuer_id: Optional[str] = None
    name: str
    role_in_theme: str
    rationale: str
    market_cap_category: MarketCapCategory
    supply_chain_tier: SupplyChainTier
    sector: str

    @property
    def full_ticker(self) -> str:
        if self.exchange_suffix:
            return f"{self.ticker}.{self.exchange_suffix}"
        return self.ticker

    @property
    def exposure_keys(self) -> frozenset[str]:
        """Conservative aliases used to aggregate effective issuer exposure."""
        aliases: set[str] = set()
        if self.issuer_id and self.issuer_id.strip():
            aliases.add(f"issuer:{self.issuer_id.strip().casefold()}")
        ignored = {
            "adr", "ads", "ag", "class", "co", "company", "corp",
            "corporation", "depositary", "group", "holding", "holdings",
            "inc", "limited", "llc", "lp", "ltd", "nv", "ordinary",
            "plc", "receipt", "sa", "se", "shares", "spa", "sponsored",
            "the",
        }
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", self.name.casefold())
            if token not in ignored and len(token) > 1
        ]
        canonical_name = ":".join(tokens)
        if canonical_name:
            aliases.add(f"name:{canonical_name}")
        if not aliases:
            aliases.add(f"ticker:{self.full_ticker.upper()}")
        return frozenset(aliases)

class SubTheme(BaseModel):
    name: str
    description: str
    companies: list[Company] = Field(default_factory=list)


class ThemeThesis(BaseModel):
    name: str
    thesis_summary: str
    why_now: str
    bull_case: str
    bear_case: str
    confidence_score: int = Field(ge=1, le=10)
    time_horizon: str = "3-5 years"
    sub_themes: list[SubTheme] = Field(default_factory=list)
    discovered_at: Optional[datetime] = None

    @property
    def all_companies(self) -> list[Company]:
        return [c for st in self.sub_themes for c in st.companies]

    @property
    def tier_1(self) -> list[Company]:
        return [c for c in self.all_companies if c.supply_chain_tier == SupplyChainTier.TIER_1_DEMAND_DRIVER]

    @property
    def tier_2(self) -> list[Company]:
        return [c for c in self.all_companies if c.supply_chain_tier == SupplyChainTier.TIER_2_DIRECT_ENABLER]

    @property
    def tier_3(self) -> list[Company]:
        return [c for c in self.all_companies if c.supply_chain_tier == SupplyChainTier.TIER_3_PICKS_AND_SHOVELS]


class ThemeDependency(BaseModel):
    source_theme: str
    target_theme: str
    relationship: DependencyRelationship
    explanation: str


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------

class Fundamentals(BaseModel):
    ticker: str
    provider_symbol: Optional[str] = None
    name: Optional[str] = None
    quote_type: Optional[str] = None
    source: Optional[str] = None
    sector: Optional[str] = None
    market_cap: Optional[float] = None
    revenue_growth_cagr: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices(
            "revenue_growth_cagr",
            "revenue_growth_3yr_cagr",
        ),
        description="Annualized revenue growth over the reported observation span.",
    )
    revenue_growth_period_years: Optional[float] = Field(default=None, gt=0)
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    free_cash_flow: Optional[float] = None
    fcf_yield: Optional[float] = None
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    peg_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    roe: Optional[float] = None
    rd_pct_revenue: Optional[float] = None
    earnings_surprises: list[float] = Field(
        default_factory=list,
        description="Last 4 quarters earnings surprise pct.",
    )
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    current_price: Optional[float] = None
    price_basis: Optional[PriceBasis] = Field(
        default=None,
        description="Provider-defined basis for current_price, such as adjusted_close.",
    )
    price_as_of: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the price itself when distinct from retrieval time.",
    )
    drawdown_from_peak: Optional[float] = None
    ev_to_ebitda: Optional[float] = None
    avg_daily_volume: Optional[float] = None
    # Technical indicators
    return_2yr: Optional[float] = Field(default=None, description="2-year price return %")
    pct_from_200dma: Optional[float] = Field(default=None, description="% distance from 200-day moving average")
    forward_to_trailing_pe_ratio: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices(
            "forward_to_trailing_pe_ratio",
            "pe_revision_ratio",
        ),
        description="Forward P/E divided by trailing P/E; not an earnings-revision measure.",
    )
    forward_pe_vs_price_history_proxy: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices(
            "forward_pe_vs_price_history_proxy",
            "pe_vs_own_history",
        ),
        description=(
            "Forward P/E as a percentage of average historical price divided by "
            "current EPS; not historical P/E."
        ),
    )
    fetched_at: Optional[datetime] = None

    @property
    def revenue_growth_3yr_cagr(self) -> Optional[float]:
        """Compatibility accessor for snapshots written before the metric rename."""
        return self.revenue_growth_cagr

    @property
    def pe_revision_ratio(self) -> Optional[float]:
        """Compatibility accessor for snapshots written before the metric rename."""
        return self.forward_to_trailing_pe_ratio

    @property
    def pe_vs_own_history(self) -> Optional[float]:
        """Compatibility accessor for snapshots written before the metric rename."""
        return self.forward_pe_vs_price_history_proxy


class FundamentalsResult(BaseModel):
    """A timestamped market-data observation, including failure outcomes."""

    ticker: str
    status: MarketDataStatus
    data: Optional[Fundamentals] = None
    observed_at: datetime
    as_of: Optional[datetime] = None
    from_cache: bool = False
    age: Optional[float] = Field(
        default=None,
        ge=0,
        description="Age of the underlying observation in seconds.",
    )
    reason: Optional[str] = None

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("ticker must not be empty")
        return value

    @model_validator(mode="after")
    def _validate_outcome(self):
        has_observation = self.data is not None and self.as_of is not None and self.age is not None
        if self.status in (MarketDataStatus.AVAILABLE, MarketDataStatus.STALE):
            if not has_observation:
                raise ValueError(f"{self.status.value} market data requires data, as_of, and age")
        elif self.data is not None or self.as_of is not None or self.age is not None:
            raise ValueError(f"{self.status.value} market data cannot contain an observation")
        if self.status is MarketDataStatus.STALE and not self.from_cache:
            raise ValueError("stale market data must come from cache")
        if self.status in (MarketDataStatus.UNAVAILABLE, MarketDataStatus.INVALID) and self.from_cache:
            raise ValueError(f"{self.status.value} market data cannot come from cache")
        return self


# ---------------------------------------------------------------------------
# Scoring & valuation
# ---------------------------------------------------------------------------

class ValuationContext(BaseModel):
    level: ValuationLevel
    forward_pe_vs_sp500: Optional[str] = None
    summary: str


class ThemeScore(BaseModel):
    ticker: str
    fundamental_score: float = Field(ge=0, le=100)
    thesis_alignment_score: float = Field(ge=0, le=100)
    pricing_gap_score: float = Field(ge=0, le=100)
    revenue_exposure_score: Optional[float] = Field(default=None, ge=0, le=100)
    composite_score: float = Field(ge=0, le=100)
    valuation: Optional[ValuationContext] = None
    entry_method: EntryMethod = EntryMethod.DCA
    alignment_reasoning: Optional[str] = None
    pricing_gap_reasoning: Optional[str] = None
    revenue_exposure_reasoning: Optional[str] = None
    score_as_of: Optional[datetime] = None
    evidence_sources: list[str] = Field(default_factory=list)
    scoring_provider: Optional[str] = None
    scoring_model: Optional[str] = None

    @property
    def has_complete_evidence(self) -> bool:
        """Whether the score is eligible for allocation and publication."""
        return bool(
            self.revenue_exposure_score is not None
            and self.score_as_of is not None
            and self.evidence_sources
            and self.scoring_provider
            and self.scoring_model
            and self.alignment_reasoning
            and self.pricing_gap_reasoning
            and self.revenue_exposure_reasoning
        )


class OpportunitySignal(BaseModel):
    ticker: str
    signal_type: OpportunityType
    thesis_confidence: int
    fundamental_health: str
    current_price: Optional[float] = None
    drawdown_pct: Optional[float] = None
    recommended_action: str
    theme_name: Optional[str] = None
    supply_chain_tier: Optional[str] = None
    volume_vs_avg: Optional[float] = Field(default=None, description="Current volume / 3-month avg. >1.5 = strong confirmation.")


# ---------------------------------------------------------------------------
# ETF
# ---------------------------------------------------------------------------

class ETFMarketEvidence(BaseModel):
    """Provider evidence required before an ETF can receive portfolio weight."""

    ticker: str
    provider_symbol: Optional[str] = None
    name: Optional[str] = None
    quote_type: Optional[str] = None
    average_daily_volume: Optional[float] = None
    total_assets: Optional[float] = None
    expense_ratio: Optional[float] = None
    adjusted_close: Optional[float] = None
    price_as_of: Optional[datetime] = None
    source: Optional[str] = None
    holdings: dict[str, float] = Field(default_factory=dict)

    def investability_reasons(self, requested_ticker: str) -> list[str]:
        """Return deterministic policy failures for this provider observation."""
        import math
        from alpha_holdings.config import (
            MAX_ETF_EXPENSE_RATIO,
            MIN_ETF_AUM,
            MIN_ETF_AVG_DAILY_VOLUME,
        )

        reasons: list[str] = []
        requested = requested_ticker.strip().upper()
        if self.ticker.strip().upper() != requested:
            reasons.append("evidence ticker does not match the requested ticker")
        if (self.provider_symbol or "").strip().upper() != requested:
            reasons.append("provider symbol does not match the requested ticker")
        if (self.quote_type or "").strip().upper() != "ETF":
            reasons.append("provider quote type is not ETF")
        if (
            self.average_daily_volume is None
            or not math.isfinite(self.average_daily_volume)
            or self.average_daily_volume < MIN_ETF_AVG_DAILY_VOLUME
        ):
            reasons.append("average daily volume is below policy minimum")
        if (
            self.total_assets is None
            or not math.isfinite(self.total_assets)
            or self.total_assets < MIN_ETF_AUM
        ):
            reasons.append("assets are below policy minimum")
        if (
            self.expense_ratio is None
            or not math.isfinite(self.expense_ratio)
            or self.expense_ratio < 0
            or self.expense_ratio > MAX_ETF_EXPENSE_RATIO
        ):
            reasons.append("expense ratio is unavailable or above policy maximum")
        if (
            self.adjusted_close is None
            or not math.isfinite(self.adjusted_close)
            or self.adjusted_close <= 0
        ):
            reasons.append("adjusted entry price is unavailable")
        if self.price_as_of is None:
            reasons.append("adjusted entry price is missing its as-of timestamp")
        if not self.source or not self.source.strip():
            reasons.append("provider source is unavailable")
        if not self.holdings:
            reasons.append("holdings data is unavailable")
        elif any(
            not math.isfinite(weight) or weight <= 0
            for weight in self.holdings.values()
        ):
            reasons.append("holdings weights must be finite and positive")
        else:
            total_weight = sum(self.holdings.values())
            if not math.isfinite(total_weight) or total_weight > 100.01:
                reasons.append("holdings weights exceed 100%")
        return reasons


class ETFCandidateEvaluation(BaseModel):
    ticker: str
    is_valid: bool
    rejection_reasons: list[str] = Field(default_factory=list)
    evidence: Optional[ETFMarketEvidence] = None
    holdings_coverage_pct: Optional[float] = None
    unknown_weight_pct: Optional[float] = None
    theme_coverage_pct: Optional[float] = None

    @model_validator(mode="after")
    def _validate_outcome(self):
        if self.is_valid:
            if self.evidence is None:
                raise ValueError("valid ETF evaluation requires evidence")
            if self.rejection_reasons:
                raise ValueError("valid ETF evaluation cannot have rejection reasons")
            reasons = self.evidence.investability_reasons(self.ticker)
            if reasons:
                raise ValueError("valid ETF evaluation contains invalid evidence")
        elif not self.rejection_reasons:
            raise ValueError("invalid ETF evaluation requires rejection reasons")
        return self

    def to_market_data(
        self,
        *,
        observed_at: datetime | None = None,
    ) -> FundamentalsResult:
        """Project a validated candidate into allocation market data."""
        if not self.is_valid or self.evidence is None:
            raise ValueError("only validated ETF evidence can become market data")
        evidence = self.evidence
        assert evidence.adjusted_close is not None
        assert evidence.price_as_of is not None
        retrieval_time = observed_at or datetime.now(UTC)
        retrieval_utc = (
            retrieval_time.replace(tzinfo=UTC)
            if retrieval_time.tzinfo is None
            else retrieval_time.astimezone(UTC)
        )
        price_utc = (
            evidence.price_as_of.replace(tzinfo=UTC)
            if evidence.price_as_of.tzinfo is None
            else evidence.price_as_of.astimezone(UTC)
        )
        return FundamentalsResult(
            ticker=evidence.ticker,
            status=MarketDataStatus.AVAILABLE,
            data=Fundamentals(
                ticker=evidence.ticker,
                provider_symbol=evidence.provider_symbol,
                name=evidence.name,
                quote_type=evidence.quote_type,
                source=evidence.source,
                current_price=evidence.adjusted_close,
                price_basis=PriceBasis.ADJUSTED_CLOSE,
                price_as_of=price_utc,
                avg_daily_volume=evidence.average_daily_volume,
                fetched_at=retrieval_utc,
            ),
            observed_at=retrieval_utc,
            as_of=price_utc,
            age=max((retrieval_utc - price_utc).total_seconds(), 0),
        )


class ETFRecommendation(BaseModel):
    theme_name: str
    etf_ticker: Optional[str] = None
    etf_name: Optional[str] = None
    expense_ratio: Optional[float] = None
    aum: Optional[float] = None
    overlap_pct: Optional[float] = None
    holdings: dict[str, float] = Field(default_factory=dict)
    holdings_coverage_pct: Optional[float] = None
    unknown_weight_pct: Optional[float] = None
    selected_evidence: Optional[ETFMarketEvidence] = None
    candidate_evaluations: list[ETFCandidateEvaluation] = Field(default_factory=list)
    recommendation: ETFRecommendationType
    reasoning: str

    @property
    def adjusted_entry_price(self) -> Optional[float]:
        return self.selected_evidence.adjusted_close if self.selected_evidence else None

    @property
    def price_as_of(self) -> Optional[datetime]:
        return self.selected_evidence.price_as_of if self.selected_evidence else None

    @property
    def price_source(self) -> Optional[str]:
        return self.selected_evidence.source if self.selected_evidence else None

    @model_validator(mode="after")
    def _validate_funded_selection(self):
        if self.recommendation is not ETFRecommendationType.ETF_SUFFICIENT:
            return self
        ticker = (self.etf_ticker or "").strip().upper()
        if not ticker or self.selected_evidence is None:
            raise ValueError("ETF-sufficient recommendation requires selected evidence")
        if self.selected_evidence.ticker.strip().upper() != ticker:
            raise ValueError("selected ETF evidence must match the recommendation ticker")
        if not any(
            evaluation.ticker.strip().upper() == ticker
            and evaluation.is_valid
            and evaluation.evidence == self.selected_evidence
            for evaluation in self.candidate_evaluations
        ):
            raise ValueError("selected ETF evidence must match a valid candidate audit")
        return self

    def to_market_data(
        self,
        *,
        observed_at: datetime | None = None,
    ) -> FundamentalsResult:
        """Project the audited selected vehicle into allocation market data."""
        for evaluation in self.candidate_evaluations:
            if evaluation.is_valid and evaluation.evidence == self.selected_evidence:
                return evaluation.to_market_data(observed_at=observed_at)
        raise ValueError("recommendation has no validated selected evidence")


# ---------------------------------------------------------------------------
# Risk & allocation
# ---------------------------------------------------------------------------

class RiskProfile(BaseModel):
    appetite: RiskAppetite
    time_horizon: TimeHorizon


class AllocationEntry(BaseModel):
    theme: str
    vehicle: str = Field(
        default="",
        exclude=True,
        description="Legacy display field; explicit tickers are authoritative.",
    )
    tickers: list[str] = Field(default_factory=list)
    vehicle_type: str = "etf"
    pct_allocation: float
    entry_method: EntryMethod
    rationale: str
    entry_prices: dict[str, float] = Field(
        default_factory=dict,
        description="Ticker → price at time of allocation, for sell discipline tracking.",
    )

    @model_validator(mode="after")
    def _populate_tickers(self):
        vehicle_tickers = [
            ticker.strip().upper()
            for ticker in self.vehicle.split(",")
            if ticker.strip()
        ]
        supplied_tickers = [
            ticker.strip().upper() for ticker in self.tickers if ticker.strip()
        ]
        if supplied_tickers and vehicle_tickers and supplied_tickers != vehicle_tickers:
            raise ValueError("allocation entry tickers must match its vehicle")
        self.tickers = supplied_tickers or vehicle_tickers
        return self


class InstrumentPosition(BaseModel):
    """A concrete portfolio position with an explicit percentage-point weight."""

    ticker: str
    instrument_type: InstrumentType
    sleeve: PortfolioSleeve
    weight_pct: float = Field(ge=0, le=100)
    capital_amount: Optional[float] = Field(default=None, ge=0)
    currency: str
    entry_price: float = Field(gt=0)
    price_timestamp: datetime

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("ticker must not be empty")
        return value

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a three-letter ISO code")
        return value

class InstrumentMetadata(BaseModel):
    ticker: str
    instrument_type: InstrumentType
    currency: str
    name: Optional[str] = None
    exchange: Optional[str] = None

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("ticker must not be empty")
        return value

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a three-letter ISO code")
        return value


class InstrumentPrice(BaseModel):
    ticker: str
    price: float = Field(gt=0)
    currency: str
    observed_at: datetime
    source: Optional[str] = None

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("ticker must not be empty")
        return value

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a three-letter ISO code")
        return value


class PortfolioAllocation(BaseModel):
    ROUNDING_TOLERANCE_PCT: ClassVar[float] = 0.1

    risk_profile: RiskProfile
    macro_regime: MacroRegime
    portfolio_construction_mode: PortfolioConstructionMode = Field(
        default=PortfolioConstructionMode.AUTOMATIC,
    )
    positions: list[InstrumentPosition] = Field(default_factory=list)
    entries: list[AllocationEntry] = Field(default_factory=list)
    core_pct: float = Field(
        ge=0,
        le=100,
        description="Percentage allocated to broad market core (SPY/VT).",
    )
    defensive_pct: float = Field(
        default=0.0,
        ge=0,
        le=100,
        description="Percentage in defensive vehicles (bear regime only).",
    )
    cash_pct: float = Field(
        default=0.0,
        ge=0,
        le=100,
        description="Percentage retained as cash when investable capacity is unavailable.",
    )
    effective_allocation_modifier: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="Deterministic regime modifier applied by the allocation policy.",
    )
    residual_reason: Optional[str] = None
    unfunded_themes: list[str] = Field(
        default_factory=list,
        description="Themes that could not receive a position under the selected mode.",
    )
    coverage_failure: bool = False
    coverage_failure_reason: Optional[str] = None
    capital: Optional[float] = Field(
        default=None,
        description="Total capital to invest, if provided via --capital.",
    )
    generated_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _validate_position_weights(self):
        if self.portfolio_construction_mode is PortfolioConstructionMode.ETF_ONLY:
            invalid = [
                position.ticker
                for position in self.positions
                if position.instrument_type not in {
                    InstrumentType.ETF,
                    InstrumentType.CASH,
                }
            ]
            if invalid:
                raise ValueError(
                    "ETF-only allocations cannot contain non-ETF positions: "
                    + ", ".join(invalid)
                )
        if not self.positions:
            return self
        tickers = [position.ticker for position in self.positions]
        if len(tickers) != len(set(tickers)):
            raise ValueError("allocation positions cannot contain duplicate tickers")
        total = sum(position.weight_pct for position in self.positions)
        if abs(total - 100.0) > self.ROUNDING_TOLERANCE_PCT + 1e-9:
            raise ValueError("allocation positions must total 100% within 0.1 percentage points")
        sleeve_totals = {
            sleeve: sum(
                position.weight_pct
                for position in self.positions
                if position.sleeve is sleeve
            )
            for sleeve in PortfolioSleeve
        }
        declared = {
            PortfolioSleeve.CORE: self.core_pct,
            PortfolioSleeve.DEFENSIVE: self.defensive_pct,
            PortfolioSleeve.CASH: self.cash_pct,
        }
        for sleeve, declared_pct in declared.items():
            if (
                abs(sleeve_totals[sleeve] - declared_pct)
                > self.ROUNDING_TOLERANCE_PCT + 1e-9
            ):
                raise ValueError(
                    f"{sleeve.value} percentage does not match its positions"
                )
        thematic_pct = sleeve_totals[PortfolioSleeve.THEMATIC]
        entry_pct = sum(entry.pct_allocation for entry in self.entries)
        if abs(thematic_pct - entry_pct) > self.ROUNDING_TOLERANCE_PCT + 1e-9:
            raise ValueError("legacy entries must match thematic positions")
        position_tickers = {
            position.ticker
            for position in self.positions
            if position.sleeve is PortfolioSleeve.THEMATIC
        }
        entry_tickers = {
            ticker
            for entry in self.entries
            for ticker in entry.tickers
        }
        if position_tickers != entry_tickers:
            raise ValueError("legacy entry vehicles must match thematic positions")
        if self.capital is not None:
            for position in self.positions:
                expected_cents = round(
                    self.capital * position.weight_pct,
                )
                actual_cents = round((position.capital_amount or 0) * 100)
                if (
                    position.capital_amount is None
                    or abs(actual_cents - expected_cents) > 1
                ):
                    raise ValueError(
                        f"{position.ticker} capital amount does not match its weight"
                    )
            allocated_cents = round(
                sum(position.capital_amount or 0 for position in self.positions) * 100
            )
            if allocated_cents != round(self.capital * 100):
                raise ValueError("position capital amounts must preserve total capital")
        return self


RUN_SNAPSHOT_SECTIONS = (
    "themes",
    "candidate_scores",
    "etf_recommendations",
    "instrument_metadata",
    "prices",
    "positions",
    "allocation",
    "model_configuration",
    "provenance",
)


class SnapshotCompleteness(BaseModel):
    source_format: SnapshotSourceFormat
    is_complete: bool
    available_sections: list[str] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_complete_state(self):
        available = self.available_sections
        missing = self.missing_sections
        if len(available) != len(set(available)) or len(missing) != len(set(missing)):
            raise ValueError("snapshot sections cannot contain duplicates")
        available_set = set(available)
        missing_set = set(missing)
        known = set(RUN_SNAPSHOT_SECTIONS)
        unknown = (available_set | missing_set) - known
        if unknown:
            raise ValueError("unknown snapshot sections: " + ", ".join(sorted(unknown)))
        overlap = available_set & missing_set
        if overlap:
            raise ValueError(
                "snapshot sections cannot be both available and missing: "
                + ", ".join(sorted(overlap))
            )
        if available_set | missing_set != known:
            raise ValueError("snapshot completeness must classify every section")
        if self.is_complete != (not missing):
            raise ValueError("is_complete must agree with the missing sections")
        return self


class RunSnapshot(BaseModel):
    """One coherent and independently loadable investment-research run."""

    SECTIONS: ClassVar[tuple[str, ...]] = RUN_SNAPSHOT_SECTIONS

    schema_version: int = Field(default=1, ge=1)
    run_id: str = Field(default="", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    portfolio_construction_mode: PortfolioConstructionMode | None = Field(
        default=None,
    )
    themes: list[ThemeThesis] = Field(default_factory=list)
    candidate_scores: dict[str, list[ThemeScore]] = Field(default_factory=dict)
    etf_recommendations: dict[str, ETFRecommendation] = Field(default_factory=dict)
    instrument_metadata: dict[str, InstrumentMetadata] = Field(default_factory=dict)
    prices: dict[str, InstrumentPrice] = Field(default_factory=dict)
    positions: list[InstrumentPosition] = Field(default_factory=list)
    allocation: PortfolioAllocation
    model_configuration: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    completeness: SnapshotCompleteness = Field(
        default_factory=lambda: SnapshotCompleteness(
            source_format=SnapshotSourceFormat.VERSIONED,
            is_complete=True,
            available_sections=list(RUN_SNAPSHOT_SECTIONS),
        )
    )

    @model_validator(mode="before")
    @classmethod
    def _assign_run_id(cls, data):
        if not isinstance(data, dict) or data.get("run_id"):
            return data
        created_at = data.get("created_at") or datetime.now(UTC)
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        data = dict(data)
        data["run_id"] = f"{created_at.strftime('%Y%m%dT%H%M%S%f')}-{uuid4().hex}"
        return data

    @model_validator(mode="after")
    def _validate_position_projection(self):
        if self.positions != self.allocation.positions:
            raise ValueError("snapshot positions must match allocation positions")
        allocation_mode = self.allocation.portfolio_construction_mode
        if self.portfolio_construction_mode is None:
            self.portfolio_construction_mode = allocation_mode
        elif self.portfolio_construction_mode is not allocation_mode:
            raise ValueError(
                "snapshot construction mode must match the allocation"
            )
        return self


# ---------------------------------------------------------------------------
# Course correction & rebalancing
# ---------------------------------------------------------------------------

class ThesisUpdate(BaseModel):
    theme_name: str
    status: ThesisStatus
    reason: str
    previous_confidence: int
    new_confidence: int
    companies_to_add: list[str] = Field(default_factory=list)
    companies_to_remove: list[str] = Field(default_factory=list)


class MonitoringEvent(BaseModel):
    """An immutable record of one course-correction run."""

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    source_run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updates: list[ThesisUpdate]
    signals: list[OpportunitySignal]
    pending_company_additions: dict[str, list[str]] = Field(default_factory=dict)


class RebalanceSignal(BaseModel):
    action: RebalanceAction
    from_asset: Optional[str] = None
    to_asset: Optional[str] = None
    reason: str
    urgency: Urgency
