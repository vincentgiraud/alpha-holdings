"""Pydantic data models for all Alpha Holdings entities."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional, Union

from pydantic import BaseModel, Field, field_validator


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
    BUY_THE_DIP = "buy_the_dip"
    CAUTION = "caution"
    AVOID = "avoid"


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
    allocation_modifier: float = Field(
        ge=0.0,
        le=1.0,
        description="Multiplier applied to thematic allocation. 1.0 = full, 0.5 = halved.",
    )


# ---------------------------------------------------------------------------
# Theme discovery
# ---------------------------------------------------------------------------

class Company(BaseModel):
    ticker: str
    exchange_suffix: Optional[str] = None
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
    name: Optional[str] = None
    sector: Optional[str] = None
    market_cap: Optional[float] = None
    revenue_growth_3yr_cagr: Optional[float] = None
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
    drawdown_from_peak: Optional[float] = None
    ev_to_ebitda: Optional[float] = None
    avg_daily_volume: Optional[float] = None
    # Technical indicators
    return_2yr: Optional[float] = Field(default=None, description="2-year price return %")
    pct_from_200dma: Optional[float] = Field(default=None, description="% distance from 200-day moving average")
    pe_revision_ratio: Optional[float] = Field(default=None, description="forward_pe / trailing_pe. >1 = estimates cut, <1 = estimates rising")
    fetched_at: Optional[datetime] = None


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
    composite_score: float = Field(ge=0, le=100)
    valuation: Optional[ValuationContext] = None
    entry_method: EntryMethod = EntryMethod.DCA
    alignment_reasoning: Optional[str] = None
    pricing_gap_reasoning: Optional[str] = None
    revenue_exposure_reasoning: Optional[str] = None


class OpportunitySignal(BaseModel):
    ticker: str
    signal_type: OpportunityType
    thesis_confidence: int
    fundamental_health: str
    current_price: Optional[float] = None
    drawdown_pct: Optional[float] = None
    recommended_action: str


# ---------------------------------------------------------------------------
# ETF
# ---------------------------------------------------------------------------

class ETFRecommendation(BaseModel):
    theme_name: str
    etf_ticker: Optional[str] = None
    etf_name: Optional[str] = None
    expense_ratio: Optional[float] = None
    aum: Optional[float] = None
    overlap_pct: Optional[float] = None
    recommendation: ETFRecommendationType
    reasoning: str


# ---------------------------------------------------------------------------
# Risk & allocation
# ---------------------------------------------------------------------------

class RiskProfile(BaseModel):
    appetite: RiskAppetite
    time_horizon: TimeHorizon


class AllocationEntry(BaseModel):
    theme: str
    vehicle: str
    vehicle_type: str = "etf"
    pct_allocation: float
    entry_method: EntryMethod
    rationale: str
    entry_prices: dict[str, float] = Field(
        default_factory=dict,
        description="Ticker → price at time of allocation, for sell discipline tracking.",
    )


class PortfolioAllocation(BaseModel):
    risk_profile: RiskProfile
    macro_regime: MacroRegime
    entries: list[AllocationEntry] = Field(default_factory=list)
    core_pct: float = Field(
        description="Percentage allocated to broad market core (SPY/VT).",
    )
    defensive_pct: float = Field(
        default=0.0,
        description="Percentage in defensive vehicles (bear regime only).",
    )
    capital: Optional[float] = Field(
        default=None,
        description="Total capital to invest, if provided via --capital.",
    )
    generated_at: Optional[datetime] = None


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


class RebalanceSignal(BaseModel):
    action: RebalanceAction
    from_asset: Optional[str] = None
    to_asset: Optional[str] = None
    reason: str
    urgency: Urgency
